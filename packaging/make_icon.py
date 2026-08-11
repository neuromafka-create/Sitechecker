"""Create packaging/sitechecker.ico from static/img/logo.png."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "static" / "img" / "logo.png"
OUT = ROOT / "packaging" / "sitechecker.ico"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if not SRC.exists():
        print(f"No logo at {SRC}", file=sys.stderr)
        return 1
    try:
        from PIL import Image
    except ImportError:
        print("pillow required: pip install pillow", file=sys.stderr)
        return 1

    img = Image.open(SRC).convert("RGBA")
    # square canvas
    size = max(img.size)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - img.size[0]) // 2, (size - img.size[1]) // 2))
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icons = [canvas.resize(s, Image.Resampling.LANCZOS) for s in sizes]
    icons[0].save(OUT, format="ICO", sizes=sizes, append_images=icons[1:])
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
