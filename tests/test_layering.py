"""Layering: the framework core must not import the bundled sample (or any domain pack)."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "governedgraph"
SAMPLE = "pension_wtp"


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def test_framework_core_never_imports_the_sample_domain():
    offenders = sorted(
        f"{path.relative_to(ROOT)} imports {module}"
        for path in CORE.rglob("*.py")
        for module in _imported_modules(path)
        if module == SAMPLE or module.startswith(SAMPLE + ".")
    )
    assert offenders == []


def test_sample_reaches_the_core_only_through_configuration():
    from governedgraph.common import config

    assert not hasattr(config, "LOCAL_VALUES_PROVIDER")
    assert not hasattr(config, "TEXT_DEMO_MODULE")
    assert config.DATA_DIR.name == "data" and config.DATA_DIR.parent.name == SAMPLE
