"""Установка Chromium для Playwright в data dir (первый запуск)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_install_lock = threading.Lock()
_install_state = {"done": False, "error": "", "running": False}


def _browsers_dir() -> str:
    try:
        from config import PLAYWRIGHT_BROWSERS_DIR
        return PLAYWRIGHT_BROWSERS_DIR
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".sitechecker", "playwright-browsers")


def _find_browser_executable() -> str | None:
    """Ищет chrome.exe / chrome-headless-shell.exe в data dir."""
    root = Path(_browsers_dir())
    if not root.is_dir():
        return None
    names = (
        "chrome-headless-shell.exe",
        "chrome.exe",
        "chromium.exe",
        "headless_shell",
    )
    try:
        for p in root.rglob("*"):
            if p.is_file() and p.name.lower() in {n.lower() for n in names}:
                return str(p)
            # linux/mac names
            if p.is_file() and p.name in (
                "chrome-headless-shell", "chrome", "chromium", "headless_shell",
            ):
                return str(p)
    except OSError:
        return None
    return None


def chromium_installed() -> bool:
    return _find_browser_executable() is not None


def _install_command(env: dict) -> list[str]:
    """
    Команда установки браузеров.
    В frozen-сборке sys.executable = Sitechecker.exe — НЕЛЬЗЯ вызывать
    `sys.executable -m playwright install` (откроется второе окно приложения).
    Используем node-драйвер Playwright.
    """
    # 1) Драйвер из пакета playwright (dev и onedir, если datas включены)
    try:
        from playwright._impl._driver import compute_driver_executable
        driver = compute_driver_executable()
        # playwright>=1.40: tuple (node, cli.js) или Path
        if isinstance(driver, (list, tuple)):
            return list(driver) + ["install", "chromium"]
        return [str(driver), "install", "chromium"]
    except Exception as e:
        logger.warning("compute_driver_executable failed: %s", e)

    # 2) Dev: обычный python -m playwright
    if not getattr(sys, "frozen", False):
        return [sys.executable, "-m", "playwright", "install", "chromium"]

    # 3) Frozen fallback: искать node+cli рядом с _MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        node = Path(meipass) / "playwright" / "driver" / "node.exe"
        cli = Path(meipass) / "playwright" / "driver" / "package" / "cli.js"
        if node.is_file() and cli.is_file():
            return [str(node), str(cli), "install", "chromium"]

    raise RuntimeError(
        "Не удалось найти Playwright driver для установки Chromium. "
        "Переустановите приложение или выполните: playwright install chromium"
    )


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
            try:
                from playwright._impl._driver import get_driver_env
                env.update(get_driver_env())
                env["PLAYWRIGHT_BROWSERS_PATH"] = browsers
            except Exception:
                pass

            cmd = _install_command(env)
            # Никогда не запускать сам Sitechecker.exe как installer
            if getattr(sys, "frozen", False):
                exe_name = Path(sys.executable).name.lower()
                if cmd and Path(cmd[0]).name.lower() == exe_name:
                    raise RuntimeError(
                        f"Refuse to run install via app executable: {cmd[0]}"
                    )

            logger.info("Playwright install cmd: %s", cmd)
            # CREATE_NO_WINDOW — без чёрной консоли на Windows
            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=600,
                creationflags=creationflags,
            )
            out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            if proc.returncode != 0:
                err = out or f"exit code {proc.returncode}"
                _install_state["error"] = err[:500]
                logger.error("Playwright install failed: %s", err[:500])
                return False, err[:500]

            if not chromium_installed():
                msg = (
                    "Команда install завершилась, но chrome/headless-shell не найден. "
                    + out[:300]
                )
                _install_state["error"] = msg
                logger.error(msg)
                return False, msg

            _install_state["done"] = True
            logger.info("Playwright Chromium installed OK: %s", _find_browser_executable())
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
        "executable": _find_browser_executable(),
    }
