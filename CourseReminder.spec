# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path.cwd()
PY_ROOT = Path(r"C:\Users\Lenovo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python")
TCL_DIR = PY_ROOT / "tcl" / "tcl8.6"
TK_DIR = PY_ROOT / "tcl" / "tk8.6"

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(TCL_DIR), "_tcl_data"),
    (str(TK_DIR), "_tk_data"),
]


a = Analysis(
    ["run_desktop.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["requests", "sqlite3", "tkinter", "tkinter.ttk"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CourseReminder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CourseReminder",
)
