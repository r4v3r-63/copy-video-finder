# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller build.spec --noconfirm
# Только режим папки (onedir): однофайловый exe антивирусы принимают за троян.
# UPX не используется по той же причине.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

download_data = collect_data_files("yt_dlp_ejs") + collect_data_files("yt_dlp")
download_imports = collect_submodules("yt_dlp") + collect_submodules("yt_dlp_ejs")

webview_data = collect_data_files("webview", includes=["lib/**/*.dll"])

a = Analysis(
    ["run_app.py"],
    pathex=["."],
    datas=[("resources", "resources"), ("docs/USER_GUIDE.md", "docs"), ("docs/vk.md", "docs")] + webview_data + download_data,
    hiddenimports=["PIL.AvifImagePlugin"] + download_imports,  # превью Coverr приходят в AVIF
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
