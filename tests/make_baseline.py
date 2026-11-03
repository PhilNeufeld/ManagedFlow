"""One-time helper: record simulation results for the v0.1 sample file.

Run from the project root:  python tests/make_baseline.py
The output (tests/fixtures/baseline_v0_1.json) is the "before" picture that
test_regression.py compares every future version of the engine against.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import storage, simulation  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def summarize(results):
    out = {}
    for r in results:
        years = []
        for y in r.annual_results:
            years.append({
                "year": y.year,
                "total_gross_income": y.total_gross_income,
                "total_expenses": y.total_expenses,
                "total_estimated_tax": y.total_estimated_tax,
                "net_cashflow": y.net_cashflow,
                "withdrawal_amount": y.withdrawal_amount,
                "reinvestment_amount": y.reinvestment_amount,
                "total_net_worth": y.total_net_worth,
                "liquid_net_worth": y.liquid_net_worth,
                "asset_ending_values": {a.asset_id: a.ending_value for a in y.asset_results},
            })
        out[r.scenario_id] = years
    return out


if __name__ == "__main__":
    p = storage.load_portfolio(FIXTURES / "portfolio_v0_1.json")
    data = summarize(simulation.run_all_scenarios(p))
    (FIXTURES / "baseline_v0_1.json").write_text(json.dumps(data, indent=1))
    print("scenarios:", list(data), "years each:", len(next(iter(data.values()))))
