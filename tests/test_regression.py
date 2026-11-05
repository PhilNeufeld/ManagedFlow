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
    # Scenarios compared in order: since v1.0 their keys are integers, not the baseline's text keys.
    assert len(actual) == len(BASELINE)
    for (scenario_id, years), got_years in zip(BASELINE.items(), actual.values()):
        for expected, got in zip(years, got_years):
            for key, value in expected.items():
                if isinstance(value, dict):   # keyed by asset: compare in asset order (keys renumbered in v1.0)
                    value, got = list(value.values()), {**got, key: list(got[key].values())}
                assert got[key] == pytest.approx(value, rel=1e-9, abs=1e-6), (
                    f"{scenario_id} {expected['year']} {key}"
                )


def test_old_file_gives_same_results_after_migration():
    p = storage.load_portfolio(FIXTURES / "portfolio_v0_1.json")
    _assert_matches_baseline(simulation.run_all_scenarios(p))


def test_rebuilt_sample_gives_same_results(sample_portfolio):
    _assert_matches_baseline(simulation.run_all_scenarios(sample_portfolio))
