"""Reject temporary scripts and hard-coded Azure AI endpoints in tracked source.

No Azure resources or credentials are needed. Complements (not replaces)
GitHub secret scanning and review of historical commits and nontext artifacts.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TEMPORARY = re.compile(r"(?:^|/)(?:temp|scratch)\.(?:py|sh)$|^(?:tests/)?(?:tmp|scratch)/", re.I)
AI_ENDPOINT = re.compile(
    r"https?://([a-z][a-z0-9-]{2,})"
    r"\.(?:services\.ai\.azure\.com|openai\.azure\.com|cognitiveservices\.azure\.com)"
    r"(?=[/:?\"'\s]|$)", re.I
)
SYNTHETIC = {"synthetic", "example", "test", "fake", "mock", "placeholder", "contoso", "sample"}
TEXT = {".py", ".sh", ".bicep", ".md", ".yaml", ".yml", ".json",
        ".jsonl", ".toml", ".ps1", ".txt", ".env", ".http"}


def real_endpoint_found(content: str) -> bool:
    for match in AI_ENDPOINT.finditer(content):
        host = match.group(1).lower()
        if host not in SYNTHETIC and not any(
            host.startswith(label + "-") or host.endswith("-" + label)
            for label in SYNTHETIC
        ):
            return True
    return False


def public_hygiene_violations() -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    problems = []
    for item in result.stdout.split(b"\0"):
        if not item:
            continue
        relative = Path(item.decode("utf-8"))
        name = relative.as_posix()
        if TEMPORARY.search(name):
            problems.append(name + ": remove tracked exploratory script")
            continue
        if relative.suffix.lower() not in TEXT:
            continue
        file = ROOT / relative
        if not file.is_file():
            continue
        try:
            content = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if real_endpoint_found(content):
            # Don't re-expose endpoint names in CI output.
            problems.append(name + ": deployment-specific Azure AI endpoint literal")
    return problems


def main() -> int:
    issues = public_hygiene_violations()
    for issue in issues:
        print(issue, file=sys.stderr)
    print("Public source hygiene: " + ("FAILED" if issues else "PASSED"))
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
