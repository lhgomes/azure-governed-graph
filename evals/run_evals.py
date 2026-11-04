"""Compatibility entry point; sample cases and acceptance logic belong to pension_wtp."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))

from pension_wtp.evaluation import run_evals  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_evals.cli())

# Keep existing evaluator integrations and monkeypatches on the actual sample module.
sys.modules[__name__] = run_evals
