"""Static GitHub Markdown link verification. Standard library; no Azure inputs or runtime."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
EXTERNAL = ("https:", "http:", "mailto:", "data:", "//", "#")


def paths():
    for root in (ROOT, ROOT / "docs", ROOT / "examples"):
        if root == ROOT:
            yield from (ROOT / name for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md"))
        else:
            yield from root.rglob("*.md")


def broken_links():
    failures = []
    for doc in paths():
        if not doc.exists():
            continue
        fence = chr(96) * 3
        data = re.sub(r"(?s)" + fence + r".*?" + fence, "", doc.read_text(encoding="utf-8"))
        for match in LINK.finditer(data):
            raw = match.group(1).split(" ", 1)[0].strip("<>")
            if raw.startswith(EXTERNAL) or raw.startswith("{") or raw == "":
                continue
            path = unquote(raw.split("#", 1)[0].split("?", 1)[0])
            if not path:
                continue
            target = (doc.parent / path).resolve()
            if ROOT not in target.parents and target != ROOT:
                failures.append(f"{doc.relative_to(ROOT)}: outside repository: {path}")
            elif not target.exists():
                failures.append(f"{doc.relative_to(ROOT)}: missing: {path}")
    return failures


def main() -> int:
    errors = broken_links()
    for error in errors:
        print(error, file=sys.stderr)
    print(f"Markdown documentation links: {'FAILED' if errors else 'OK'} ({len(errors)} missing)")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
