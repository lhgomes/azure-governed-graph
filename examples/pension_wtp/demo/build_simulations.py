"""Project ILLUSTRATIVE - synthetic data into demo personas: behaviour cards only, never profiles or ages."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

from governedgraph.common.config import DATA_DIR
from governedgraph.common.io import load_json
from governedgraph.voice.simulations import WATERMARK, Simulation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = load_json(DATA_DIR / "personas" / "personas.json")
    if WATERMARK not in source.get("watermark", ""):
        raise ValueError("only watermarked synthetic persona fixtures may be projected")
    rows = []
    for profile in source["personas"]:
        for contact_type in ("first_contact", "second_contact"):
            scenario = profile if contact_type == "first_contact" else profile["second_contact"]
            simulation_id = profile["persona_id"] + ("-return" if contact_type == "second_contact" else "")
            item = Simulation.from_dict({
                "simulation_id": simulation_id,
                "contact_type": contact_type,
                **{key: profile[key] for key in (
                    "display_name", "member_ref", "channel", "accessibility_mode",
                )},
                "persona": {"persona_id": simulation_id, "goal": scenario["goal"],
                            "opening": scenario["opening"], **profile["live"],
                            **(scenario["live"] if contact_type == "second_contact" else {})},
            })
            rows.append(asdict(item))
    if not rows or len({row["simulation_id"] for row in rows}) != len(rows):
        raise ValueError("synthetic simulations must be nonempty and unique")
    args.output.write_text(json.dumps({"watermark": WATERMARK, "simulations": rows}, indent=2) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    main()
