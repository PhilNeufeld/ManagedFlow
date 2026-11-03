"""
storage.py

JSON persistence for the retirement planning application.

Responsibilities:
- Create a new (empty) portfolio file.
- Save a Portfolio to JSON (human-readable, indented).
- Load a Portfolio from JSON.
- Validate JSON structure before it becomes a Portfolio object.
- Create timestamped backup copies before overwriting a file.
- Save/load simulation results as versioned snapshots under outputs/.

All file I/O for portfolio data should go through this module rather than
being scattered across app.py / simulation.py, so there is exactly one place
that understands the on-disk format.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from migrations import MigrationError, migrate
from models import Portfolio, SimulationResult, ValidationError


REQUIRED_TOP_LEVEL_KEYS = (
    "metadata",
    "household",
    "assumptions",
    "assets",
    "cashflows",
    "scenarios",
)


class StorageError(Exception):
    """Raised for any problem reading, writing, or validating portfolio files."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_portfolio_json(data: Dict[str, Any]) -> List[str]:
    """Validate the raw JSON dict structure before attempting to build a
    Portfolio object. Returns a list of human-readable problem descriptions;
    an empty list means the structure looks sound enough to attempt loading.

    This is intentionally a "structural smoke test", not full schema
    validation -- Portfolio.from_dict() / the dataclasses' __post_init__
    methods do the deeper validation (enum values, duplicate ids, etc.).
    """
    problems: List[str] = []

    if not isinstance(data, dict):
        return ["Top-level JSON must be an object/dict"]

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in data:
            problems.append(f"Missing required top-level key: '{key}'")

    if "assumptions" in data:
        assumptions = data["assumptions"]
        if not isinstance(assumptions, dict):
            problems.append("'assumptions' must be an object")
        else:
            for required in ("start_year", "end_year"):
                if required not in assumptions:
                    problems.append(f"'assumptions' is missing required key '{required}'")

    if "assets" in data and not isinstance(data["assets"], list):
        problems.append("'assets' must be a list")

    if "cashflows" in data and not isinstance(data["cashflows"], list):
        problems.append("'cashflows' must be a list")

    if "scenarios" in data and not isinstance(data["scenarios"], list):
        problems.append("'scenarios' must be a list")

    for i, asset in enumerate(data.get("assets", []) or []):
        if not isinstance(asset, dict):
            problems.append(f"assets[{i}] is not an object")
            continue
        for required in ("asset_id", "name", "asset_type", "owner", "initial_value", "start_year"):
            if required not in asset:
                problems.append(f"assets[{i}] is missing required key '{required}'")

    return problems


# ---------------------------------------------------------------------------
# Create / Load / Save
# ---------------------------------------------------------------------------

def create_new_portfolio_file(
    path: str | Path,
    household_name: str = "",
    start_year: int = 2026,
    end_year: int = 2055,
) -> Portfolio:
    """Create a brand-new, empty Portfolio and write it to `path`.

    Raises StorageError if a file already exists at `path` (use save_portfolio
    with overwrite=True, or delete the file first, if you really want to
    replace it).
    """
    path = Path(path)
    if path.exists():
        raise StorageError(
            f"Cannot create new portfolio: file already exists at {path}. "
            "Use load_portfolio() to open it, or choose a different path."
        )

    from models import Household, Assumptions  # local import avoids unused-import noise above

    portfolio = Portfolio(
        household=Household(name=household_name, members=[]),
        assumptions=Assumptions(start_year=start_year, end_year=end_year),
        assets=[],
        cashflows=[],
        scenarios=[],
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    save_portfolio(portfolio, path, make_backup=False)
    return portfolio


def save_portfolio(portfolio: Portfolio, path: str | Path, make_backup: bool = True) -> None:
    """Write a Portfolio to JSON at `path`, indented for human readability.

    If `make_backup` is True and a file already exists at `path`, a timestamped
    copy is written alongside it first (see backup_file()).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if make_backup and path.exists():
        backup_file(path)

    portfolio.updated_at = _now_iso()
    if not portfolio.created_at:
        portfolio.created_at = portfolio.updated_at

    data = portfolio.to_dict()

    # Validate our own output before writing -- a cheap sanity check that
    # catches programming errors in to_dict()/from_dict() round-tripping.
    problems = validate_portfolio_json(data)
    if problems:
        raise StorageError(
            "Refusing to save: portfolio failed structural validation:\n  - "
            + "\n  - ".join(problems)
        )

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp_path.replace(path)  # atomic on POSIX; avoids truncating the file on a crash mid-write


def load_portfolio(path: str | Path) -> Portfolio:
    """Load and validate a Portfolio from a JSON file.

    Raises StorageError if the file is missing, is not valid JSON, or fails
    structural validation. Raises models.ValidationError (re-raised as
    StorageError with context) if the data builds but fails model-level
    validation (e.g. duplicate asset ids, bad enum values).
    """
    path = Path(path)
    if not path.exists():
        raise StorageError(f"Portfolio file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise StorageError(f"Portfolio file is not valid JSON: {path} ({exc})") from exc

    # Older files are upgraded in memory to the current schema. The file on
    # disk is untouched until you save, and save_portfolio() backs up the
    # old version first.
    try:
        data = migrate(data)
    except MigrationError as exc:
        raise StorageError(f"Portfolio file can't be upgraded: {path} ({exc})") from exc

    problems = validate_portfolio_json(data)
    if problems:
        raise StorageError(
            f"Portfolio file failed validation: {path}\n  - " + "\n  - ".join(problems)
        )

    try:
        return Portfolio.from_dict(data)
    except ValidationError as exc:
        raise StorageError(f"Portfolio file failed model validation: {path} ({exc})") from exc


def backup_file(path: str | Path) -> Path:
    """Copy `path` to a timestamped backup file in the same directory.

    Returns the path to the backup file. No-op-safe: if `path` does not
    exist, raises StorageError rather than silently doing nothing, since a
    caller asking for a backup of a nonexistent file is almost always a bug.
    """
    path = Path(path)
    if not path.exists():
        raise StorageError(f"Cannot back up: file does not exist: {path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.stem}.backup_{timestamp}{path.suffix}")
    shutil.copy2(path, backup_path)
    return backup_path


# ---------------------------------------------------------------------------
# Simulation results (versioned snapshots)
# ---------------------------------------------------------------------------

def save_simulation_results(
    results: List[SimulationResult],
    outputs_dir: str | Path,
    year_label: int | str | None = None,
) -> Path:
    """Save one or more SimulationResult objects (e.g. one per scenario) as a
    single JSON snapshot under outputs_dir, named outputs/YYYY_simulation_results.json
    (or with a custom label).

    Returns the path written.
    """
    outputs_dir = Path(outputs_dir)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    label = year_label if year_label is not None else datetime.now().year
    out_path = outputs_dir / f"{label}_simulation_results.json"

    payload = {
        "generated_at": _now_iso(),
        "scenarios": [r.to_dict() for r in results],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return out_path


def load_simulation_results(path: str | Path) -> List[SimulationResult]:
    path = Path(path)
    if not path.exists():
        raise StorageError(f"Simulation results file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return [SimulationResult.from_dict(s) for s in data.get("scenarios", [])]
