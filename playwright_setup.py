"""Установка Chromium для Playwright в data dir (первый запуск)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

_install_lock = threading.Lock()
_install_state = {"done": False, "error": "", "running": False}


def _browsers_dir() -> str:
    try:
        from config import PLAYWRIGHT_BROWSERS_DIR
        return PLAYWRIGHT_BROWSERS_DIR
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".sitechecker", "playwright-browsers")


def chromium_installed() -> bool:
    """Грубая проверка: есть ли каталог chromium в PLAYWRIGHT_BROWSERS_PATH."""
    root = _browsers_dir()
    if not os.path.isdir(root):
        return False
    try:
        for name in os.listdir(root):
            low = name.lower()
            if "chromium" in low or "chrome" in low:
                path = os.path.join(root, name)
                if os.path.isdir(path):
                    return True
    except OSError:
        return False
    return False


def ensure_playwright_browsers(force: bool = False) -> tuple[bool, str]:
    """
    Скачивает Chromium при первом использовании Playwright.
    Потокобезопасно. Возвращает (ok, message).
    """
    browsers = _browsers_dir()
    os.makedirs(browsers, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers

    if not force and chromium_installed():
        _install_state["done"] = True
        return True, "Chromium already installed"

    with _install_lock:
        if not force and chromium_installed():
            _install_state["done"] = True
            return True, "Chromium already installed"
        if _install_state.get("running"):
            return False, "Install already in progress"

        _install_state["running"] = True
        _install_state["error"] = ""
        try:
            logger.info("Installing Playwright Chromium into %s …", browsers)
            env = os.environ.copy()
            env["PLAYWRIGHT_BROWSERS_PATH"] = browsers
            # python -m playwright install chromium
            cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                timeout=600,
            )
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "unknown error").strip()
                _install_state["error"] = err
                logger.error("Playwright install failed: %s", err)
                return False, err
            _install_state["done"] = True
            logger.info("Playwright Chromium installed OK")
            return True, "Chromium installed"
        except subprocess.TimeoutExpired:
            msg = "Timeout while downloading Chromium (>10 min)"
            _install_state["error"] = msg
            logger.error(msg)
            return False, msg
        except Exception as e:
            _install_state["error"] = str(e)
            logger.error("Playwright install error: %s", e)
            return False, str(e)
        finally:
            _install_state["running"] = False


def get_install_status() -> dict:
    return {
        "installed": chromium_installed(),
        "running": _install_state.get("running", False),
        "done": _install_state.get("done", False),
        "error": _install_state.get("error", ""),
        "browsers_path": _browsers_dir(),
    }
