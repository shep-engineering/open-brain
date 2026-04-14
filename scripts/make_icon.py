"""One-shot helper to regenerate assets/brain.ico from a source PNG.

Usage:
    python scripts/make_icon.py <path/to/source.png>

If no argument is given, defaults to assets/brain-source.png relative
to the repo root. Output is always written to assets/brain.ico.
"""
import sys
from pathlib import Path
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = REPO_ROOT / "assets" / "brain-source.png"
OUT_PATH = REPO_ROOT / "assets" / "brain.ico"

src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
if not src.exists():
    sys.exit(f"source image not found: {src}")

img = Image.open(src).convert("RGBA")
# Crop to square from center
w, h = img.size
side = min(w, h)
left = (w - side) // 2
top = (h - side) // 2
img = img.crop((left, top, left + side, top + side))
img.save(
    OUT_PATH,
    format="ICO",
    sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
)
print(f"Done: {OUT_PATH}")
