#!/usr/bin/env python3
"""Fetch latest ffmpeg build from Academy-FFmpeg-Build into vendor folder.

Used by both local dev setup and the release workflow. Run from repo root.
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile

REPO = "hornet-animation/Academy-FFmpeg-Build"
ASSETS = {
    "ffmpeg-windows-x86_64.zip": "windows",
    "ffmpeg-linux-x86_64.zip": "linux",
}
VENDOR_DIR = os.path.join("client", "ayon_nuke", "vendor", "ffmpeg")


def main():
    api = f"https://api.github.com/repos/{REPO}/releases/latest"
    print(f"Fetching {api}")
    with urllib.request.urlopen(api) as resp:
        release = json.load(resp)
    print(f"Latest release: {release.get('tag_name', '<unknown>')}")

    by_name = {a["name"]: a["browser_download_url"] for a in release["assets"]}
    for asset_name, subdir in ASSETS.items():
        url = by_name.get(asset_name)
        if not url:
            sys.exit(f"Asset not found in latest release: {asset_name}")
        dest = os.path.join(VENDOR_DIR, subdir)
        print(f"Extracting {asset_name} -> {dest}")
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(dest, exist_ok=True)
        # stream to a temp file so we don't hold ~160MB in RAM
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            with urllib.request.urlopen(url) as resp:
                shutil.copyfileobj(resp, tmp)
            tmp_path = tmp.name
        try:
            with zipfile.ZipFile(tmp_path) as zf:
                zf.extractall(dest)
        finally:
            os.unlink(tmp_path)
    print("Done.")


if __name__ == "__main__":
    main()
