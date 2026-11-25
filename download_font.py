"""
One-off helper: downloads Montserrat ExtraBold TTF from the official
Google Fonts zip and saves it to assets/fonts/.

Run from the project root:
    python download_font.py
"""
import io
import sys
import zipfile
import urllib.request
from pathlib import Path

DEST = Path("assets/fonts/Montserrat-ExtraBold.ttf")
# Google Fonts API zip for Montserrat (all weights)
URL = "https://fonts.google.com/download?family=Montserrat"
TARGET_NAME = "static/Montserrat-ExtraBold.ttf"

print(f"Downloading Montserrat family zip from Google Fonts...")
try:
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
except Exception as e:
    sys.exit(f"Download failed: {e}")

print(f"Received {len(data):,} bytes, extracting {TARGET_NAME}...")
try:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        ttf_bytes = zf.read(TARGET_NAME)
except KeyError:
    # list available names to help diagnose
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if "ExtraBold" in n]
    sys.exit(f"'{TARGET_NAME}' not found in zip. ExtraBold entries: {names}")

DEST.parent.mkdir(parents=True, exist_ok=True)
DEST.write_bytes(ttf_bytes)
print(f"Saved {len(ttf_bytes):,} bytes → {DEST}")

# Quick sanity check
from PIL import ImageFont
f = ImageFont.truetype(str(DEST), 92)
print(f"Font loaded OK: {f.getname()}")
