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

    # Явно Edge Chromium на Windows (избегаем legacy MSHTML)
    gui = None
    if sys.platform == "win32":
        gui = "edgechromium"

    try:
        logger.info("Creating webview window url=%s gui=%s", url, gui)
        webview.create_window(
            title="Sitechecker — Аудит сайтов",
            url=url,
            width=1280,
            height=860,
            min_size=(960, 640),
            background_color="#0c0d0f",
            text_select=True,
        )
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
