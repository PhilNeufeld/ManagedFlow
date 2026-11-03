"""
app.py

Streamlit local UI for the retirement planning application.

Run with:
    streamlit run app.py

This module is intentionally "thin" -- all real logic lives in models.py,
storage.py, simulation.py, exports.py, scenarios.py, and taxes.py. app.py
just wires user actions to those functions and renders results.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

import exports
import scenarios as scenario_engine
import simulation
import storage
from models import (
    Asset,
    AssetType,
    Assumptions,
    Bundle,
    Cashflow,
    CashflowDirection,
    Entity,
    EntityType,
    FlowType,
    Frequency,
    Household,
    LiquidityCategory,
    Portfolio,
    Scenario,
    TaxCategory,
    ValidationError,
    format_currency,
)

DATA_DIR = Path(__file__).parent / "data"
OUTPUTS_DIR = Path(__file__).parent / "outputs"
DEFAULT_PORTFOLIO_PATH = DATA_DIR / "portfolio.json"

st.set_page_config(page_title="ManagedFlow", layout="wide")


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

def _get_portfolio() -> Portfolio | None:
    return st.session_state.get("portfolio")


def _set_portfolio(portfolio: Portfolio, path: Path) -> None:
    st.session_state["portfolio"] = portfolio
    st.session_state["portfolio_path"] = path


def _get_portfolio_path() -> Path | None:
    return st.session_state.get("portfolio_path")


def _generate_id(prefix: str, name: str, existing_ids: list[str]) -> str:
    """Auto-generate a unique, human-readable id from a name, e.g.
    name="Rental Address 1" -> "asset_rental_address_1". Falls back to a
    numbered id (e.g. "asset_3") if the name is blank or the slug is
    somehow still taken. IDs are never typed by the user -- this keeps
    primary keys internal/stable and avoids collisions or stray
    characters breaking JSON/Excel/lookup keys downstream.
    """
    existing = set(existing_ids)
    slug = "".join(c if c.isalnum() else "_" for c in (name or "").strip().lower())
    slug = "_".join(filter(None, slug.split("_")))  # collapse repeated underscores
    base = f"{prefix}_{slug}" if slug else prefix

    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def _clear_simulation_cache() -> None:
    st.session_state.pop("simulation_results", None)


# ---------------------------------------------------------------------------
# Editor state helpers (shared Create/Read/Update/Delete pattern)
# ---------------------------------------------------------------------------
#
# Each editable list (Assets, Cashflows, Entities, Bundles, Scenarios) uses
# the same pattern, keyed by a short `kind` string (e.g. "asset"):
#
#   - selecting an existing record from the table puts the editor into
#     "editing record X" mode, pre-filled with that record's values
#   - clicking "Add new" puts the editor into "new record" mode, blank
#   - the form has Save and Cancel; Save validates and either updates the
#     existing record in place or appends a new one; Cancel discards
#     whatever was typed and closes the editor
#   - a separate Delete button (outside the form) removes the selected
#     record without going through field validation
#
# `_editor_generation[kind]` is bumped every time the editor is opened
# (new or edit) or closed, and is baked into every widget's `key`. This is
# what actually fixes the "stale value resubmitted" bug: Streamlit keeps a
# widget's value pinned to its key across reruns, so re-using the same key
# for a blank field after a successful save just redisplays the old value.
# Changing the key forces Streamlit to start that widget fresh.

def _editor_state_key(kind: str) -> str:
    return f"_editor_{kind}"


def _editor_generation_key(kind: str) -> str:
    return f"_editor_gen_{kind}"


def _editor_widget_key(kind: str, field: str) -> str:
    gen = st.session_state.get(_editor_generation_key(kind), 0)
    return f"_editor_{kind}_{field}_{gen}"


def _editor_open_new(kind: str) -> None:
    st.session_state[_editor_state_key(kind)] = {"mode": "new", "record_id": None}
    st.session_state[_editor_generation_key(kind)] = st.session_state.get(_editor_generation_key(kind), 0) + 1


def _editor_open_edit(kind: str, record_id: str) -> None:
    st.session_state[_editor_state_key(kind)] = {"mode": "edit", "record_id": record_id}
    st.session_state[_editor_generation_key(kind)] = st.session_state.get(_editor_generation_key(kind), 0) + 1


def _editor_close(kind: str) -> None:
    st.session_state.pop(_editor_state_key(kind), None)
    st.session_state[_editor_generation_key(kind)] = st.session_state.get(_editor_generation_key(kind), 0) + 1


def _editor_get(kind: str) -> dict | None:
    return st.session_state.get(_editor_state_key(kind))


def _render_selectable_table(
    kind: str,
    df: pd.DataFrame,
    record_ids: list[str],
    on_delete,
    delete_label: str = "this record",
) -> None:
    """Render `df` with single-row selection enabled. If a row is selected,
    show Edit / Delete buttons scoped to that exact row (matched by
    position, not by any displayed label -- this is what fixes selection
    becoming ambiguous when two records share a display name, e.g. two
    cashflows both named "Salary").

    `record_ids` must be the same length and order as `df`'s rows.
    `on_delete(record_id)` is called when Delete is confirmed; it should
    perform the deletion (including any guard/dependency checks) and
    return True on success, False (after showing its own st.error) on
    failure.
    """
    select_key = f"_table_select_{kind}"
    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=select_key,
    )

    selected_rows = event.selection.rows if event and event.selection else []
    if not selected_rows:
        return

    row_idx = selected_rows[0]
    if row_idx >= len(record_ids):
        return
    record_id = record_ids[row_idx]

    confirm_key = f"_confirm_delete_{kind}_{record_id}"

    col_edit, col_delete, col_spacer = st.columns([1, 1, 3])
    with col_edit:
        if st.button("✏️ Edit selected", key=f"_edit_btn_{kind}_{record_id}"):
            _editor_open_edit(kind, record_id)
            st.session_state.pop(confirm_key, None)
            st.rerun()
    with col_delete:
        if st.button("🗑️ Delete selected", key=f"_delete_btn_{kind}_{record_id}"):
            st.session_state[confirm_key] = True

    if st.session_state.get(confirm_key):
        st.warning(f"Delete {delete_label}? This can't be undone.")
        col_confirm, col_cancel = st.columns([1, 1])
        with col_confirm:
            if st.button("Yes, delete", key=f"_confirm_yes_{kind}_{record_id}", type="primary"):
                if on_delete(record_id):
                    st.session_state.pop(confirm_key, None)
                    if _editor_get(kind) and _editor_get(kind).get("record_id") == record_id:
                        _editor_close(kind)
                    st.rerun()
        with col_cancel:
            if st.button("Cancel", key=f"_confirm_no_{kind}_{record_id}"):
                st.session_state.pop(confirm_key, None)
                st.rerun()


# ---------------------------------------------------------------------------
# Sidebar: load / create / save
# ---------------------------------------------------------------------------

def render_sidebar() -> None:
    st.sidebar.title("Portfolio File")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing_files = sorted(DATA_DIR.glob("*.json"))
    file_labels = [f.name for f in existing_files]

    chosen = st.sidebar.selectbox(
        "Open an existing portfolio file", ["(none)"] + file_labels, index=0
    )
    if chosen != "(none)":
        if st.sidebar.button("Load selected file"):
            path = DATA_DIR / chosen
            try:
                portfolio = storage.load_portfolio(path)
                _set_portfolio(portfolio, path)
                _clear_simulation_cache()
                st.sidebar.success(f"Loaded {chosen}")
            except storage.StorageError as exc:
                st.sidebar.error(str(exc))

    st.sidebar.markdown("---")
    st.sidebar.subheader("Create new portfolio")
    new_name = st.sidebar.text_input("New file name", value="portfolio.json")
    new_household_name = st.sidebar.text_input("Household name", value="")
    new_start_year = st.sidebar.number_input("Start year", value=2026, step=1)
    new_end_year = st.sidebar.number_input("End year", value=2055, step=1)
    if st.sidebar.button("Create new portfolio file"):
        path = DATA_DIR / new_name
        try:
            portfolio = storage.create_new_portfolio_file(
                path,
                household_name=new_household_name,
                start_year=int(new_start_year),
                end_year=int(new_end_year),
            )
            _set_portfolio(portfolio, path)
            _clear_simulation_cache()
            st.sidebar.success(f"Created {new_name}")
        except storage.StorageError as exc:
            st.sidebar.error(str(exc))

    portfolio = _get_portfolio()
    path = _get_portfolio_path()
    if portfolio is not None and path is not None:
        st.sidebar.markdown("---")
        st.sidebar.write(f"**Current file:** `{path.name}`")
        if st.sidebar.button("Save portfolio"):
            try:
                storage.save_portfolio(portfolio, path)
                st.sidebar.success("Saved.")
            except storage.StorageError as exc:
                st.sidebar.error(str(exc))
        if st.sidebar.button("Save backup copy now"):
            try:
                backup_path = storage.backup_file(path)
                st.sidebar.success(f"Backup written: {backup_path.name}")
            except storage.StorageError as exc:
                st.sidebar.error(str(exc))


# ---------------------------------------------------------------------------
# Assets tab
# ---------------------------------------------------------------------------

def render_assets_tab(portfolio: Portfolio) -> None:
    st.header("Assets")

    kind = "asset"
    entity_name_by_id = {e.entity_id: e.name for e in portfolio.entities}

    if portfolio.assets:
        df = pd.DataFrame(
            [
                {
                    "Asset ID": a.asset_id,
                    "Name": a.name,
                    "Type": a.asset_type.value,
                    "Owner": a.owner,
                    "Entity": entity_name_by_id.get(a.entity_id, a.entity_id or ""),
                    "Current Value": format_currency(a.current_value),
                    "Growth Rate": a.annual_growth_rate,
                    "Liquidity": a.liquidity_category.value,
                    "Active": a.active,
                    "Do Not Sell": a.do_not_sell,
                }
                for a in portfolio.assets
            ]
        )
        st.caption("Click a row to select it, then Edit or Delete below.")

        def _delete_asset(asset_id: str) -> bool:
            name = next((a.name for a in portfolio.assets if a.asset_id == asset_id), asset_id)
            referencing_bundles = [b.name for b in portfolio.bundles if asset_id in b.asset_ids]
            if referencing_bundles:
                st.error(
                    f"Can't delete '{name}': it's referenced by Bundle(s) "
                    f"{', '.join(referencing_bundles)}. Remove it from those Bundles first."
                )
                return False
            portfolio.assets = [a for a in portfolio.assets if a.asset_id != asset_id]
            _clear_simulation_cache()
            st.success(f"Deleted asset '{name}'.")
            return True

        _render_selectable_table(
            kind, df, [a.asset_id for a in portfolio.assets], _delete_asset, delete_label="this asset",
        )
    else:
        st.info("No assets yet. Click 'Add new asset' below to create one.")

    if not portfolio.entities:
        st.caption(
            "💡 No Entities defined yet. Visit the Entities & Bundles tab first if "
            "you'd like to attribute assets to a holding structure (Personal, an LLC, "
            "a Trust, etc.) -- not required, but recommended."
        )

    if st.button("➕ Add new asset", key="asset_add_new_btn"):
        _editor_open_new(kind)
        st.rerun()

    editor = _editor_get(kind)
    if editor is not None:
        _render_asset_editor(portfolio, editor)


def _render_asset_editor(portfolio: Portfolio, editor: dict) -> None:
    kind = "asset"
    mode = editor["mode"]
    record_id = editor["record_id"]
    existing = portfolio.get_asset(record_id) if mode == "edit" else None

    if mode == "edit" and existing is None:
        st.warning("That asset no longer exists.")
        _editor_close(kind)
        return

    st.subheader(f"Editing '{existing.name}'" if mode == "edit" else "New asset")

    entity_options = ["(none)"] + [e.entity_id for e in portfolio.entities]
    entity_labels = {"(none)": "(none)"}
    entity_labels.update({e.entity_id: f"{e.name} ({e.entity_type.value})" for e in portfolio.entities})

    def field(name: str):
        return _editor_widget_key(kind, name)

    defaults = {
        "name": existing.name if existing else "",
        "asset_type": existing.asset_type.value if existing else AssetType.OTHER.value,
        "owner": existing.owner if existing else "",
        "entity_choice": existing.entity_id if existing and existing.entity_id else "(none)",
        "initial_value": existing.initial_value if existing else 0.0,
        "current_value": existing.current_value if existing else 0.0,
        "start_year": existing.start_year if existing else portfolio.assumptions.start_year,
        "growth_rate": existing.annual_growth_rate if existing else 0.0,
        "liquidity": existing.liquidity_category.value if existing else LiquidityCategory.LIQUID.value,
        "tax_category": existing.tax_category.value if existing else TaxCategory.NONE.value,
        "do_not_sell": existing.do_not_sell if existing else False,
        "active": existing.active if existing else True,
        "notes": existing.notes if existing else "",
    }

    with st.form(f"asset_editor_form_{mode}_{record_id}"):
        col1, col2, col3 = st.columns(3)
        with col1:
            name = st.text_input("Name", value=defaults["name"], key=field("name"))
            asset_type = st.selectbox(
                "Type", [t.value for t in AssetType],
                index=[t.value for t in AssetType].index(defaults["asset_type"]), key=field("asset_type"),
            )
            owner = st.text_input("Owner", value=defaults["owner"], key=field("owner"))
        with col2:
            entity_choice = st.selectbox(
                "Entity / holding structure", entity_options,
                index=entity_options.index(defaults["entity_choice"]) if defaults["entity_choice"] in entity_options else 0,
                format_func=lambda eid: entity_labels.get(eid, eid), key=field("entity_choice"),
            )
            initial_value = st.number_input(
                "Initial value ($) -- the simulation's starting point",
                min_value=0.0, step=1000.0, value=float(defaults["initial_value"]), key=field("initial_value"),
            )
            if mode == "edit":
                st.caption(f"Current value as of last simulation: {format_currency(defaults['current_value'])}")
        with col3:
            start_year = st.number_input(
                "Start year", value=int(defaults["start_year"]), step=1, key=field("start_year"),
            )
            growth_rate = st.number_input(
                "Annual growth rate", value=float(defaults["growth_rate"]), step=0.005, format="%.3f",
                key=field("growth_rate"),
            )
            liquidity = st.selectbox(
                "Liquidity category", [c.value for c in LiquidityCategory],
                index=[c.value for c in LiquidityCategory].index(defaults["liquidity"]), key=field("liquidity"),
            )

        tax_category = st.selectbox(
            "Tax category", [c.value for c in TaxCategory],
            index=[c.value for c in TaxCategory].index(defaults["tax_category"]), key=field("tax_category"),
        )
        do_not_sell = st.checkbox("Do not sell", value=defaults["do_not_sell"], key=field("do_not_sell"))
        active = st.checkbox("Active", value=defaults["active"], key=field("active"))
        notes = st.text_area("Notes", value=defaults["notes"], key=field("notes"))

        col_save, col_cancel = st.columns([1, 1])
        with col_save:
            save_clicked = st.form_submit_button("💾 Save", type="primary")
        with col_cancel:
            cancel_clicked = st.form_submit_button("Cancel")

    if cancel_clicked:
        _editor_close(kind)
        st.rerun()

    if save_clicked:
        if not name.strip():
            st.error("Name is required.")
        else:
            try:
                new_entity_id = None if entity_choice == "(none)" else entity_choice
                if mode == "new":
                    asset_id = _generate_id("asset", name, [a.asset_id for a in portfolio.assets])
                else:
                    asset_id = record_id

                asset = Asset(
                    asset_id=asset_id,
                    name=name,
                    asset_type=asset_type,
                    owner=owner,
                    entity_id=new_entity_id,
                    initial_value=initial_value,
                    current_value=initial_value if mode == "new" else existing.current_value,
                    start_year=int(start_year),
                    annual_growth_rate=growth_rate,
                    liquidity_category=liquidity,
                    tax_category=tax_category,
                    do_not_sell=do_not_sell,
                    active=active,
                    notes=notes,
                    cashflows=existing.cashflows if existing else [],
                )

                if mode == "new":
                    portfolio.add_asset(asset)
                    st.success(f"Added asset '{name}'.")
                else:
                    portfolio.replace_asset(asset_id, asset)
                    st.success(f"Saved changes to '{name}'.")

                _clear_simulation_cache()
                _editor_close(kind)
                st.rerun()
            except ValidationError as exc:
                st.error(str(exc))

    if mode == "edit":
        st.divider()
        referencing_bundles = [b.name for b in portfolio.bundles if record_id in b.asset_ids]
        if referencing_bundles:
            st.caption(
                f"⚠️ This asset is referenced by Bundle(s): {', '.join(referencing_bundles)}. "
                "Deleting it will require updating those Bundles afterward."
            )
        if existing.cashflows:
            st.caption(
                f"⚠️ This asset has {len(existing.cashflows)} attached cashflow(s) "
                f"({', '.join(cf.name for cf in existing.cashflows)}). Deleting the asset "
                "will delete those cashflows too."
            )
        confirm_key = f"_confirm_delete_asset_inline_{record_id}"
        if st.button("🗑️ Delete this asset", key=f"asset_delete_{record_id}"):
            st.session_state[confirm_key] = True
        if st.session_state.get(confirm_key):
            st.warning("Delete this asset? This can't be undone.")
            col_yes, col_no = st.columns([1, 1])
            with col_yes:
                if st.button("Yes, delete", key=f"asset_delete_confirm_{record_id}", type="primary"):
                    if referencing_bundles:
                        st.error(
                            f"Can't delete '{existing.name}': it's referenced by Bundle(s) "
                            f"{', '.join(referencing_bundles)}. Remove it from those Bundles first."
                        )
                    else:
                        portfolio.assets = [a for a in portfolio.assets if a.asset_id != record_id]
                        _clear_simulation_cache()
                        st.session_state.pop(confirm_key, None)
                        _editor_close(kind)
                        st.success(f"Deleted asset '{existing.name}'.")
                        st.rerun()
            with col_no:
                if st.button("Cancel", key=f"asset_delete_cancel_{record_id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()


# ---------------------------------------------------------------------------
# Cashflows tab
# ---------------------------------------------------------------------------

def render_cashflows_tab(portfolio: Portfolio) -> None:
    st.header("Cashflows")

    kind = "cashflow"
    all_flows = portfolio.all_cashflows()

    if all_flows:
        df = pd.DataFrame(
            [
                {
                    "Flow ID": cf.flow_id,
                    "Asset": cf.asset_id or "(household)",
                    "Name": cf.name,
                    "Type": cf.flow_type.value,
                    "Direction": cf.direction.value,
                    "Amount": format_currency(cf.amount),
                    "Frequency": cf.frequency.value,
                    "Start": cf.start_year,
                    "End": cf.end_year,
                    "Taxable": cf.taxable,
                }
                for cf in all_flows
            ]
        )
        st.caption("Click a row to select it, then Edit or Delete below.")

        def _delete_cashflow(flow_id: str) -> bool:
            name = next((cf.name for cf in all_flows if cf.flow_id == flow_id), flow_id)
            referencing_bundles = [b.name for b in portfolio.bundles if flow_id in b.standalone_cashflow_ids]
            if referencing_bundles:
                st.error(
                    f"Can't delete '{name}': it's referenced by Bundle(s) "
                    f"{', '.join(referencing_bundles)}. Remove it from those Bundles first."
                )
                return False
            portfolio.household_cashflows = [cf for cf in portfolio.household_cashflows if cf.flow_id != flow_id]
            for a in portfolio.assets:
                a.cashflows = [cf for cf in a.cashflows if cf.flow_id != flow_id]
            _clear_simulation_cache()
            st.success(f"Deleted cashflow '{name}'.")
            return True

        _render_selectable_table(
            kind, df, [cf.flow_id for cf in all_flows], _delete_cashflow, delete_label="this cashflow",
        )
    else:
        st.info("No cashflows yet. Click 'Add new cashflow' below to create one.")

    if st.button("➕ Add new cashflow", key="cashflow_add_new_btn"):
        _editor_open_new(kind)
        st.rerun()

    editor = _editor_get(kind)
    if editor is not None:
        _render_cashflow_editor(portfolio, editor)


def _render_cashflow_editor(portfolio: Portfolio, editor: dict) -> None:
    kind = "cashflow"
    mode = editor["mode"]
    record_id = editor["record_id"]
    existing = portfolio.get_cashflow(record_id) if mode == "edit" else None

    if mode == "edit" and existing is None:
        st.warning("That cashflow no longer exists.")
        _editor_close(kind)
        return

    st.subheader(f"Editing '{existing.name}'" if mode == "edit" else "New cashflow")

    asset_options = ["(household-level, no asset)"] + [a.asset_id for a in portfolio.assets]
    asset_labels = {"(household-level, no asset)": "(household-level, no asset)"}
    asset_labels.update({a.asset_id: a.name for a in portfolio.assets})

    def field(name: str):
        return _editor_widget_key(kind, name)

    defaults = {
        "name": existing.name if existing else "",
        "asset_choice": (existing.asset_id if existing and existing.asset_id else "(household-level, no asset)"),
        "flow_type": existing.flow_type.value if existing else FlowType.OTHER.value,
        "direction": existing.direction.value if existing else CashflowDirection.INFLOW.value,
        "amount": existing.amount if existing else 0.0,
        "frequency": existing.frequency.value if existing else Frequency.ANNUAL.value,
        "start_year": existing.start_year if existing else portfolio.assumptions.start_year,
        "end_year": str(existing.end_year) if existing and existing.end_year is not None else "",
        "growth_rate": existing.annual_growth_rate if existing else 0.0,
        "taxable": existing.taxable if existing else True,
        "tax_category": existing.tax_category.value if existing else TaxCategory.NONE.value,
        "affects_asset_value": existing.affects_asset_value if existing else False,
        "affects_debt_balance": existing.affects_debt_balance if existing else False,
        "inflation_adjusted": existing.inflation_adjusted if existing else False,
        "notes": existing.notes if existing else "",
    }

    with st.form(f"cashflow_editor_form_{mode}_{record_id}"):
        col1, col2, col3 = st.columns(3)
        with col1:
            name = st.text_input("Name", value=defaults["name"], key=field("name"))
            asset_choice = st.selectbox(
                "Attach to asset", asset_options,
                index=asset_options.index(defaults["asset_choice"]) if defaults["asset_choice"] in asset_options else 0,
                format_func=lambda aid: asset_labels.get(aid, aid), key=field("asset_choice"),
            )
            flow_type = st.selectbox(
                "Flow type", [t.value for t in FlowType],
                index=[t.value for t in FlowType].index(defaults["flow_type"]), key=field("flow_type"),
            )
        with col2:
            direction = st.selectbox(
                "Direction", [d.value for d in CashflowDirection],
                index=[d.value for d in CashflowDirection].index(defaults["direction"]), key=field("direction"),
            )
            amount = st.number_input(
                "Amount (per period)", min_value=0.0, step=100.0, value=float(defaults["amount"]), key=field("amount"),
            )
            frequency = st.selectbox(
                "Frequency", [f.value for f in Frequency],
                index=[f.value for f in Frequency].index(defaults["frequency"]), key=field("frequency"),
            )
        with col3:
            start_year = st.number_input(
                "Start year", value=int(defaults["start_year"]), step=1, key=field("start_year"),
            )
            end_year_input = st.text_input(
                "End year, e.g. 2032 (blank = no end)", value=defaults["end_year"], key=field("end_year"),
            )

        col4, col5, col6 = st.columns(3)
        with col4:
            growth_rate = st.number_input(
                "Annual growth rate", value=float(defaults["growth_rate"]), step=0.005, format="%.3f",
                key=field("growth_rate"),
            )
        with col5:
            taxable = st.checkbox("Taxable", value=defaults["taxable"], key=field("taxable"))
            tax_category = st.selectbox(
                "Tax category", [c.value for c in TaxCategory],
                index=[c.value for c in TaxCategory].index(defaults["tax_category"]), key=field("tax_category"),
            )
        with col6:
            affects_asset_value = st.checkbox(
                "Affects asset value", value=defaults["affects_asset_value"], key=field("affects_asset_value"),
            )
            affects_debt_balance = st.checkbox(
                "Affects debt balance", value=defaults["affects_debt_balance"], key=field("affects_debt_balance"),
            )

        inflation_adjusted = st.checkbox(
            "Inflation adjusted", value=defaults["inflation_adjusted"], key=field("inflation_adjusted"),
        )
        notes = st.text_area("Notes", value=defaults["notes"], key=field("notes"))

        col_save, col_cancel = st.columns([1, 1])
        with col_save:
            save_clicked = st.form_submit_button("💾 Save", type="primary")
        with col_cancel:
            cancel_clicked = st.form_submit_button("Cancel")

    if cancel_clicked:
        _editor_close(kind)
        st.rerun()

    if save_clicked:
        end_year_str = end_year_input.strip()
        if not name.strip():
            st.error("Name is required.")
        elif end_year_str and not end_year_str.isdigit():
            st.error(
                f"End year must be a plain 4-digit year like '2032', not '{end_year_str}'. "
                "Leave it blank if this cashflow has no end."
            )
        else:
            try:
                end_year = int(end_year_str) if end_year_str else None
                new_asset_id = None if asset_choice == "(household-level, no asset)" else asset_choice

                if mode == "new":
                    existing_flow_ids = [cf.flow_id for cf in portfolio.all_cashflows()]
                    flow_id = _generate_id("flow", name, existing_flow_ids)
                else:
                    flow_id = record_id

                cashflow = Cashflow(
                    flow_id=flow_id,
                    asset_id=new_asset_id,
                    name=name,
                    flow_type=flow_type,
                    direction=direction,
                    amount=amount,
                    start_year=int(start_year),
                    end_year=end_year,
                    frequency=frequency,
                    annual_growth_rate=growth_rate,
                    inflation_adjusted=inflation_adjusted,
                    taxable=taxable,
                    tax_category=tax_category,
                    affects_asset_value=affects_asset_value,
                    affects_debt_balance=affects_debt_balance,
                    notes=notes,
                )

                if mode == "new":
                    if new_asset_id is None:
                        portfolio.add_household_cashflow(cashflow)
                    else:
                        portfolio.get_asset(new_asset_id).add_cashflow(cashflow)
                    st.success(f"Added cashflow '{name}'.")
                else:
                    old_asset_id = existing.asset_id
                    if old_asset_id == new_asset_id:
                        portfolio.replace_cashflow(flow_id, cashflow)
                    else:
                        # Moved between household-level and an asset (or
                        # between two assets) -- remove from the old
                        # container, add to the new one.
                        if old_asset_id is None:
                            portfolio.household_cashflows = [
                                cf for cf in portfolio.household_cashflows if cf.flow_id != flow_id
                            ]
                        else:
                            old_asset = portfolio.get_asset(old_asset_id)
                            if old_asset:
                                old_asset.cashflows = [cf for cf in old_asset.cashflows if cf.flow_id != flow_id]
                        if new_asset_id is None:
                            portfolio.add_household_cashflow(cashflow)
                        else:
                            portfolio.get_asset(new_asset_id).add_cashflow(cashflow)
                    st.success(f"Saved changes to '{name}'.")

                _clear_simulation_cache()
                _editor_close(kind)
                st.rerun()
            except ValidationError as exc:
                st.error(str(exc))

    if mode == "edit":
        st.divider()
        referencing_bundles = [b.name for b in portfolio.bundles if record_id in b.standalone_cashflow_ids]
        if referencing_bundles:
            st.caption(
                f"⚠️ This cashflow is referenced by Bundle(s): {', '.join(referencing_bundles)}. "
                "Deleting it will require updating those Bundles afterward."
            )
        confirm_key = f"_confirm_delete_cashflow_inline_{record_id}"
        if st.button("🗑️ Delete this cashflow", key=f"cashflow_delete_{record_id}"):
            st.session_state[confirm_key] = True
        if st.session_state.get(confirm_key):
            st.warning("Delete this cashflow? This can't be undone.")
            col_yes, col_no = st.columns([1, 1])
            with col_yes:
                if st.button("Yes, delete", key=f"cashflow_delete_confirm_{record_id}", type="primary"):
                    if referencing_bundles:
                        st.error(
                            f"Can't delete '{existing.name}': it's referenced by Bundle(s) "
                            f"{', '.join(referencing_bundles)}. Remove it from those Bundles first."
                        )
                    else:
                        portfolio.household_cashflows = [
                            cf for cf in portfolio.household_cashflows if cf.flow_id != record_id
                        ]
                        for a in portfolio.assets:
                            a.cashflows = [cf for cf in a.cashflows if cf.flow_id != record_id]
                        _clear_simulation_cache()
                        st.session_state.pop(confirm_key, None)
                        _editor_close(kind)
                        st.success(f"Deleted cashflow '{existing.name}'.")
                        st.rerun()
            with col_no:
                if st.button("Cancel", key=f"cashflow_delete_cancel_{record_id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()


# ---------------------------------------------------------------------------
# Entities & Bundles tab
# ---------------------------------------------------------------------------

def render_entities_bundles_tab(portfolio: Portfolio) -> None:
    st.header("Entities & Bundles")
    st.caption(
        "An Entity is a legal/tax holding structure (Personal, an LLC, a Trust, "
        "etc.). A Bundle groups related Assets and/or household Cashflows under "
        "an Entity -- e.g. \"Rental Address 1\" (a property plus its loan, rent, "
        "and expenses) or \"Spouse A Earnings\" (just a salary cashflow, no asset). "
        "Scenarios can be scoped to run against just one Entity or one Bundle."
    )

    # --- Entities -----------------------------------------------------------
    st.subheader("Entities")
    entity_kind = "entity"

    if portfolio.entities:
        df = pd.DataFrame(
            [
                {"Entity ID": e.entity_id, "Name": e.name, "Type": e.entity_type.value, "Notes": e.notes}
                for e in portfolio.entities
            ]
        )
        st.caption("Click a row to select it, then Edit or Delete below.")

        def _delete_entity(entity_id: str) -> bool:
            name = next((e.name for e in portfolio.entities if e.entity_id == entity_id), entity_id)
            referencing_bundles = [b.name for b in portfolio.bundles if b.entity_id == entity_id]
            referencing_assets = [a.name for a in portfolio.assets if a.entity_id == entity_id]
            blockers = referencing_bundles + referencing_assets
            if blockers:
                st.error(
                    f"Can't delete '{name}': it's still referenced by "
                    f"{', '.join(blockers)}. Reassign or remove those first."
                )
                return False
            portfolio.entities = [e for e in portfolio.entities if e.entity_id != entity_id]
            st.success(f"Deleted entity '{name}'.")
            return True

        _render_selectable_table(
            entity_kind, df, [e.entity_id for e in portfolio.entities], _delete_entity, delete_label="this entity",
        )
    else:
        st.info("No entities yet. Click 'Add new entity' below -- you'll need at least one before adding a Bundle.")

    if st.button("➕ Add new entity", key="entity_add_new_btn"):
        _editor_open_new(entity_kind)
        st.rerun()

    editor = _editor_get(entity_kind)
    if editor is not None:
        _render_entity_editor(portfolio, editor)

    st.divider()

    # --- Bundles --------------------------------------------------------------
    st.subheader("Bundles")
    bundle_kind = "bundle"
    entity_name_lookup = {e.entity_id: e.name for e in portfolio.entities}
    asset_name_by_id = {a.asset_id: a.name for a in portfolio.assets}
    flow_name_by_id = {cf.flow_id: cf.name for cf in portfolio.household_cashflows}

    if portfolio.bundles:
        df = pd.DataFrame(
            [
                {
                    "Bundle ID": b.bundle_id,
                    "Name": b.name,
                    "Entity": entity_name_lookup.get(b.entity_id, b.entity_id),
                    "Assets": ", ".join(asset_name_by_id.get(aid, aid) for aid in b.asset_ids),
                    "Standalone Cashflows": ", ".join(
                        flow_name_by_id.get(fid, fid) for fid in b.standalone_cashflow_ids
                    ),
                }
                for b in portfolio.bundles
            ]
        )
        st.caption("Click a row to select it, then Edit or Delete below.")

        def _delete_bundle(bundle_id: str) -> bool:
            name = next((b.name for b in portfolio.bundles if b.bundle_id == bundle_id), bundle_id)
            referencing_scenarios = [s.name for s in portfolio.scenarios if bundle_id in s.bundle_ids]
            if referencing_scenarios:
                st.error(
                    f"Can't delete '{name}': it's targeted directly by "
                    f"Scenario(s) {', '.join(referencing_scenarios)}. Update those scenarios' scope first."
                )
                return False
            portfolio.bundles = [b for b in portfolio.bundles if b.bundle_id != bundle_id]
            st.success(f"Deleted bundle '{name}'.")
            return True

        _render_selectable_table(
            bundle_kind, df, [b.bundle_id for b in portfolio.bundles], _delete_bundle, delete_label="this bundle",
        )
    else:
        st.info("No bundles yet. Click 'Add new bundle' below to create one.")

    if not portfolio.entities:
        st.caption("💡 Add at least one Entity above before creating a Bundle.")
    else:
        if st.button("➕ Add new bundle", key="bundle_add_new_btn"):
            _editor_open_new(bundle_kind)
            st.rerun()

        editor = _editor_get(bundle_kind)
        if editor is not None:
            _render_bundle_editor(portfolio, editor)


def _render_entity_editor(portfolio: Portfolio, editor: dict) -> None:
    kind = "entity"
    mode = editor["mode"]
    record_id = editor["record_id"]
    existing = portfolio.get_entity(record_id) if mode == "edit" else None

    if mode == "edit" and existing is None:
        st.warning("That entity no longer exists.")
        _editor_close(kind)
        return

    st.markdown(f"**Editing '{existing.name}'**" if mode == "edit" else "**New entity**")

    def field(name: str):
        return _editor_widget_key(kind, name)

    defaults = {
        "name": existing.name if existing else "",
        "entity_type": existing.entity_type.value if existing else EntityType.PERSONAL.value,
        "notes": existing.notes if existing else "",
    }

    with st.form(f"entity_editor_form_{mode}_{record_id}"):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Name", value=defaults["name"], key=field("name"))
            entity_type = st.selectbox(
                "Entity type", [t.value for t in EntityType],
                index=[t.value for t in EntityType].index(defaults["entity_type"]), key=field("entity_type"),
            )
        with col2:
            notes = st.text_area("Notes", value=defaults["notes"], key=field("notes"))

        col_save, col_cancel = st.columns([1, 1])
        with col_save:
            save_clicked = st.form_submit_button("💾 Save", type="primary")
        with col_cancel:
            cancel_clicked = st.form_submit_button("Cancel")

    if cancel_clicked:
        _editor_close(kind)
        st.rerun()

    if save_clicked:
        if not name.strip():
            st.error("Name is required.")
        else:
            try:
                if mode == "new":
                    entity_id = _generate_id("entity", name, [e.entity_id for e in portfolio.entities])
                    entity = Entity(entity_id=entity_id, name=name, entity_type=entity_type, notes=notes)
                    portfolio.add_entity(entity)
                    st.success(f"Added entity '{name}'.")
                else:
                    entity = Entity(entity_id=record_id, name=name, entity_type=entity_type, notes=notes)
                    portfolio.replace_entity(record_id, entity)
                    st.success(f"Saved changes to '{name}'.")

                _editor_close(kind)
                st.rerun()
            except ValidationError as exc:
                st.error(str(exc))

    if mode == "edit":
        st.divider()
        referencing_bundles = [b.name for b in portfolio.bundles if b.entity_id == record_id]
        referencing_assets = [a.name for a in portfolio.assets if a.entity_id == record_id]
        blockers = referencing_bundles + referencing_assets
        if blockers:
            st.caption(f"⚠️ Still referenced by: {', '.join(blockers)}. These must be reassigned or removed first.")
        confirm_key = f"_confirm_delete_entity_inline_{record_id}"
        if st.button("🗑️ Delete this entity", key=f"entity_delete_{record_id}"):
            st.session_state[confirm_key] = True
        if st.session_state.get(confirm_key):
            st.warning("Delete this entity? This can't be undone.")
            col_yes, col_no = st.columns([1, 1])
            with col_yes:
                if st.button("Yes, delete", key=f"entity_delete_confirm_{record_id}", type="primary"):
                    if blockers:
                        st.error(
                            f"Can't delete '{existing.name}': it's still referenced by "
                            f"{', '.join(blockers)}. Reassign or remove those first."
                        )
                    else:
                        portfolio.entities = [e for e in portfolio.entities if e.entity_id != record_id]
                        st.session_state.pop(confirm_key, None)
                        _editor_close(kind)
                        st.success(f"Deleted entity '{existing.name}'.")
                        st.rerun()
            with col_no:
                if st.button("Cancel", key=f"entity_delete_cancel_{record_id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()


def _render_bundle_editor(portfolio: Portfolio, editor: dict) -> None:
    kind = "bundle"
    mode = editor["mode"]
    record_id = editor["record_id"]
    existing = portfolio.get_bundle(record_id) if mode == "edit" else None

    if mode == "edit" and existing is None:
        st.warning("That bundle no longer exists.")
        _editor_close(kind)
        return

    st.markdown(f"**Editing '{existing.name}'**" if mode == "edit" else "**New bundle**")

    entity_name_by_id = {e.entity_id: e.name for e in portfolio.entities}
    asset_name_by_id = {a.asset_id: a.name for a in portfolio.assets}
    flow_name_by_id = {cf.flow_id: cf.name for cf in portfolio.household_cashflows}
    entity_ids = [e.entity_id for e in portfolio.entities]

    def field(name: str):
        return _editor_widget_key(kind, name)

    defaults = {
        "name": existing.name if existing else "",
        "entity_choice": existing.entity_id if existing else (entity_ids[0] if entity_ids else None),
        "asset_ids": list(existing.asset_ids) if existing else [],
        "flow_ids": list(existing.standalone_cashflow_ids) if existing else [],
        "notes": existing.notes if existing else "",
    }

    with st.form(f"bundle_editor_form_{mode}_{record_id}"):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Name", value=defaults["name"], key=field("name"))
            entity_choice = st.selectbox(
                "Entity", entity_ids,
                index=entity_ids.index(defaults["entity_choice"]) if defaults["entity_choice"] in entity_ids else 0,
                format_func=lambda eid: entity_name_by_id.get(eid, eid), key=field("entity_choice"),
            )
        with col2:
            selected_assets = st.multiselect(
                "Assets in this bundle",
                options=[a.asset_id for a in portfolio.assets],
                default=[aid for aid in defaults["asset_ids"] if aid in asset_name_by_id],
                format_func=lambda aid: asset_name_by_id.get(aid, aid), key=field("asset_ids"),
            )
            selected_flows = st.multiselect(
                "Household cashflows in this bundle (asset-level cashflows "
                "travel automatically with their asset)",
                options=[cf.flow_id for cf in portfolio.household_cashflows],
                default=[fid for fid in defaults["flow_ids"] if fid in flow_name_by_id],
                format_func=lambda fid: flow_name_by_id.get(fid, fid), key=field("flow_ids"),
            )
        notes = st.text_area("Notes", value=defaults["notes"], key=field("notes"))

        col_save, col_cancel = st.columns([1, 1])
        with col_save:
            save_clicked = st.form_submit_button("💾 Save", type="primary")
        with col_cancel:
            cancel_clicked = st.form_submit_button("Cancel")

    if cancel_clicked:
        _editor_close(kind)
        st.rerun()

    if save_clicked:
        if not name.strip():
            st.error("Name is required.")
        else:
            try:
                if mode == "new":
                    bundle_id = _generate_id("bundle", name, [b.bundle_id for b in portfolio.bundles])
                else:
                    bundle_id = record_id

                bundle = Bundle(
                    bundle_id=bundle_id,
                    name=name,
                    entity_id=entity_choice,
                    asset_ids=list(selected_assets),
                    standalone_cashflow_ids=list(selected_flows),
                    notes=notes,
                )

                if mode == "new":
                    portfolio.add_bundle(bundle)
                    st.success(f"Added bundle '{name}'.")
                else:
                    portfolio.replace_bundle(bundle_id, bundle)
                    st.success(f"Saved changes to '{name}'.")

                _editor_close(kind)
                st.rerun()
            except ValidationError as exc:
                st.error(str(exc))

    if mode == "edit":
        st.divider()
        referencing_scenarios = [s.name for s in portfolio.scenarios if record_id in s.bundle_ids]
        if referencing_scenarios:
            st.caption(
                f"⚠️ This bundle is targeted directly by Scenario(s): {', '.join(referencing_scenarios)}. "
                "Deleting it will require updating those scenarios' scope afterward."
            )
        confirm_key = f"_confirm_delete_bundle_inline_{record_id}"
        if st.button("🗑️ Delete this bundle", key=f"bundle_delete_{record_id}"):
            st.session_state[confirm_key] = True
        if st.session_state.get(confirm_key):
            st.warning("Delete this bundle? This can't be undone.")
            col_yes, col_no = st.columns([1, 1])
            with col_yes:
                if st.button("Yes, delete", key=f"bundle_delete_confirm_{record_id}", type="primary"):
                    if referencing_scenarios:
                        st.error(
                            f"Can't delete '{existing.name}': it's targeted directly by "
                            f"Scenario(s) {', '.join(referencing_scenarios)}. Update those scenarios' scope first."
                        )
                    else:
                        portfolio.bundles = [b for b in portfolio.bundles if b.bundle_id != record_id]
                        st.session_state.pop(confirm_key, None)
                        _editor_close(kind)
                        st.success(f"Deleted bundle '{existing.name}'.")
                        st.rerun()
            with col_no:
                if st.button("Cancel", key=f"bundle_delete_cancel_{record_id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()


# ---------------------------------------------------------------------------
# Assumptions tab
# ---------------------------------------------------------------------------

def render_assumptions_tab(portfolio: Portfolio) -> None:
    st.header("Assumptions")
    a = portfolio.assumptions

    col1, col2, col3 = st.columns(3)
    with col1:
        start_year = st.number_input("Start year", value=a.start_year, step=1)
    with col2:
        end_year = st.number_input("End year", value=a.end_year, step=1)
    with col3:
        inflation_rate = st.number_input("Inflation rate", value=a.inflation_rate, step=0.005, format="%.3f")

    if st.button("Update assumptions"):
        try:
            updated = Assumptions(
                start_year=int(start_year),
                end_year=int(end_year),
                inflation_rate=inflation_rate,
                default_tax_rates=dict(a.default_tax_rates),
            )
            portfolio.assumptions = updated
            _clear_simulation_cache()
            st.success("Assumptions updated.")
        except ValidationError as exc:
            st.error(str(exc))

    st.subheader("Default tax rates by category")
    rate_df = pd.DataFrame(
        [{"Tax category": k, "Rate": v} for k, v in sorted(portfolio.assumptions.default_tax_rates.items())]
    )
    edited = st.data_editor(rate_df, use_container_width=True, num_rows="fixed", key="tax_rate_editor")
    if st.button("Save tax rates"):
        for _, row in edited.iterrows():
            portfolio.assumptions.default_tax_rates[row["Tax category"]] = float(row["Rate"])
        _clear_simulation_cache()
        st.success("Tax rates updated.")


# ---------------------------------------------------------------------------
# Scenarios tab
# ---------------------------------------------------------------------------

def render_scenarios_tab(portfolio: Portfolio) -> None:
    st.header("Scenarios")

    kind = "scenario"
    entity_name_by_id = {e.entity_id: e.name for e in portfolio.entities}
    bundle_name_by_id = {b.bundle_id: b.name for b in portfolio.bundles}
    scenario_name_lookup = {s.scenario_id: s.name for s in portfolio.scenarios}

    def _scope_label(s: Scenario) -> str:
        if not s.entity_ids and not s.bundle_ids:
            return "Full Portfolio"
        parts = []
        if s.entity_ids:
            parts.append("Entities: " + ", ".join(entity_name_by_id.get(e, e) for e in s.entity_ids))
        if s.bundle_ids:
            parts.append("Bundles: " + ", ".join(bundle_name_by_id.get(b, b) for b in s.bundle_ids))
        return " | ".join(parts)

    if portfolio.scenarios:
        df = pd.DataFrame(
            [
                {
                    "Scenario ID": s.scenario_id,
                    "Name": s.name,
                    "Base case?": s.is_base_case,
                    "Scope": _scope_label(s),
                    "Inflation override": s.inflation_rate_override,
                    "Asset overrides": len(s.asset_overrides),
                    "Cashflow overrides": len(s.cashflow_overrides),
                }
                for s in portfolio.scenarios
            ]
        )
        st.caption("Click a row to select it, then Edit or Delete below.")

        def _delete_scenario(scenario_id: str) -> bool:
            name = scenario_name_lookup.get(scenario_id, scenario_id)
            portfolio.scenarios = [s for s in portfolio.scenarios if s.scenario_id != scenario_id]
            _clear_simulation_cache()
            st.success(f"Deleted scenario '{name}'.")
            return True

        _render_selectable_table(
            kind, df, [s.scenario_id for s in portfolio.scenarios], _delete_scenario, delete_label="this scenario",
        )
    else:
        st.info("No scenarios defined yet. A 'Base Case' with no overrides will be used by default.")

    if st.button("➕ Add new scenario", key="scenario_add_new_btn"):
        _editor_open_new(kind)
        st.rerun()

    editor = _editor_get(kind)
    if editor is not None:
        _render_scenario_editor(portfolio, editor)

    if portfolio.scenarios and portfolio.assets:
        with st.expander("Add an asset override to an existing scenario"):
            scenario_ids = [s.scenario_id for s in portfolio.scenarios]
            target_scenario_id = st.selectbox(
                "Scenario", scenario_ids, key="override_scenario_select",
                format_func=lambda sid: scenario_name_lookup.get(sid, sid),
            )
            asset_ids = [a.asset_id for a in portfolio.assets]
            asset_name_lookup = {a.asset_id: a.name for a in portfolio.assets}
            target_asset_id = st.selectbox(
                "Asset", asset_ids, key="override_asset_select",
                format_func=lambda aid: asset_name_lookup.get(aid, aid),
            )
            override_key = st.selectbox(
                "Field to override",
                list(scenario_engine.SUPPORTED_ASSET_OVERRIDE_KEYS),
            )
            override_value_str = st.text_input("New value")
            if st.button("Add override"):
                target_scenario = portfolio.get_scenario(target_scenario_id)
                parsed_value = _parse_override_value(override_value_str)
                target_scenario.asset_overrides.setdefault(target_asset_id, {})[override_key] = parsed_value
                _clear_simulation_cache()
                st.success(
                    f"Set {override_key}={parsed_value} for asset '{asset_name_lookup.get(target_asset_id, target_asset_id)}' "
                    f"in scenario '{scenario_name_lookup.get(target_scenario_id, target_scenario_id)}'."
                )


def _render_scenario_editor(portfolio: Portfolio, editor: dict) -> None:
    kind = "scenario"
    mode = editor["mode"]
    record_id = editor["record_id"]
    existing = portfolio.get_scenario(record_id) if mode == "edit" else None

    if mode == "edit" and existing is None:
        st.warning("That scenario no longer exists.")
        _editor_close(kind)
        return

    st.subheader(f"Editing '{existing.name}'" if mode == "edit" else "New scenario")

    entity_ids = [e.entity_id for e in portfolio.entities]
    entity_name_by_id = {e.entity_id: e.name for e in portfolio.entities}
    bundle_ids = [b.bundle_id for b in portfolio.bundles]
    bundle_name_by_id = {b.bundle_id: b.name for b in portfolio.bundles}

    def field(name: str):
        return _editor_widget_key(kind, name)

    defaults = {
        "name": existing.name if existing else "",
        "description": existing.description if existing else "",
        "inflation_override": (
            "" if not existing or existing.inflation_rate_override is None
            else str(existing.inflation_rate_override)
        ),
        "allow_illiquid": existing.allow_illiquid_withdrawals if existing else False,
        "reinvest": existing.reinvest_surplus if existing else True,
        "entity_ids": list(existing.entity_ids) if existing else [],
        "bundle_ids": list(existing.bundle_ids) if existing else [],
    }

    with st.form(f"scenario_editor_form_{mode}_{record_id}"):
        name = st.text_input("Name", value=defaults["name"], key=field("name"))
        description = st.text_area("Description", value=defaults["description"], key=field("description"))
        inflation_override = st.text_input(
            "Inflation rate override, e.g. 0.05 for 5% (blank = use base assumption)",
            value=defaults["inflation_override"], key=field("inflation_override"),
        )
        allow_illiquid = st.checkbox(
            "Allow illiquid withdrawals (overrides do_not_sell when drawing down)",
            value=defaults["allow_illiquid"], key=field("allow_illiquid"),
        )
        reinvest = st.checkbox("Reinvest surplus", value=defaults["reinvest"], key=field("reinvest"))

        st.caption(
            "Optionally scope this scenario to just specific Entities and/or "
            "Bundles instead of the whole portfolio. Leave both blank to run "
            "everything (the default)."
        )
        scoped_entities = st.multiselect(
            "Limit to Entities", options=entity_ids,
            default=[e for e in defaults["entity_ids"] if e in entity_ids],
            format_func=lambda eid: entity_name_by_id.get(eid, eid), key=field("entity_ids"),
        )
        scoped_bundles = st.multiselect(
            "Limit to Bundles", options=bundle_ids,
            default=[b for b in defaults["bundle_ids"] if b in bundle_ids],
            format_func=lambda bid: bundle_name_by_id.get(bid, bid), key=field("bundle_ids"),
        )

        if mode == "edit":
            st.caption(
                f"This scenario has {len(existing.asset_overrides)} asset override(s) and "
                f"{len(existing.cashflow_overrides)} cashflow override(s), managed separately "
                "below (not editable in this form)."
            )

        col_save, col_cancel = st.columns([1, 1])
        with col_save:
            save_clicked = st.form_submit_button("💾 Save", type="primary")
        with col_cancel:
            cancel_clicked = st.form_submit_button("Cancel")

    if cancel_clicked:
        _editor_close(kind)
        st.rerun()

    if save_clicked:
        inflation_str = inflation_override.strip()
        if not name.strip():
            st.error("Name is required.")
        elif inflation_str:
            try:
                parsed_inflation = float(inflation_str)
            except ValueError:
                st.error(
                    f"Inflation rate override must be a number like '0.05', not '{inflation_str}'. "
                    "Leave it blank to use the base assumption."
                )
                parsed_inflation = "__invalid__"
        else:
            parsed_inflation = None

        if name.strip() and parsed_inflation != "__invalid__":
            try:
                if mode == "new":
                    scenario_id = _generate_id("scenario", name, [s.scenario_id for s in portfolio.scenarios])
                    asset_overrides, cashflow_overrides = {}, {}
                else:
                    scenario_id = record_id
                    # Preserve overrides set via the separate "Add an asset
                    # override" tool -- this form doesn't edit those.
                    asset_overrides = dict(existing.asset_overrides)
                    cashflow_overrides = dict(existing.cashflow_overrides)

                scenario = Scenario(
                    scenario_id=scenario_id,
                    name=name,
                    description=description,
                    inflation_rate_override=parsed_inflation,
                    allow_illiquid_withdrawals=allow_illiquid,
                    reinvest_surplus=reinvest,
                    entity_ids=list(scoped_entities),
                    bundle_ids=list(scoped_bundles),
                    asset_overrides=asset_overrides,
                    cashflow_overrides=cashflow_overrides,
                    is_base_case=existing.is_base_case if existing else False,
                )

                if mode == "new":
                    portfolio.add_scenario(scenario)
                    st.success(f"Added scenario '{name}'.")
                else:
                    portfolio.replace_scenario(scenario_id, scenario)
                    st.success(f"Saved changes to '{name}'.")

                _clear_simulation_cache()
                _editor_close(kind)
                st.rerun()
            except ValidationError as exc:
                st.error(str(exc))

    if mode == "edit":
        st.divider()
        confirm_key = f"_confirm_delete_scenario_inline_{record_id}"
        if st.button("🗑️ Delete this scenario", key=f"scenario_delete_{record_id}"):
            st.session_state[confirm_key] = True
        if st.session_state.get(confirm_key):
            st.warning("Delete this scenario? This can't be undone.")
            col_yes, col_no = st.columns([1, 1])
            with col_yes:
                if st.button("Yes, delete", key=f"scenario_delete_confirm_{record_id}", type="primary"):
                    portfolio.scenarios = [s for s in portfolio.scenarios if s.scenario_id != record_id]
                    _clear_simulation_cache()
                    st.session_state.pop(confirm_key, None)
                    _editor_close(kind)
                    st.success(f"Deleted scenario '{existing.name}'.")
                    st.rerun()
            with col_no:
                if st.button("Cancel", key=f"scenario_delete_cancel_{record_id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()


def _parse_override_value(value_str: str):
    """Best-effort parse of a free-typed override value into bool/int/float/str."""
    lowered = value_str.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(value_str)
    except ValueError:
        pass
    try:
        return float(value_str)
    except ValueError:
        pass
    return value_str


# ---------------------------------------------------------------------------
# Simulation tab
# ---------------------------------------------------------------------------

def render_simulation_tab(portfolio: Portfolio) -> None:
    st.header("Run Simulation")

    if st.button("Run simulation for all scenarios", type="primary"):
        with st.spinner("Running year-by-year simulation..."):
            results = simulation.run_all_scenarios(portfolio)
            st.session_state["simulation_results"] = results
        st.success(f"Simulation complete: {len(results)} scenario(s).")

    results = st.session_state.get("simulation_results")
    if not results:
        st.info("Run the simulation to see annual results.")
        return

    scenario_ids = [r.scenario_id for r in results]
    chosen_id = st.selectbox("View scenario", scenario_ids)
    chosen = next(r for r in results if r.scenario_id == chosen_id)

    numeric_df = pd.DataFrame(
        [
            {
                "Year": r.year,
                "Gross Income": r.total_gross_income,
                "Expenses": r.total_expenses,
                "Est. Tax": r.total_estimated_tax,
                "Surplus/(Deficit)": r.surplus_or_deficit,
                "Withdrawal": r.withdrawal_amount,
                "Reinvestment": r.reinvestment_amount,
                "Total Net Worth": r.total_net_worth,
                "Liquid Net Worth": r.liquid_net_worth,
                "Illiquid Net Worth": r.illiquid_net_worth,
            }
            for r in chosen.annual_results
        ]
    )
    currency_cols = [c for c in numeric_df.columns if c != "Year"]
    display_df = numeric_df.copy()
    for col in currency_cols:
        display_df[col] = display_df[col].apply(format_currency)
    st.dataframe(display_df, use_container_width=True)
    st.line_chart(numeric_df.set_index("Year")[["Total Net Worth", "Liquid Net Worth", "Illiquid Net Worth"]])

    all_warnings = [(r.year, w) for r in chosen.annual_results for w in r.warnings]
    if all_warnings:
        st.subheader("Warnings")
        for year, w in all_warnings:
            st.warning(f"Year {year}: {w}")


# ---------------------------------------------------------------------------
# Export tab
# ---------------------------------------------------------------------------

def render_export_tab(portfolio: Portfolio) -> None:
    st.header("Export Excel Report")

    results = st.session_state.get("simulation_results")
    if not results:
        st.info("Run a simulation first (see the Simulation tab) before exporting.")
        return

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    default_name = f"{portfolio.household.name or 'household'}_report.xlsx".replace(" ", "_")
    file_name = st.text_input("Output file name", value=default_name)

    if st.button("Export to Excel", type="primary"):
        out_path = OUTPUTS_DIR / file_name
        try:
            written_path = exports.export_to_excel(portfolio, results, out_path)
            st.success(f"Exported: {written_path}")
            with open(written_path, "rb") as f:
                st.download_button(
                    "Download Excel report",
                    data=f.read(),
                    file_name=written_path.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
        except Exception as exc:  # noqa: BLE001 - surface any export failure to the user
            st.error(f"Export failed: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("ManagedFlow")
    st.caption("Asset, Cashflow, Events and Transitions Planner")
    render_sidebar()

    portfolio = _get_portfolio()
    if portfolio is None:
        st.info(
            "Load an existing portfolio file or create a new one from the sidebar to get started. "
            "You can also generate a sample portfolio by running `python sample_data.py` from the "
            "project folder, then loading data/portfolio_sample.json."
        )
        return

    tabs = st.tabs(
        ["Assets", "Cashflows", "Entities & Bundles", "Assumptions", "Scenarios", "Simulation", "Export"]
    )
    with tabs[0]:
        render_assets_tab(portfolio)
    with tabs[1]:
        render_cashflows_tab(portfolio)
    with tabs[2]:
        render_entities_bundles_tab(portfolio)
    with tabs[3]:
        render_assumptions_tab(portfolio)
    with tabs[4]:
        render_scenarios_tab(portfolio)
    with tabs[5]:
        render_simulation_tab(portfolio)
    with tabs[6]:
        render_export_tab(portfolio)


if __name__ == "__main__":
    main()
