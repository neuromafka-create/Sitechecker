# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: Sitechecker Desktop (Windows onedir)
# Build: pyinstaller --noconfirm sitechecker.spec

import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "templates"), "templates"),
    (str(ROOT / "static"), "static"),
    (str(ROOT / "prompts"), "prompts"),
    (str(ROOT / "admin_settings.example.json"), "."),
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "version.py"), "."),
]

hiddenimports = [
    "flask",
    "jinja2",
    "werkzeug",
    "werkzeug.serving",
    "bs4",
    "lxml",
    "lxml.etree",
    "lxml._elementpath",
    "openpyxl",
    "pandas",
    "openai",
    "dotenv",
    "playwright",
    "playwright.async_api",
    "playwright.sync_api",
    "greenlet",
    "webview",
    "clr",
    "pythonnet",
    "app",
    "app_auth",
    "app_sanctions",
    "auth",
    "auth.checker",
    "auth.prompts",
    "auth.rec_report",
    "auth.sources",
    "sanctions",
    "sanctions.checker",
    "sanctions.prompts",
    "sanctions.rec_report",
    "sanctions.sources",
    "checker",
    "config",
    "deepseek",
    "history",
    "report",
    "rec_report",
    "playwright_setup",
    "version",
]

a = Analysis(
    [str(ROOT / "desktop_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "IPython", "notebook"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Sitechecker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "packaging" / "sitechecker.ico")
    if (ROOT / "packaging" / "sitechecker.ico").exists()
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Sitechecker",
)
