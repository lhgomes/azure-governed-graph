"""Compatibility entry point; sample cases and acceptance logic belong to the selected sample (GOVERNEDGRAPH_SAMPLE)."""
import importlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))

SAMPLE = os.getenv("GOVERNEDGRAPH_SAMPLE", "pension_wtp")
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", SAMPLE):
    raise SystemExit("GOVERNEDGRAPH_SAMPLE must be the name of a Python package under examples/")
run_evals = importlib.import_module(f"{SAMPLE}.evaluation.run_evals")

if __name__ == "__main__":
    raise SystemExit(run_evals.cli())

# Keep existing evaluator integrations and monkeypatches on the actual sample module.
sys.modules[__name__] = run_evals
