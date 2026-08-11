"""
Sitechecker Desktop — Windows shell.

Запускает Flask на 127.0.0.1 и открывает нативное окно (pywebview / WebView2).
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

# Режим десктопа до импорта config/app
os.environ["SITECHECKER_DESKTOP"] = "1"
os.environ.setdefault("HOST", "127.0.0.1")


def _find_free_port(preferred: int = 6001) -> int:
    for port in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
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
            os.environ.get("APPDATA", "."), "Sitechecker", "desktop.log"
        )
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("desktop")


def main() -> int:
    logger = _setup_logging()
    logger.info("Sitechecker Desktop starting…")

    try:
        from config import DATA_DIR, PORT as CFG_PORT, get_resource_dir
        logger.info("DATA_DIR=%s RESOURCE_DIR=%s", DATA_DIR, get_resource_dir())
    except Exception as e:
        logger.error("config import failed: %s\n%s", e, traceback.format_exc())
        return 1

    port = _find_free_port(int(os.getenv("PORT", CFG_PORT or 6001)))
    os.environ["PORT"] = str(port)
    url = f"http://127.0.0.1:{port}/"
    logger.info("Binding Flask to %s", url)

    try:
        from app import app
    except Exception as e:
        logger.error("app import failed: %s\n%s", e, traceback.format_exc())
        _show_error(f"Не удалось загрузить приложение:\n{e}")
        return 1

    ready = threading.Event()

    def run_flask():
        try:
            # werkzeug: use_reloader=False обязателен в потоке
            app.run(
                host="127.0.0.1",
                port=port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )
        except Exception as e:
            logger.error("Flask error: %s\n%s", e, traceback.format_exc())

    t = threading.Thread(target=run_flask, name="flask", daemon=True)
    t.start()

    # Ждём готовности HTTP
    deadline = time.time() + 30
    import urllib.request
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status < 500:
                    ready.set()
                    break
        except Exception:
            time.sleep(0.2)

    if not ready.is_set():
        logger.warning("Flask not ready in 30s — opening window anyway")

    try:
        import webview
    except ImportError:
        logger.error("pywebview not installed")
        # Fallback: браузер
        import webbrowser
        webbrowser.open(url)
        try:
            while t.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    icon = None
    try:
        from config import RESOURCE_DIR
        candidate = os.path.join(RESOURCE_DIR, "static", "img", "logo.png")
        if os.path.isfile(candidate):
            icon = candidate
    except Exception:
        pass

    window = webview.create_window(
        title="Sitechecker — Аудит сайтов",
        url=url,
        width=1280,
        height=860,
        min_size=(960, 640),
        background_color="#0c0d0f",
        text_select=True,
    )
    # icon parameter depends on pywebview version — set if supported
    try:
        webview.start(debug=False, icon=icon)
    except TypeError:
        webview.start(debug=False)

    logger.info("Window closed — exiting")
    return 0


def _show_error(msg: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "Sitechecker", 0x10)
    except Exception:
        print(msg, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
