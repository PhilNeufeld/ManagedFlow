"""The restructure must not change any numbers.

tests/fixtures/baseline_v0_1.json holds the yearly results the v1 engine
produced for the sample portfolio (made by tests/make_baseline.py BEFORE
any code changed). Every test here re-runs the simulation and demands the
same answers, to within rounding noise.
"""
import json

import pytest

import simulation
import storage
from tests.conftest import FIXTURES
from tests.make_baseline import summarize

BASELINE = json.loads((FIXTURES / "baseline_v0_1.json").read_text())


def _assert_matches_baseline(results):
    actual = summarize(results)
    assert actual.keys() == BASELINE.keys()
    for scenario_id, years in BASELINE.items():
        for expected, got in zip(years, actual[scenario_id]):
            for key, value in expected.items():
                assert got[key] == pytest.approx(value, rel=1e-9, abs=1e-6), (
                    f"{scenario_id} {expected['year']} {key}"
                )


def test_old_file_gives_same_results_after_migration():
    p = storage.load_portfolio(FIXTURES / "portfolio_v0_1.json")
    _assert_matches_baseline(simulation.run_all_scenarios(p))


def test_rebuilt_sample_gives_same_results(sample_portfolio):
    _assert_matches_baseline(simulation.run_all_scenarios(sample_portfolio))
