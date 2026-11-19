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


@pytest.fixture
def local_counsellor(monkeypatch, tmp_path):
    """Deterministic Counsellor doubles for guardrail-pipeline tests; Azure boundaries are replaced in-process."""
    import counsellor_doubles

    counsellor_doubles.install(monkeypatch, tmp_path)


@pytest.fixture(scope="session")
def built_simulations(tmp_path_factory):
    """Data directory whose demo/simulations.json is projected from the sample personas, as the image build does."""
    import subprocess

    from governedgraph.common.config import DATA_DIR

    directory = tmp_path_factory.mktemp("sample-data")
    (directory / "demo").mkdir()
    result = subprocess.run(
        [sys.executable, str(DATA_DIR.parent / "demo" / "build_simulations.py"),
         "--output", str(directory / "demo" / "simulations.json")],
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT / "examples")])},
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return directory


@pytest.fixture
def sample_simulations(monkeypatch, built_simulations):
    """Point the simulation loader at the build-time projection instead of an image-only file."""
    from governedgraph.voice import simulations

    monkeypatch.setattr(simulations, "DATA_DIR", built_simulations)
    return built_simulations
