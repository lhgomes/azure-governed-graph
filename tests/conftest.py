import os
import sys
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))
os.environ.setdefault("GOVERNEDGRAPH_DATA_DIR", str(ROOT / "examples/pension_wtp/data"))


@pytest.fixture
def isolated_conversations():
    """Explicit context-free double for tests unrelated to conversation storage."""
    @contextmanager
    def session(*args, **kwargs):
        yield SimpleNamespace(messages=(), prepare=lambda *_: None)
    return SimpleNamespace(session=session)
