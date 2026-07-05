"""Print the CHANGELOG.md section for a given version.

Used by the release workflow to build GitHub release notes:

    python scripts/extract_changelog.py v1.4.4 > release_notes.md

Exits non-zero if there is no section for the version — this deliberately
fails the release so no version can be published without a changelog entry.
"""
import pathlib
import re
import sys


def extract(changelog: str, version: str) -> str:
    version = version.lstrip("v").strip()
    lines = changelog.splitlines()
    out = []
    capturing = False
    for line in lines:
        m = re.match(r"^##\s+v?([0-9][^\s]*)\s*$", line)
        if m:
            if capturing:  # reached the next version header — stop
                break
            capturing = m.group(1) == version
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def main():
    if len(sys.argv) < 2:
        print("usage: extract_changelog.py <version>", file=sys.stderr)
        raise SystemExit(2)
    version = sys.argv[1]
    path = pathlib.Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    section = extract(path.read_text(encoding="utf-8"), version)
    if not section:
        print(
            f"ERROR: no CHANGELOG.md section for version {version!r}. "
            f"Add a '## {version.lstrip('v')}' entry before releasing.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(section)


if __name__ == "__main__":
    main()
