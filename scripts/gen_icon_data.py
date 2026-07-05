"""Regenerate subtitle_translator/icon_data.py from make_icon.draw_icon.

Run after changing the icon design in make_icon.py:

    python scripts/gen_icon_data.py

Requires Pillow (a build-time dependency, not needed at runtime).
"""
import base64
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from make_icon import draw_icon  # noqa: E402

SIZES = [16, 32, 48, 64, 128, 256]


def main():
    lines = [
        '"""App icon embedded as base64 PNGs (multiple sizes).',
        "",
        "Generated from make_icon.draw_icon so the running app has a window/",
        "taskbar icon without shipping separate image files. Regenerate with",
        "scripts/gen_icon_data.py after changing make_icon.py.",
        '"""',
        "",
        "# size -> base64-encoded PNG",
        "ICON_PNGS = {",
    ]
    for s in SIZES:
        buf = io.BytesIO()
        draw_icon(s).save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        chunks = [b64[i:i + 76] for i in range(0, len(b64), 76)]
        joined = "\n        ".join(f'"{c}"' for c in chunks)
        lines.append(f"    {s}: (\n        {joined}\n    ),")
    lines.append("}")

    out = ROOT / "subtitle_translator" / "icon_data.py"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
