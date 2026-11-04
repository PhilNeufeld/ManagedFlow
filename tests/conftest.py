"""Shared pytest setup: lets tests `import models`, `import storage`, etc.
from the project root, and provides a few reusable fixtures."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))


@pytest.fixture
def v0_1_data():
    """The raw JSON dict of a portfolio saved by ManagedFlow v1 (schema 0.1)."""
    return json.loads((FIXTURES / "portfolio_v0_1.json").read_text(encoding="utf-8"))


@pytest.fixture
def sample_portfolio():
    import sample_data
    return sample_data.build_sample_portfolio()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "ui: drives the Streamlit screens headlessly (slower; skip with -m \"not ui\")"
    )
