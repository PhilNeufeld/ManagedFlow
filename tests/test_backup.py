"""Backups: what's included, the zip, pruning, daily auto-backup, restore, tidy."""
import json
import zipfile
from datetime import date, datetime

import pytest

import backup


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "outputs" / "sub").mkdir(parents=True)
    (root / "data" / "household.json").write_text('{"a": 1}')
    (root / "data" / "household.backup_20260101_000000.json").write_text("old")
    (root / "data" / "actuals.csv").write_text("year,amount\n")
    (root / "data" / "build_household.py").write_text("print('hi')")
    (root / "data" / "household_tasks.md").write_text("- [ ] task")
    (root / "data" / "notes.txt").write_text("not private-pattern")
    (root / "outputs" / "sub" / "report.xlsx").write_bytes(b"xlsx")
    (root / "app.py").write_text("code is on GitHub")
    (root / "data" / "Archive" / "pre_restore_20260101_000000" / "data").mkdir(parents=True)
    (root / "data" / "Archive" / "household_2025-12-31_year-end.json").write_text("{}")
    (root / "data" / "Archive" / "household.backup_20251231_000000.json").write_text("{}")
    (root / "data" / "Archive" / "pre_restore_20260101_000000" / "data" / "household.json").write_text("{}")
    return root


def test_files_to_back_up_covers_private_files_only(project):
    names = [p.as_posix() for p in backup.files_to_back_up(project)]
    assert names == ["data/Archive/household_2025-12-31_year-end.json", "data/actuals.csv", "data/build_household.py", "data/household.json",
                     "data/household_tasks.md", "outputs/sub/report.xlsx"]


def test_make_backup_writes_dated_zip_with_manifest(project, tmp_path):
    dest = tmp_path / "Drive" / "ManagedFlow"
    z = backup.make_backup(dest, root=project, now=datetime(2026, 11, 6, 8, 30, 0))
    assert z.name == "ManagedFlow_backup_2026-11-06_083000.zip" and z.parent == dest
    with zipfile.ZipFile(z) as zf:
        manifest = json.loads(zf.read("MANIFEST.json"))
        assert zf.read("data/household.json") == b'{"a": 1}'
    assert "outputs/sub/report.xlsx" in manifest["files"]
    assert backup.backup_contents(z) == manifest["files"]


def test_keeps_only_the_newest(project, tmp_path):
    dest = tmp_path / "bk"
    for minute in range(5):
        backup.make_backup(dest, root=project, keep=3, now=datetime(2026, 11, 6, 8, minute))
    kept = [p.name for p in backup.list_backups(dest)]
    assert kept == [f"ManagedFlow_backup_2026-11-06_08{m:02d}00.zip" for m in (4, 3, 2)]


def test_no_folder_is_a_clear_error(project):
    with pytest.raises(backup.BackupError, match="No backup folder"):
        backup.make_backup("", root=project)


def test_auto_backup_runs_once_a_day(project, tmp_path):
    settings_path = tmp_path / "settings.json"
    s = backup.BackupSettings(folder=str(tmp_path / "bk"))
    first = backup.auto_backup_if_due(s, root=project, today=date(2026, 11, 6), settings_path=settings_path)
    assert first is not None and s.last_auto_backup == "2026-11-06"
    assert backup.load_settings(settings_path).last_auto_backup == "2026-11-06"
    assert backup.auto_backup_if_due(s, root=project, today=date(2026, 11, 6), settings_path=settings_path) is None
    assert backup.auto_backup_if_due(s, root=project, today=date(2026, 11, 7), settings_path=settings_path)
    s.auto_daily = False
    assert backup.auto_backup_if_due(s, root=project, today=date(2026, 11, 8), settings_path=settings_path) is None


def test_settings_roundtrip_and_defaults(tmp_path):
    p = tmp_path / ".managedflow" / "settings.json"
    assert backup.load_settings(p) == backup.BackupSettings()
    backup.save_settings(backup.BackupSettings(folder="X:/bk", keep=7), p)
    assert backup.load_settings(p).keep == 7 and backup.load_settings(p).folder == "X:/bk"


def test_restore_puts_files_back_and_keeps_current_copies(project, tmp_path):
    z = backup.make_backup(tmp_path / "bk", root=project, now=datetime(2026, 11, 6, 9, 0))
    (project / "data" / "household.json").write_text('{"a": 2}')       # changed since the backup
    aside = backup.restore_backup(z, root=project, now=datetime(2026, 11, 6, 10, 0))
    assert (project / "data" / "household.json").read_text() == '{"a": 1}'
    assert (aside / "data" / "household.json").read_text() == '{"a": 2}'
    assert aside == project / "data" / "Archive" / "pre_restore_20261106_100000"


def test_restore_refuses_unsafe_paths(project, tmp_path):
    evil = tmp_path / "ManagedFlow_backup_evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../outside.txt", "x")
    with pytest.raises(backup.BackupError, match="unexpected paths"):
        backup.restore_backup(evil, root=project)
    assert not (project.parent / "outside.txt").exists()


def test_tidy_moves_save_copies_to_archive(project):
    assert backup.tidy_local_backups(project) == 1
    assert (project / "data" / "Archive" / "household.backup_20260101_000000.json").exists()
    assert not list((project / "data").glob("*.backup_*.json"))


def test_a_windows_drive_path_is_never_created_elsewhere(project, tmp_path, monkeypatch):
    import os
    if os.name == "nt":
        pytest.skip("on Windows the drive is real")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(backup.BackupError, match="Windows drive path"):
        backup.make_backup(r"G:\My Drive\Backups", root=project)
    assert not any(p.name.startswith("G:") for p in tmp_path.iterdir())
