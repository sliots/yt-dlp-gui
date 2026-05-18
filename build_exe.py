import argparse
import os
import subprocess
import sys
from pathlib import Path


EXCLUDE_MODULES = [
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngine",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSql",
    "PySide6.QtNetwork",
    "PySide6.QtOpenGL",
    "PySide6.QtSvg",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", action="store_true")
    parser.add_argument("--name", default="YT-DLP-Downloader")
    parser.add_argument("--upx-dir", default=r"C:\apps\upx\upx.exe")
    parser.add_argument("--no-exclude", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    script = root / "yt_downloader_gui.py"
    if not script.exists():
        raise FileNotFoundError(str(script))

    add_data = []
    for name in ["yt-dlp.exe", "ffmpeg.exe"]:
        p = root / name
        if p.exists():
            add_data.append(f"{p}{os.pathsep}.")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--name",
        args.name,
    ]
    if args.clean:
        cmd.append("--clean")
    if not args.dir:
        cmd.append("--onefile")
    if args.upx_dir:
        upx_path = Path(args.upx_dir)
        if upx_path.suffix.lower() == ".exe" and upx_path.is_file():
            upx_dir = upx_path.parent
        else:
            upx_dir = upx_path
        cmd += ["--upx-dir", str(upx_dir)]
    if not args.no_exclude:
        for module in EXCLUDE_MODULES:
            cmd += ["--exclude-module", module]
    for module in args.exclude:
        cmd += ["--exclude-module", module]
    for item in add_data:
        cmd += ["--add-data", item]
    cmd.append(str(script))

    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
