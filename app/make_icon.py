"""Draw the app icon as icon.icns: lines of text and a caret on a dark screen, with the look of an old monitor
(soft glow, scan lines, a faint vertical mask, darker corners and a little grain).

    python3 make_icon.py        # writes icon.icns next to this file (needs Pillow, numpy, scipy, iconutil)
"""
import shutil, subprocess, tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter

HERE = Path(__file__).resolve().parent
INK, PAPER = (16, 15, 14), (243, 240, 232)        # near-black screen and warm white
RED, ORANGE = (209, 58, 50), (245, 154, 69)


def draw(n):
    s = n / 1024
    body = [100 * s, 100 * s, 924 * s, 924 * s]     # macOS icon grid: 824 px body inside 1024
    im = Image.new("RGB", (n, n), INK)
    d = ImageDraw.Draw(im)
    # lines of text; the third is the one being edited, red warming to orange, with the caret after it
    widths = [470, 520, 330, 500, 440, 260]
    for i, w in enumerate(widths):
        y = (262 + i * 86) * s
        x0, x1 = 252 * s, (252 + w) * s
        if i == 2:
            for k in range(int(x0), int(x1)):       # horizontal blend from red to orange
                a = (k - x0) / max(1, x1 - x0)
                d.line([k, y, k, y + 34 * s], fill=tuple(int(RED[c] * (1 - a) + ORANGE[c] * a) for c in range(3)))
        else:
            d.rounded_rectangle([x0, y, x1, y + 34 * s], radius=8 * s, fill=tuple(int(c * 0.80) for c in PAPER))
    d.rectangle([(252 + widths[2] + 26) * s, (262 + 2 * 86 - 22) * s, (252 + widths[2] + 44) * s, (262 + 2 * 86 + 56) * s],
                fill=PAPER)                         # caret

    a = np.asarray(im, dtype=np.float32) / 255.0
    glow = gaussian_filter(a, sigma=(26 * s, 26 * s, 0))
    a = 1 - (1 - a) * (1 - 0.65 * glow)             # glow: the blurred picture screened over itself
    a = gaussian_filter(a, sigma=(1.2 * s, 1.2 * s, 0))
    yy, xx = np.mgrid[0:n, 0:n]
    a *= (1 - 0.24 * (0.5 + 0.5 * np.sin(2 * np.pi * yy / (10 * s))))[..., None]     # scan lines
    a *= (1 - 0.07 * (0.5 + 0.5 * np.sin(2 * np.pi * xx / (7 * s))))[..., None]      # vertical mask
    r = np.hypot(xx / n - 0.5, yy / n - 0.5)
    a *= (1 - 1.1 * np.clip(r - 0.30, 0, 1) ** 1.6 * 4)[..., None].clip(0.5, 1)      # darker corners
    grain = gaussian_filter(np.random.default_rng(5).standard_normal((n, n)).astype(np.float32), (0.6, 1.6))
    a = a + (0.014 + 0.02 * a.mean(axis=2))[..., None] * (grain / grain.std())[..., None]
    rgb = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))

    mask = Image.new("L", (n, n), 0)                # rounded-square body
    ImageDraw.Draw(mask).rounded_rectangle(body, radius=185 * s, fill=255)
    out = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    out.paste(rgb, (0, 0), mask)
    return out


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
