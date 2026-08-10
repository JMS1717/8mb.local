# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['shared.local_runtime', 'celery.backends.cache', 'celery.loaders.app', 'kombu.transport.memory', 'worker.app.tasks', 'worker.app.startup_tests']
hiddenimports += collect_submodules('app')
hiddenimports += collect_submodules('worker.app')


a = Analysis(
    ['C:/Users/jdude/OneDrive/Documents/GitHub/8mb.local/windows/desktop_app.py'],
    pathex=['C:/Users/jdude/OneDrive/Documents/GitHub/8mb.local/backend-api', 'C:/Users/jdude/OneDrive/Documents/GitHub/8mb.local'],
    binaries=[('C:/Users/jdude/OneDrive/Documents/GitHub/8mb.local/windows/ffmpeg/bin/ffmpeg.exe', 'bin'), ('C:/Users/jdude/OneDrive/Documents/GitHub/8mb.local/windows/ffmpeg/bin/ffprobe.exe', 'bin')],
    datas=[('C:/Users/jdude/OneDrive/Documents/GitHub/8mb.local/frontend/build', 'frontend-build')],
    hiddenimports=hiddenimports,
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
    a.binaries,
    a.datas,
    [],
    name='8mblocal-debug',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
