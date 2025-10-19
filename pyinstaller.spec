# -*- mode: python ; coding: utf-8 -*-

import pathlib

block_cipher = None

project_dir = pathlib.Path.cwd()

a = Analysis(
    ['lexisharp.py'],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[],
    hiddenimports=[
        'tkinter',
        'pyperclip',
        'dashscope',
        'evdev',
        # pynput 在 Linux 下按需加载以下后端组件，需显式保留
        'pynput',
        'pynput._util.xorg',
        'pynput._util.xorg_keysyms',
        'pynput._util.uinput',
        'pynput.keyboard._xorg',
        'pynput.keyboard._uinput',
        'pynput.mouse._xorg',
        'Xlib',
        'Xlib.display',
        'Xlib.X',
        'Xlib.ext.xtest',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='lexisharp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='lexisharp',
)
