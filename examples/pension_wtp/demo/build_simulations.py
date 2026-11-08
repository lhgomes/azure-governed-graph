"""Project ILLUSTRATIVE - synthetic data into deployment scripts, never profiles."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

from governedgraph.voice.simulations import WATERMARK, load_simulations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    body = {"watermark": WATERMARK, "simulations": [asdict(item) for item in load_simulations()]}
    args.output.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
