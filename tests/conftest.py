import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))
os.environ.setdefault("GOVERNEDGRAPH_MODE", "local")
os.environ.setdefault("GOVERNEDGRAPH_DATA_DIR", str(ROOT / "examples/pension_wtp/data"))
os.environ.setdefault("GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER", "pension_wtp.member_values_api.data:get_member_values")
os.environ.setdefault("GOVERNEDGRAPH_TEXT_DEMO_MODULE", "pension_wtp.demo.run_demo")
