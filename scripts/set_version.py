"""Write the release version into subtitle_translator/__init__.py.

Used by CI before the PyInstaller build so the frozen binary knows its
own version (the source of truth is the git tag):

    python scripts/set_version.py v1.4.0
"""
import sys
import pathlib


def main():
    if len(sys.argv) < 2:
        print("usage: set_version.py <tag>", file=sys.stderr)
        raise SystemExit(2)
    version = sys.argv[1].lstrip("v").strip()
    init_path = pathlib.Path(__file__).resolve().parent.parent / "subtitle_translator" / "__init__.py"
    init_path.write_text(
        f'# Package entry\n__version__ = "{version}"\n', encoding="utf-8"
    )
    print(f"Set __version__ = {version} in {init_path}")


if __name__ == "__main__":
    main()
