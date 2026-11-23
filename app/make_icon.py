"""Draw the app icon (a page with lines of text and a pen nib) as icon.icns.

    python3 make_icon.py        # writes icon.icns next to this file (needs Pillow, iconutil)
"""
import shutil, subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent


def draw(n):
    s = n / 1024
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # rounded-square background (macOS icon grid: 824 px body inside 1024)
    d.rounded_rectangle([100 * s, 100 * s, 924 * s, 924 * s], radius=185 * s, fill=(44, 82, 130))
    # page
    d.rounded_rectangle([270 * s, 190 * s, 754 * s, 834 * s], radius=28 * s, fill=(250, 248, 243))
    # text lines
    for i, w in enumerate([400, 400, 330, 400, 380, 250]):
        y = (300 + i * 72) * s
        d.rounded_rectangle([322 * s, y, (322 + w) * s, y + 26 * s], radius=13 * s,
                            fill=(170, 166, 158) if i != 2 else (214, 120, 60))
    # caret
    d.rectangle([660 * s, 432 * s, 672 * s, 492 * s], fill=(44, 82, 130))
    return im


def main():
    tmp = Path(tempfile.mkdtemp())
    iconset = tmp / "icon.iconset"
    iconset.mkdir()
    big = draw(1024)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = "icon_%dx%d%s.png" % (size, size, "@2x" if scale == 2 else "")
            big.resize((px, px), Image.LANCZOS).save(iconset / name)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "icon.icns")], check=True)
    shutil.rmtree(tmp)
    print("wrote", HERE / "icon.icns")


if __name__ == "__main__":
    main()
