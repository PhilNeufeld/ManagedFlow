"""
backup.py

Off-machine backups of the PRIVATE files -- everything .gitignore keeps off GitHub
(the code itself is backed up on GitHub):

    data/*.json          your portfolio files (not the timestamped .backup_ copies)
    data/*.csv           actuals / forecast imports
    data/build_*.py      private builder scripts
    data/*_tasks.md      private task lists
    outputs/             reports, pro formas, the private manual, rental sheets
    data/Archive/*.json  snapshots you archived (not the automatic save copies)

make_backup() zips them into ONE dated file in a folder you choose -- ideally one a
sync service watches (Google Drive, OneDrive, Dropbox), so the copy leaves this
computer and gets the service's version history too. Only the newest `keep` zips
are kept. auto_backup_if_due() does it once a day (the app calls it when you save).

restore_backup() puts a zip back: your current files are first copied to
data/Archive/pre_restore_<time>/ so a restore can itself be undone.

tidy_local_backups() moves the data/*.backup_*.json copies that save_portfolio()
leaves behind into data/Archive/.

Settings (the folder, how many to keep, the last automatic backup) live in
.managedflow/settings.json -- per computer, never committed.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / ".managedflow" / "settings.json"
ZIP_PREFIX = "ManagedFlow_backup_"
DEFAULT_KEEP = 30


class BackupError(Exception):
    """A backup or restore could not be done (message says why, in plain words)."""


@dataclass
class BackupSettings:
    folder: str = ""                 # where the zips go; "" = backups not set up
    keep: int = DEFAULT_KEEP         # newest N zips kept in the folder
    auto_daily: bool = True          # back up automatically on the first save of the day
    last_auto_backup: str = ""       # ISO date of the last automatic backup


def load_settings(path: Path = SETTINGS_PATH) -> BackupSettings:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BackupSettings()
    known = {k: v for k, v in data.items() if k in BackupSettings.__dataclass_fields__}
    return BackupSettings(**known)


def save_settings(settings: BackupSettings, path: Path = SETTINGS_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")


def _is_local_backup(p: Path) -> bool:
    return ".backup_" in p.name


def files_to_back_up(root: Path = ROOT) -> List[Path]:
    """The private files, as paths relative to `root`, sorted."""
    root = Path(root)
    data, outputs = root / "data", root / "outputs"
    found = set()
    if data.is_dir():
        for pattern in ("*.json", "*.csv", "build_*.py", "*_tasks.md"):
            found.update(p for p in data.glob(pattern) if p.is_file() and not _is_local_backup(p))
        # Snapshots you archived yourself (not the automatic save copies or pre-restore sets)
        archive = data / "Archive"
        if archive.is_dir():
            found.update(p for p in archive.rglob("*.json") if p.is_file() and not _is_local_backup(p)
                         and not any(part.startswith("pre_restore_") for part in p.relative_to(archive).parts))
    if outputs.is_dir():
        found.update(p for p in outputs.rglob("*") if p.is_file())
    return sorted(p.relative_to(root) for p in found)


def _check_folder(folder: str | Path) -> Path:
    if not str(folder).strip():
        raise BackupError("No backup folder is set. Choose one in the sidebar under Backup.")
    folder = Path(folder)
    anchor = Path(folder.anchor) if folder.anchor else None
    if anchor is not None and not anchor.exists():
        raise BackupError(f"Drive {folder.anchor} isn't available. If it's Google Drive (or another sync "
                          "service), make sure it's running and signed in, then try again.")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"Can't create or open the backup folder {folder}: {exc}") from exc
    return folder


def list_backups(folder: str | Path) -> List[Path]:
    """Backup zips in `folder`, newest first."""
    folder = Path(folder)
    if not str(folder).strip() or not folder.is_dir():
        return []
    return sorted(folder.glob(f"{ZIP_PREFIX}*.zip"), key=lambda p: p.name, reverse=True)


def make_backup(folder: str | Path, root: Path = ROOT, keep: int = DEFAULT_KEEP,
                now: Optional[datetime] = None) -> Path:
    """Zip the private files into `folder`; keep only the newest `keep` zips. Returns the zip."""
    folder = _check_folder(folder)
    root = Path(root)
    files = files_to_back_up(root)
    if not files:
        raise BackupError("There are no private files to back up yet.")
    now = now or datetime.now()
    target = folder / f"{ZIP_PREFIX}{now:%Y-%m-%d_%H%M%S}.zip"
    tmp = target.with_suffix(".zip.tmp")
    manifest = {"created": now.isoformat(timespec="seconds"), "source": str(root),
                "files": [p.as_posix() for p in files]}
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for rel in files:
                z.write(root / rel, rel.as_posix())
            z.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
        tmp.replace(target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise BackupError(f"Backup failed while writing {target.name}: {exc}") from exc
    for old in list_backups(folder)[max(1, keep):]:
        try:
            old.unlink()
        except OSError:
            pass   # a sync service may hold a lock; it'll be pruned next time
    return target


def auto_backup_if_due(settings: BackupSettings, root: Path = ROOT, today: Optional[date] = None,
                       settings_path: Path = SETTINGS_PATH) -> Optional[Path]:
    """Back up once per day when automatic backups are on and a folder is set.
    Returns the zip written, or None when not due. Raises BackupError on failure."""
    today = today or date.today()
    if not settings.auto_daily or not settings.folder.strip() or settings.last_auto_backup == today.isoformat():
        return None
    zip_path = make_backup(settings.folder, root=root, keep=settings.keep)
    settings.last_auto_backup = today.isoformat()
    save_settings(settings, settings_path)
    return zip_path


def backup_contents(zip_path: str | Path) -> List[str]:
    """The files inside a backup zip (not counting the manifest)."""
    with zipfile.ZipFile(zip_path) as z:
        return [n for n in z.namelist() if n != "MANIFEST.json" and not n.endswith("/")]


def _safe_member(name: str) -> bool:
    p = Path(name)
    return (not p.is_absolute() and ".." not in p.parts and p.parts
            and p.parts[0] in ("data", "outputs"))


def restore_backup(zip_path: str | Path, root: Path = ROOT, now: Optional[datetime] = None) -> Path:
    """Restore a backup zip into `root`. Current private files are first copied to
    data/Archive/pre_restore_<time>/. Files that aren't in the zip are left alone.
    Returns the folder holding the pre-restore copies."""
    root = Path(root)
    now = now or datetime.now()
    names = backup_contents(zip_path)
    bad = [n for n in names if not _safe_member(n)]
    if bad:
        raise BackupError(f"Refusing to restore: unexpected paths in the zip ({', '.join(bad[:3])}).")
    aside = root / "data" / "Archive" / f"pre_restore_{now:%Y%m%d_%H%M%S}"
    for rel in files_to_back_up(root):
        dest = aside / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, dest)
    with zipfile.ZipFile(zip_path) as z:
        for name in names:
            dest = root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    return aside


def tidy_local_backups(root: Path = ROOT) -> int:
    """Move data/*.backup_*.json into data/Archive/. Returns how many were moved."""
    data = Path(root) / "data"
    archive = data / "Archive"
    moved = 0
    for p in sorted(data.glob("*.backup_*.json")):
        archive.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(archive / p.name))
        moved += 1
    return moved
