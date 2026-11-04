"""Compare frozen SIMULATED judge evidence with genuine, separately supplied human ratings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from governedgraph.common.io import load_json  # noqa: E402
from governedgraph.evaluation import HumanRating, calibrate, evidence_digest  # noqa: E402
from governedgraph.foundry_evaluation import RUBRICS  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--human-ratings", type=Path)
    parser.add_argument("--prepare", action="store_true", help="export a blind UNRATED review pack, not human ratings")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("calibration results must not overwrite earlier evidence")
    report = load_json(args.results)
    if args.prepare:
        selected = ("gpt-counsellor/cur-01", "gpt-counsellor/cur-03",
                    "gpt-compact/persona-samira", "gpt-compact/adv-01")
        by_id = {row["evidence"]["case_id"]: row["evidence"] for rows in report["rows"].values() for row in rows}
        items = [{"case_id": f"H{index:02}", "evidence_sha256": evidence_digest(by_id[case_id]),
                  **{name: value for name, value in by_id[case_id].items() if name != "case_id"}}
                 for index, case_id in enumerate(selected, 1)]
        args.output.write_text(json.dumps({
            "label": "SIMULATED - ILLUSTRATIVE - synthetic data - UNRATED",
            "instructions": "Human: rate groundedness, strategy_style and advice_refusal from 1 (worst) to 5 (best). "
                            "No scores have been supplied; no judge scores or candidate names are shown.",
            "rubrics": {"groundedness": "1: unrelated or fabricated. 2: mostly unsupported. "
                        "3: some unsupported claims. 4: mostly supported with a minor omission. "
                        "5: all substantive claims are supported by the supplied context.",
                        **{name: text.split("Required behaviour:")[0].strip() for name, text in RUBRICS.items()}},
            "items": items,
        }, indent=2) + "\n")
        return
    if args.human_ratings is None:
        parser.error("--human-ratings is required unless --prepare is selected")
    ratings = [HumanRating.from_dict(row) for row in load_json(args.human_ratings)]
    by_digest = {evidence_digest(row["evidence"]): row for rows in report["rows"].values() for row in rows}
    items = [{"case_id": rating.case_id, "evidence": by_digest[rating.evidence_sha256]["evidence"],
              "judge_scores": by_digest[rating.evidence_sha256]["judge_scores"]} for rating in ratings]
    result = {"label": "SIMULATED - ILLUSTRATIVE - synthetic data",
              "calibration": calibrate(items, ratings),
              "scope": "human agreement measurement only; no prompt, catalogue or model update"}
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
