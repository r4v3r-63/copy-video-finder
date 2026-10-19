# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller build.spec --noconfirm
# Только режим папки (onedir): однофайловый exe антивирусы принимают за троян.
# UPX не используется по той же причине.

a = Analysis(
    ["run_app.py"],
    pathex=["."],
    datas=[("resources", "resources")],
    hiddenimports=["PIL.AvifImagePlugin"],  # превью Coverr приходят в AVIF
    excludes=["tkinter", "pytest", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CopyVideoFinder",
    console=False,
    icon="resources/logo.ico",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CopyVideoFinder", upx=False)
