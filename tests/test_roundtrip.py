"""Saving and loading must not change anything."""
import storage
from models import Portfolio


def test_to_dict_from_dict_round_trip(sample_portfolio):
    first = sample_portfolio.to_dict()
    second = Portfolio.from_dict(first).to_dict()
    assert second == first


def test_save_then_load_round_trip(sample_portfolio, tmp_path):
    path = tmp_path / "portfolio.json"
    storage.save_portfolio(sample_portfolio, path, make_backup=False)
    loaded = storage.load_portfolio(path)
    assert loaded.to_dict() == sample_portfolio.to_dict()


def test_old_file_loads_and_saves_as_current_version(v0_1_data, tmp_path):
    import json
    from migrations import CURRENT_SCHEMA_VERSION

    old = tmp_path / "old.json"
    old.write_text(json.dumps(v0_1_data), encoding="utf-8")
    p = storage.load_portfolio(old)
    storage.save_portfolio(p, old)  # backup is made automatically

    saved = json.loads(old.read_text(encoding="utf-8"))
    assert saved["metadata"]["file_version"] == CURRENT_SCHEMA_VERSION
    backups = list(tmp_path.glob("old.backup_*.json"))
    assert len(backups) == 1  # the v0.1 original was kept
