"""
Generate SubtitleTranslator.icns for macOS.
Run from the repo root with the venv active:
    python make_icon.py
"""
import os
import math
import shutil
import subprocess
from PIL import Image, ImageDraw, ImageFont

SIZES = [16, 32, 64, 128, 256, 512, 1024]


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size

    # --- background: rounded rectangle, dark cinema charcoal ---
    radius = s * 0.18
    bg_color = (28, 28, 36, 255)
    x0, y0, x1, y1 = 0, 0, s - 1, s - 1
    d.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=bg_color)

    # --- film strip perforations on left and right edges ---
    perf_color = (50, 50, 62, 255)
    perf_count = 4
    perf_w = s * 0.08
    perf_h = s * 0.09
    perf_margin_x = s * 0.03
    perf_margin_y = s * 0.12
    perf_gap = (s - 2 * perf_margin_y - perf_count * perf_h) / (perf_count - 1)
    perf_r = perf_w * 0.3
    for i in range(perf_count):
        cy = perf_margin_y + i * (perf_h + perf_gap)
        for cx_start in [perf_margin_x, s - perf_margin_x - perf_w]:
            d.rounded_rectangle(
                [cx_start, cy, cx_start + perf_w, cy + perf_h],
                radius=perf_r,
                fill=perf_color,
            )

    # --- subtitle text area: two white bars (like real subs) ---
    bar_color = (255, 255, 255, 240)
    bar2_color = (255, 255, 255, 160)
    content_x = s * 0.17
    content_w = s * 0.66

    bar_h = s * 0.07
    bar_r = bar_h * 0.4

    # bottom bar (full width) — primary subtitle line
    bar1_y = s * 0.62
    d.rounded_rectangle(
        [content_x, bar1_y, content_x + content_w, bar1_y + bar_h],
        radius=bar_r, fill=bar_color,
    )

    # top bar (shorter) — secondary line
    bar2_y = bar1_y - bar_h * 1.6
    bar2_w = content_w * 0.72
    d.rounded_rectangle(
        [content_x, bar2_y, content_x + bar2_w, bar2_y + bar_h],
        radius=bar_r, fill=bar2_color,
    )

    # --- translation arrow below bars ---
    arrow_color = (90, 160, 255, 230)
    ax_center = s * 0.5
    ay = s * 0.79
    arrow_w = s * 0.22
    arrow_h = s * 0.09
    shaft_h = arrow_h * 0.4
    shaft_y0 = ay - shaft_h / 2
    shaft_y1 = ay + shaft_h / 2
    head_half = arrow_h / 2

    # horizontal shaft
    d.rectangle(
        [ax_center - arrow_w * 0.5, shaft_y0,
         ax_center + arrow_w * 0.1, shaft_y1],
        fill=arrow_color,
    )
    # arrowhead (triangle)
    tip_x = ax_center + arrow_w * 0.5
    d.polygon(
        [
            (ax_center + arrow_w * 0.08, ay - head_half),
            (tip_x, ay),
            (ax_center + arrow_w * 0.08, ay + head_half),
        ],
        fill=arrow_color,
    )

    # --- accent top bar (thin highlight stripe) ---
    accent = (90, 160, 255, 180)
    stripe_h = max(2, int(s * 0.025))
    stripe_y = s * 0.20
    stripe_x0 = content_x
    stripe_x1 = content_x + content_w
    d.rounded_rectangle(
        [stripe_x0, stripe_y, stripe_x1, stripe_y + stripe_h],
        radius=stripe_h * 0.5,
        fill=accent,
    )

    return img


def build_iconset(out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    for size in SIZES:
        img = draw_icon(size)
        img.save(os.path.join(out_dir, f"icon_{size}x{size}.png"))
        if size <= 512:
            img2 = draw_icon(size * 2)
            img2.save(os.path.join(out_dir, f"icon_{size}x{size}@2x.png"))


def main():
    iconset_dir = "SubtitleTranslator.iconset"
    icns_path = os.path.join("subtitle_translator", "SubtitleTranslator.icns")

    build_iconset(iconset_dir)
    subprocess.run(
        ["iconutil", "-c", "icns", iconset_dir, "-o", icns_path],
        check=True,
    )
    shutil.rmtree(iconset_dir)
    print(f"Icon written to {icns_path}")


if __name__ == "__main__":
    main()
