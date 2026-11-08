"""Explicit pension extension; its offline entry points configure their own domain."""
import os
from pathlib import Path

if os.getenv("GOVERNEDGRAPH_MODE", "local") == "local":
    os.environ.setdefault("GOVERNEDGRAPH_DATA_DIR", str(Path(__file__).parent / "data"))
    os.environ.setdefault("GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER", f"{__name__}.member_values_api.data:get_member_values")
    os.environ.setdefault("GOVERNEDGRAPH_TEXT_DEMO_MODULE", f"{__name__}.demo.run_demo")