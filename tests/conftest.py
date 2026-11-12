import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))
os.environ.setdefault("GOVERNEDGRAPH_DATA_DIR", str(ROOT / "examples/pension_wtp/data"))
