"""Export/check design-only APIM OpenAPI and Service Bus JSON Schemas; no Azure calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pension_wtp.export_design_contracts import message_document, openapi_document

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for filename, document in (
        ("design-depth.openapi.json", openapi_document()),
        ("design-depth-messages.schema.json", message_document()),
    ):
        path = ROOT / "docs" / "api" / filename
        text = json.dumps(document, indent=2, ensure_ascii=True) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != text:
                raise SystemExit(f"Contract export is stale: {path}")
        else:
            path.write_text(text)


if __name__ == "__main__":
    main()
