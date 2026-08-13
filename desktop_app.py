"""
Sitechecker Desktop — Windows shell.

Запускает Flask на 127.0.0.1 и открывает окно (pywebview / WebView2).
При сбое WebView — fallback в системный браузер.
Данные: %APPDATA%\\Sitechecker\\
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser

# Режим десктопа до импорта config/app
os.environ["SITECHECKER_DESKTOP"] = "1"
os.environ.setdefault("HOST", "127.0.0.1")


def _find_free_port(preferred: int = 6001) -> int:
    for port in [preferred] + list(range(preferred + 1, preferred + 20)) + [0]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                return int(s.getsockname()[1])
        except OSError:
            continue
    return preferred


def _setup_logging():
    try:
        from config import LOG_FILE, DATA_DIR
        os.makedirs(DATA_DIR, exist_ok=True)
        log_path = LOG_FILE
    except Exception:
        log_path = os.path.join(
            os.environ.get("APPDATA", "."), "Sitechecker", "errors.log"
        )
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    # Также stderr — если собрано с console=True для отладки
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    return logging.getLogger("desktop"), log_path


def _show_error(msg: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg[:1500], "Sitechecker", 0x10)
    except Exception:
        print(msg, file=sys.stderr)


def _show_info(msg: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg[:1500], "Sitechecker", 0x40)
    except Exception:
        print(msg)


def _wait_http(url: str, timeout: float = 45.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _open_browser_and_wait(url: str, flask_thread: threading.Thread, logger) -> int:
    """Fallback: системный браузер + держим процесс, пока жив Flask-поток."""
    logger.warning("Opening system browser as UI fallback: %s", url)
    try:
        webbrowser.open(url)
    except Exception as e:
        logger.error("webbrowser.open failed: %s", e)
        _show_error(
            f"Не удалось открыть интерфейс.\n\n"
            f"Откройте вручную в браузере:\n{url}\n\nОшибка: {e}"
        )
        return 1

    _show_info(
        "Окно приложения недоступно (WebView2).\n\n"
        f"Интерфейс открыт в браузере:\n{url}\n\n"
        "Закройте это сообщение — сервер продолжит работу.\n"
        "Чтобы выйти, завершите Sitechecker в диспетчере задач\n"
        "или закройте окно консоли (если есть)."
    )
    try:
        while flask_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    return 0


class _DesktopApi:
    """JS bridge: сохранение отчётов/docx через нативный диалог + папки AppData."""

    def __init__(self, app_origin: str, data_dir: str, logger):
        self.app_origin = (app_origin or "").rstrip("/")
        self.data_dir = data_dir
        self.logger = logger
        self._window = None

    def set_window(self, window) -> None:
        self._window = window

    def _is_local_url(self, abs_url: str) -> bool:
        try:
            from urllib.parse import urlparse
            p = urlparse(abs_url)
            host = (p.hostname or "").lower()
            return host in ("127.0.0.1", "localhost")
        except Exception:
            return False

    def _filename_from_headers(self, headers, fallback: str) -> str:
        import re
        cd = ""
        try:
            cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
        except Exception:
            cd = ""
        if cd:
            # filename*=UTF-8''name.docx  OR  filename="name.docx"
            m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", cd, re.I)
            if m:
                from urllib.parse import unquote
                return unquote(m.group(1).strip().strip('"'))
            m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I)
            if m:
                return m.group(1).strip()
            m = re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
            if m:
                return m.group(1).strip().strip('"')
        return fallback or "download.bin"

    def _guess_name_from_url(self, path: str) -> str:
        p = (path or "").lower()
        if p.endswith("/xlsx") or "/download/xlsx" in p:
            return "pd_check.xlsx"
        if p.endswith("/csv") or "/download/csv" in p:
            return "pd_check.csv"
        if "/export/" in p or p.endswith("/export"):
            return "export.xlsx"
        if "/recommend/download/" in p or p.endswith(".docx") or "/doc/" in p:
            return "recommendations.docx"
        if "/screenshots/" in p:
            return "screenshot.png"
        return "download.bin"

    def download_file(self, url: str, suggested_name: str = "") -> dict:
        """
        Скачивает локальный URL приложения и показывает «Сохранить как…».
        После успеха открывает Проводник с выделенным файлом.
        """
        import urllib.request
        from urllib.parse import urljoin, urlparse

        try:
            import webview
        except Exception as e:
            return {"ok": False, "error": f"webview: {e}"}

        if not url or not self._window:
            return {"ok": False, "error": "Окно не готово"}

        try:
            if url.startswith("http://") or url.startswith("https://"):
                abs_url = url
            else:
                abs_url = urljoin(self.app_origin + "/", str(url).lstrip("/"))

            if not self._is_local_url(abs_url):
                return {"ok": False, "error": "Разрешены только локальные файлы приложения"}

            req = urllib.request.Request(abs_url, headers={"User-Agent": "SitecheckerDesktop/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
                status = getattr(resp, "status", 200) or 200
                if status >= 400:
                    return {"ok": False, "error": f"HTTP {status}"}
                ctype = (resp.headers.get("Content-Type") or "").lower()
                # JSON-ошибка вместо файла
                if "application/json" in ctype and len(data) < 4096:
                    try:
                        import json as _json
                        err = _json.loads(data.decode("utf-8", errors="replace"))
                        if isinstance(err, dict) and err.get("error"):
                            return {"ok": False, "error": str(err["error"])}
                    except Exception:
                        pass
                name = self._filename_from_headers(
                    resp.headers,
                    suggested_name or self._guess_name_from_url(urlparse(abs_url).path),
                )

            # фильтр по расширению
            ext = os.path.splitext(name)[1].lower()
            file_types = ("Все файлы (*.*)",)
            if ext == ".xlsx":
                file_types = ("Excel (*.xlsx)", "Все файлы (*.*)")
            elif ext == ".csv":
                file_types = ("CSV (*.csv)", "Все файлы (*.*)")
            elif ext == ".docx":
                file_types = ("Word (*.docx)", "Все файлы (*.*)")
            elif ext == ".png":
                file_types = ("PNG (*.png)", "Все файлы (*.*)")

            downloads = os.path.join(os.path.expanduser("~"), "Downloads")
            if not os.path.isdir(downloads):
                downloads = self.data_dir

            result = self._window.create_file_dialog(
                webview.FileDialog.SAVE,
                directory=downloads,
                save_filename=name,
                file_types=file_types,
            )
            if not result:
                return {"ok": False, "cancelled": True}

            path = result[0] if isinstance(result, (list, tuple)) else str(result)
            # если пользователь не указал расширение — добавим
            if ext and not os.path.splitext(path)[1]:
                path = path + ext

            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)

            self.logger.info("Saved download to %s (%d bytes)", path, len(data))
            self.reveal_file(path)
            return {"ok": True, "path": path}
        except Exception as e:
            self.logger.error("download_file failed: %s\n%s", e, traceback.format_exc())
            return {"ok": False, "error": str(e)}

    def open_folder(self, kind: str = "reports") -> dict:
        """Открыть папку data: reports | recommendations | data | screenshots."""
        try:
            from config import REPORT_DIR, REC_DIR, DATA_DIR, SCREENSHOTS_DIR
            mapping = {
                "reports": REPORT_DIR,
                "recommendations": REC_DIR,
                "screenshots": SCREENSHOTS_DIR,
                "data": DATA_DIR,
            }
            path = mapping.get((kind or "data").strip().lower(), DATA_DIR)
            os.makedirs(path, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
            return {"ok": True, "path": path}
        except Exception as e:
            self.logger.error("open_folder failed: %s", e)
            return {"ok": False, "error": str(e)}

    def reveal_file(self, path: str) -> dict:
        """Показать файл в Проводнике (с выделением)."""
        try:
            if not path or not os.path.exists(path):
                return {"ok": False, "error": "Файл не найден"}
            if sys.platform == "win32":
                import subprocess
                # explorer /select,"C:\path\file.ext"
                subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
            return {"ok": True, "path": path}
        except Exception as e:
            self.logger.error("reveal_file failed: %s", e)
            return {"ok": False, "error": str(e)}


def _start_webview(url: str, data_dir: str, logger) -> bool:
    """
    Пытается открыть pywebview. Возвращает True, если окно отработало до закрытия.
    False — нужно fallback в браузер.
    """
    # WebView2 user data MUST be writable (not under Program Files / locked dirs)
    wv_data = os.path.join(data_dir, "webview2")
    os.makedirs(wv_data, exist_ok=True)
    os.environ["WEBVIEW2_USER_DATA_FOLDER"] = wv_data
    logger.info("WEBVIEW2_USER_DATA_FOLDER=%s", wv_data)

    try:
        import webview
    except Exception as e:
        logger.error("import webview failed: %s\n%s", e, traceback.format_exc())
        return False

    # Включить нативные загрузки (Save dialog в WebView2), иначе attachment отменяется
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
    except Exception:
        pass

    # Явно Edge Chromium на Windows (избегаем legacy MSHTML)
    gui = None
    if sys.platform == "win32":
        gui = "edgechromium"

    app_origin = url.rstrip("/")
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        app_origin = f"{p.scheme}://{p.netloc}"
    except Exception:
        pass

    api = _DesktopApi(app_origin=app_origin, data_dir=data_dir, logger=logger)

    try:
        logger.info("Creating webview window url=%s gui=%s", url, gui)
        window = webview.create_window(
            title="Sitechecker — Аудит сайтов",
            url=url,
            js_api=api,
            width=1280,
            height=860,
            min_size=(960, 640),
            background_color="#0c0d0f",
            text_select=True,
        )
        api.set_window(window)

        # 1) target=_blank на внутренних страницах — остаёмся в окне приложения
        # 2) скачивания (отчёты, docx) — через native Save dialog + Проводник
        def _inject_desktop_helpers():
            try:
                window.evaluate_js(
                    r"""
                    (function() {
                      if (window.__scDesktopHelpers) return;
                      window.__scDesktopHelpers = true;

                      function isLocalHost(host) {
                        host = (host || '').toLowerCase();
                        return host === '127.0.0.1' || host === 'localhost'
                          || host === (window.location.hostname || '').toLowerCase();
                      }

                      function isDownloadUrl(href) {
                        try {
                          var u = new URL(href, window.location.href);
                          if (!isLocalHost(u.hostname)) return false;
                          var p = u.pathname || '';
                          return /\/download\//.test(p)
                            || /\/recommend\/download\//.test(p)
                            || /\/export\//.test(p)
                            || /\/api\/history\/\d+\/download\//.test(p)
                            || /\/api\/history\/\d+\/doc\//.test(p);
                        } catch (e) { return false; }
                      }

                      function toast(msg, isErr) {
                        try {
                          var el = document.getElementById('sc-dl-toast');
                          if (!el) {
                            el = document.createElement('div');
                            el.id = 'sc-dl-toast';
                            el.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:99999;padding:10px 18px;border-radius:8px;font:600 13px/1.3 system-ui,sans-serif;color:#fff;box-shadow:0 4px 20px rgba(0,0,0,.35);max-width:90vw;text-align:center;opacity:0;transition:opacity .2s;pointer-events:none';
                            document.body.appendChild(el);
                          }
                          el.style.background = isErr ? '#b91c1c' : '#0f766e';
                          el.textContent = msg;
                          el.style.opacity = '1';
                          clearTimeout(el._t);
                          el._t = setTimeout(function(){ el.style.opacity = '0'; }, 3200);
                        } catch (e) {}
                      }

                      window.__scDesktopDownload = function(url, suggestedName) {
                        if (!url) return Promise.resolve();
                        if (!(window.pywebview && window.pywebview.api && window.pywebview.api.download_file)) {
                          var a = document.createElement('a');
                          a.href = url;
                          a.setAttribute('download', '');
                          a.rel = 'noopener';
                          document.body.appendChild(a);
                          a.click();
                          setTimeout(function(){ a.remove(); }, 800);
                          return Promise.resolve();
                        }
                        toast('Сохранение файла…');
                        return window.pywebview.api.download_file(url, suggestedName || '')
                          .then(function(res) {
                            if (!res) return;
                            if (res.cancelled) { toast('Сохранение отменено'); return; }
                            if (res.ok) {
                              toast('Файл сохранён' + (res.path ? ': ' + res.path : ''));
                            } else {
                              toast(res.error || 'Не удалось сохранить', true);
                            }
                          })
                          .catch(function(err) {
                            toast(String(err || 'Ошибка сохранения'), true);
                          });
                      };

                      // Клики по ссылкам скачивания
                      document.addEventListener('click', function(e) {
                        var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
                        if (!a) return;
                        var href = a.getAttribute('href') || '';
                        if (!href || href.charAt(0) === '#' || href.indexOf('javascript:') === 0) return;

                        var abs;
                        try { abs = new URL(href, window.location.href); } catch (err) { return; }

                        if (isDownloadUrl(abs.href)) {
                          e.preventDefault();
                          e.stopPropagation();
                          window.__scDesktopDownload(abs.pathname + abs.search);
                          return;
                        }

                        // Скриншоты: никогда не заменяем UI проверки raw-картинкой
                        if (isLocalHost(abs.hostname) && /^\/screenshots\//.test(abs.pathname || '')) {
                          e.preventDefault();
                          e.stopPropagation();
                          var ssPath = abs.pathname + abs.search;
                          if (typeof window.openScreenshotLightbox === 'function') {
                            window.openScreenshotLightbox(ssPath, abs.pathname.split('/').pop() || 'Скриншот');
                          } else if (window.__scOpenScreenshot) {
                            window.__scOpenScreenshot(ssPath);
                          }
                          return;
                        }

                        // target=_blank на внутренних страницах UI — без ухода во внешний браузер
                        // (не для /screenshots/ — см. выше; не для внешних сайтов)
                        var wantsNew = (a.target || '').toLowerCase() === '_blank'
                          || e.ctrlKey || e.metaKey || e.button === 1;
                        if (!wantsNew) return;
                        if (!isLocalHost(abs.hostname)) return;
                        // Обычные страницы UI: history, admin, modules
                        e.preventDefault();
                        e.stopPropagation();
                        window.location.assign(abs.pathname + abs.search + abs.hash);
                      }, true);

                      // Fallback-лайтбокс, если openScreenshotLightbox ещё не определён (другая страница)
                      window.__scOpenScreenshot = function(src) {
                        if (typeof window.openScreenshotLightbox === 'function') {
                          window.openScreenshotLightbox(src, 'Скриншот');
                          return;
                        }
                        var box = document.getElementById('sc-ss-fallback');
                        if (!box) {
                          box = document.createElement('div');
                          box.id = 'sc-ss-fallback';
                          box.style.cssText = 'position:fixed;inset:0;z-index:100000;background:rgba(0,0,0,.85);display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px;box-sizing:border-box';
                          box.innerHTML = '<button type="button" id="sc-ss-fallback-close" style="margin-bottom:12px;padding:10px 18px;border:1px solid #00c2de;background:transparent;color:#00c2de;font:600 13px system-ui;cursor:pointer;border-radius:6px">← Назад к проверке</button><img id="sc-ss-fallback-img" alt="" style="max-width:96vw;max-height:85vh;object-fit:contain;border:1px solid #333;border-radius:8px"/>';
                          document.body.appendChild(box);
                          document.getElementById('sc-ss-fallback-close').onclick = function() {
                            box.style.display = 'none';
                            var im = document.getElementById('sc-ss-fallback-img');
                            if (im) im.removeAttribute('src');
                          };
                          box.addEventListener('click', function(ev) {
                            if (ev.target === box) document.getElementById('sc-ss-fallback-close').click();
                          });
                        }
                        document.getElementById('sc-ss-fallback-img').src = src;
                        box.style.display = 'flex';
                      };

                      // window.open('/…/download/…') → диалог сохранения
                      var _open = window.open;
                      window.open = function(u, name, specs) {
                        if (u && isDownloadUrl(String(u))) {
                          try {
                            var abs2 = new URL(String(u), window.location.href);
                            window.__scDesktopDownload(abs2.pathname + abs2.search);
                          } catch (err) {
                            window.__scDesktopDownload(String(u));
                          }
                          return null;
                        }
                        return _open.apply(window, arguments);
                      };
                    })();
                    """
                )
            except Exception as ex:
                logger.debug("desktop helpers inject failed: %s", ex)

        try:
            window.events.loaded += _inject_desktop_helpers
        except Exception as ex:
            logger.debug("events.loaded hook failed: %s", ex)

        # icon= часто роняет CLR в frozen-сборке — не передаём
        if gui:
            webview.start(debug=False, gui=gui)
        else:
            webview.start(debug=False)
        logger.info("Window closed normally")
        return True
    except Exception as e:
        logger.error("webview failed: %s\n%s", e, traceback.format_exc())
        return False


def main() -> int:
    logger, log_path = _setup_logging()
    logger.info("Sitechecker Desktop starting… frozen=%s", getattr(sys, "frozen", False))

    try:
        from config import DATA_DIR, PORT as CFG_PORT, get_resource_dir
        os.makedirs(DATA_DIR, exist_ok=True)
        logger.info("DATA_DIR=%s RESOURCE_DIR=%s", DATA_DIR, get_resource_dir())
    except Exception as e:
        logger.error("config import failed: %s\n%s", e, traceback.format_exc())
        _show_error(f"Не удалось загрузить конфигурацию:\n{e}\n\nЛог: {log_path}")
        return 1

    preferred = int(os.getenv("PORT", CFG_PORT or 6001))
    port = _find_free_port(preferred)
    os.environ["PORT"] = str(port)
    # config.PORT already read — Flask uses HOST/PORT from env only if re-read;
    # we pass port explicitly to app.run below.
    url = f"http://127.0.0.1:{port}/"
    logger.info("Binding Flask to %s (preferred was %s)", url, preferred)

    try:
        from app import app
    except Exception as e:
        logger.error("app import failed: %s\n%s", e, traceback.format_exc())
        _show_error(
            f"Не удалось загрузить приложение:\n{e}\n\nЛог:\n{log_path}"
        )
        return 1

    flask_error: list[str] = []

    def run_flask():
        try:
            app.run(
                host="127.0.0.1",
                port=port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )
        except Exception as e:
            flask_error.append(str(e))
            logger.error("Flask error: %s\n%s", e, traceback.format_exc())

    t = threading.Thread(target=run_flask, name="flask", daemon=True)
    t.start()

    if not _wait_http(url, timeout=45):
        err = flask_error[0] if flask_error else "таймаут ожидания HTTP"
        logger.error("Flask not ready: %s", err)
        _show_error(
            f"Сервер не запустился ({err}).\n\n"
            f"Порт: {port}\nЛог: {log_path}"
        )
        return 1

    logger.info("HTTP ready, starting UI")

    # 1) native window
    ok = _start_webview(url, DATA_DIR, logger)
    if ok:
        return 0

    # 2) browser fallback — Flask still running in daemon thread
    return _open_browser_and_wait(url, t, logger)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        try:
            log = os.path.join(
                os.environ.get("APPDATA", "."), "Sitechecker", "errors.log"
            )
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\nFATAL: {e}\n{traceback.format_exc()}\n")
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, f"Критическая ошибка:\n{e}", "Sitechecker", 0x10
            )
        except Exception:
            pass
        raise
