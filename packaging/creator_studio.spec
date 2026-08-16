# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is the directory containing this spec. The repository root is its parent.
root = Path(SPECPATH).parent.resolve()

datas = [
    (str(root / "assets"), "assets"),
    (str(root / "NOTICE.md"), "."),
    (str(root / "LICENSE"), "."),
]
binaries = []
hiddenimports = []

# These libraries carry templates, frontend assets, dynamic modules, native binaries,
# or runtime model code that static analysis cannot always see. Model weights are
# intentionally NOT bundled; the product downloads only models the user chooses.
for package in ("gradio", "webview", "chatterbox", "faster_whisper", "ctranslate2"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hidden
    except Exception:
        hiddenimports += collect_submodules(package)

for package in ("transformers", "diffusers", "huggingface_hub"):
    hiddenimports += collect_submodules(package)

analysis = Analysis(
    [str(root / "desktop_launcher.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "playwright"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="CreatorStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CreatorStudio",
)
