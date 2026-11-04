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
import montecarlo
import quick_add
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
    CreditLine,
    Event,
    Membership,
    MembershipRole,
    Person,
    Entity,
    EntityType,
    FlowType,
    Frequency,
    Household,
    LiabilityType,
    LiquidityCategory,
    Loan,
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
    portfolio = st.session_state.get("portfolio")
    if portfolio is not None and type(portfolio) is not Portfolio:
        # The code was updated while the app was running: the object in memory
        # was built from the old classes. Round-trip it through a dict so the
        # migrations upgrade it to the new model (unsaved edits are kept).
        portfolio = Portfolio.from_dict(portfolio.to_dict())
        st.session_state["portfolio"] = portfolio
    return portfolio


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
            portfolio.remove_asset(asset_id)  # also removes its linked cashflows
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
            "💡 No Entities defined yet. Visit the Entities tab first if "
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
        ev_col1, ev_col2 = st.columns(2)
        with ev_col1:
            asset_start_event = _event_picker(
                "Bought at event (sets start year)", portfolio,
                existing.start_event_id if existing else None, field("start_event"),
            )
        with ev_col2:
            asset_end_event = _event_picker(
                "Sold at event (sets sale year)", portfolio,
                existing.end_event_id if existing else None, field("end_event"),
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
                    # keep sale details the form doesn't show (previously lost on edit)
                    sale_year=existing.sale_year if existing else None,
                    sale_assumptions=dict(existing.sale_assumptions) if existing else {},
                    start_event_id=asset_start_event,
                    end_event_id=asset_end_event,
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
        linked_flows = portfolio.cashflows_for_asset(record_id)
        if linked_flows:
            st.caption(
                f"⚠️ This asset has {len(linked_flows)} linked cashflow(s) "
                f"({', '.join(cf.name for cf in linked_flows)}). Deleting the asset "
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
                        portfolio.remove_asset(record_id)  # also removes its linked cashflows
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
            referencing_bundles = [b.name for b in portfolio.bundles if flow_id in b.cashflow_ids]
            if referencing_bundles:
                st.error(
                    f"Can't delete '{name}': it's referenced by Bundle(s) "
                    f"{', '.join(referencing_bundles)}. Remove it from those Bundles first."
                )
                return False
            portfolio.remove_cashflow(flow_id)
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
            month_col1, month_col2 = st.columns(2)
            with month_col1:
                start_month = st.selectbox(
                    "Start month", list(range(1, 13)), format_func=lambda m: MONTH_NAMES[m - 1],
                    index=(existing.start_month - 1) if existing else 0, key=field("start_month"),
                )
            with month_col2:
                end_month = st.selectbox(
                    "End month", list(range(1, 13)), format_func=lambda m: MONTH_NAMES[m - 1],
                    index=(existing.end_month - 1) if existing else 11, key=field("end_month"),
                )
            start_event_id = _event_picker(
                "Starts at event (overrides start date)", portfolio,
                existing.start_event_id if existing else None, field("start_event"),
            )
            end_event_id = _event_picker(
                "Ends at event (stops the month before)", portfolio,
                existing.end_event_id if existing else None, field("end_event"),
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
                    start_month=int(start_month),
                    end_month=int(end_month),
                    start_event_id=start_event_id,
                    end_event_id=end_event_id,
                )

                if mode == "new":
                    portfolio.add_cashflow(cashflow)
                    st.success(f"Added cashflow '{name}'.")
                else:
                    # Re-linking a flow to a different asset (or to
                    # household-level) is just a new asset_id -- all
                    # cashflows live in one list.
                    portfolio.replace_cashflow(flow_id, cashflow)
                    st.success(f"Saved changes to '{name}'.")

                _clear_simulation_cache()
                _editor_close(kind)
                st.rerun()
            except ValidationError as exc:
                st.error(str(exc))

    if mode == "edit":
        st.divider()
        referencing_bundles = [b.name for b in portfolio.bundles if record_id in b.cashflow_ids]
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
                        portfolio.remove_cashflow(record_id)
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
# Entities tab and Bundles tab
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Events tab (+ a picker the other editors use)
# ---------------------------------------------------------------------------

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _event_label(ev) -> str:
    return f"{ev.name} ({MONTH_NAMES[ev.month - 1]} {ev.year})"


def _personal_person(portfolio: Portfolio, entity) -> "Person | None":
    """For a personal entity: the person who owns it (their birthday is shown on the entity)."""
    if entity is None or entity.entity_type != EntityType.PERSONAL:
        return None
    owners = [p for p, role in portfolio.people_in(entity.entity_id) if role == MembershipRole.OWNER]
    return owners[0] if owners else None


def render_memberships_section(portfolio: Portfolio) -> None:
    st.subheader("Who's in each entity")
    st.caption(
        "Links people to entities, with a role. One person can be in many entities (Phil in his "
        "personal entity and the joint one); one entity can hold many people (the joint entity holds "
        "Phil and Debbie). Roles record e.g. trustee or beneficiary of a trust."
    )
    kind = "membership"
    entity_names = {e.entity_id: e.name for e in portfolio.entities}
    person_names = {p.person_id: p.name for p in portfolio.people}
    if portfolio.memberships:
        df = pd.DataFrame([
            {"Entity": entity_names.get(m.entity_id, m.entity_id), "Person": person_names.get(m.person_id, m.person_id),
             "Role": m.role.value.replace("_", " "), "Notes": m.notes}
            for m in portfolio.memberships
        ])

        def _delete_link(key: str) -> bool:
            portfolio.remove_membership(key)
            st.success("Link removed.")
            return True

        _render_selectable_table(kind, df, [m.key for m in portfolio.memberships], _delete_link,
                                 delete_label="this link")
        editor = _editor_get(kind)
        if editor and editor["mode"] == "edit":
            m = next((x for x in portfolio.memberships if x.key == editor["record_id"]), None)
            if m is None:
                _editor_close(kind)
            else:
                roles = [r.value for r in MembershipRole]
                new_role = st.selectbox(f"Role of {person_names.get(m.person_id)} in {entity_names.get(m.entity_id)}",
                                        roles, index=roles.index(m.role.value), key=_editor_widget_key(kind, "role"),
                                        format_func=lambda r: r.replace("_", " "))
                if st.button("💾 Save role", key=_editor_widget_key(kind, "save")):
                    try:
                        portfolio.remove_membership(m.key)
                        portfolio.add_membership(Membership(entity_id=m.entity_id, person_id=m.person_id,
                                                            role=new_role, notes=m.notes))
                        _editor_close(kind)
                        st.rerun()
                    except ValidationError as exc:
                        portfolio.memberships.append(m)  # put the original back
                        st.error(str(exc))

    if not (portfolio.entities and portfolio.people):
        st.info("Add at least one entity and one person to link them.")
        return
    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
    with col1:
        eid = st.selectbox("Entity", list(entity_names), format_func=entity_names.get, key="link_entity")
    with col2:
        pid = st.selectbox("Person", list(person_names), format_func=person_names.get, key="link_person")
    with col3:
        role = st.selectbox("Role", [r.value for r in MembershipRole], key="link_role",
                            format_func=lambda r: r.replace("_", " "))
    with col4:
        st.write("")
        if st.button("➕ Link", key="link_add"):
            try:
                portfolio.add_membership(Membership(entity_id=eid, person_id=pid, role=role))
                st.rerun()
            except ValidationError as exc:
                st.error(str(exc))


def render_people_section(portfolio: Portfolio) -> None:
    st.subheader("People")
    st.caption(
        "Everyone with a birthday: each personal entity's person, plus anyone else (e.g. each "
        "spouse in a joint entity). Birthdays let events follow ages ('Sam turns 65') and show "
        "ages in the results. 'Plan to age' is a planning age: plan long so you don't run short."
    )
    kind = "person"
    if portfolio.people:
        year = portfolio.assumptions.start_year
        df = pd.DataFrame([
            {"Name": p.name, "Born": f"{MONTH_NAMES[p.birth_month - 1]} {p.birth_year}",
             f"Age in {year}": p.age_in(year), "Plan to age": p.life_expectancy,
             "Through year": p.final_year,
             "Entities (role)": "; ".join(f"{e.name} ({r.value.replace('_', ' ')})"
                                         for e, r in portfolio.entities_of(p.person_id))}
            for p in portfolio.people
        ])

        def _delete_person(person_id: str) -> bool:
            p = portfolio.get_person(person_id)
            portfolio.remove_person(person_id)
            _clear_simulation_cache()
            st.success(f"Removed {p.name}. Their age-based events kept their dates as fixed dates.")
            return True

        _render_selectable_table(kind, df, [p.person_id for p in portfolio.people], _delete_person,
                                 delete_label="this person")
    if st.button("➕ Add person", key="person_add_new_btn"):
        _editor_open_new(kind)
        st.rerun()

    without_personal = [
        p for p in portfolio.people
        if not any(e.entity_type == EntityType.PERSONAL for e, _ in portfolio.entities_of(p.person_id))
    ]
    if without_personal:
        col_pick, col_btn = st.columns([2, 1])
        with col_pick:
            pid = st.selectbox("Person without a personal entity", [p.person_id for p in without_personal],
                               format_func=lambda x: portfolio.get_person(x).name, key="personal_entity_pick")
        with col_btn:
            st.write("")
            if st.button("Create personal entity", key="personal_entity_create"):
                p = portfolio.get_person(pid)
                eid = _generate_id("entity", p.name, [e.entity_id for e in portfolio.entities])
                portfolio.add_entity(Entity(entity_id=eid, name=p.name, entity_type=EntityType.PERSONAL))
                portfolio.add_membership(Membership(entity_id=eid, person_id=pid, role="owner"))
                _clear_simulation_cache()
                st.rerun()

    editor = _editor_get(kind)
    if editor is None:
        return
    existing = portfolio.get_person(editor["record_id"]) if editor["mode"] == "edit" else None

    def field(name: str):
        return _editor_widget_key(kind, name)

    # Plain widgets (not st.form) so the age below updates as you type.
    st.markdown(f"**Editing {existing.name}**" if existing else "**New person**")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        name = st.text_input("Name", value=existing.name if existing else "", key=field("name"))
    with col2:
        birth_year = st.number_input("Birth year", min_value=1900, max_value=2100, step=1,
                                     value=existing.birth_year if existing else 1965, key=field("birth_year"))
    with col3:
        birth_month = st.selectbox("Birth month", list(range(1, 13)), format_func=lambda m: MONTH_NAMES[m - 1],
                                   index=(existing.birth_month - 1) if existing else 0, key=field("birth_month"))
    with col4:
        life_expectancy = st.number_input("Plan to age", min_value=50, max_value=120, step=1,
                                          value=existing.life_expectancy if existing else 92,
                                          key=field("life_expectancy"))
    plan_start = portfolio.assumptions.start_year
    st.info(
        f"Age in {plan_start}: **{plan_start - int(birth_year)}** "
        f"(birthday in {MONTH_NAMES[int(birth_month) - 1]}). "
        f"Planning to age {int(life_expectancy)} runs through {int(birth_year) + int(life_expectancy)}."
    )
    col_save, col_cancel = st.columns([1, 1])
    with col_save:
        save_clicked = st.button("💾 Save person", type="primary", key=field("save"))
    with col_cancel:
        cancel_clicked = st.button("Cancel", key=field("cancel"))
    if cancel_clicked:
        _editor_close(kind)
        st.rerun()
    if save_clicked:
        if not name.strip():
            st.error("Name is required.")
            return
        try:
            person_id = existing.person_id if existing else _generate_id(
                "person", name, [p.person_id for p in portfolio.people])
            person = Person(person_id=person_id, name=name, birth_year=int(birth_year),
                            birth_month=int(birth_month), life_expectancy=int(life_expectancy),
                            notes=existing.notes if existing else "")
            if existing:
                portfolio.replace_person(person_id, person)  # moves their age-based events
            else:
                portfolio.add_person(person)
            _clear_simulation_cache()
            _editor_close(kind)
            st.rerun()
        except ValidationError as exc:
            st.error(str(exc))


def _event_picker(label: str, portfolio: Portfolio, current, key: str):
    """Selectbox of '(none)' + every Event; returns an event_id or None."""
    options = [None] + [ev.event_id for ev in portfolio.events]
    labels = {ev.event_id: _event_label(ev) for ev in portfolio.events}
    return st.selectbox(
        label, options, index=options.index(current) if current in options else 0,
        format_func=lambda eid: "(none)" if eid is None else labels.get(eid, eid), key=key,
    )


def render_events_tab(portfolio: Portfolio) -> None:
    st.header("Events")
    st.caption(
        "Named dates that other records hang off: Retirement, sell a property, pay off a loan. "
        "An event's month is the FIRST month things change: anything that starts at it begins "
        "that month, anything that ends at it stops the month before. Move an event and every "
        "linked cashflow, asset and loan moves with it."
    )
    kind = "event"
    if portfolio.events:
        df = pd.DataFrame(
            [
                {
                    "Name": ev.name,
                    "Date": f"{MONTH_NAMES[ev.month - 1]} {ev.year}",
                    "Based on": (f"{portfolio.get_person(ev.person_id).name} turns {ev.age}"
                                 if ev.person_id and portfolio.get_person(ev.person_id) else "fixed date"),
                    "Linked records": len(portfolio.records_linked_to_event(ev.event_id)),
                    "Notes": ev.notes,
                }
                for ev in portfolio.events
            ]
        )
        st.caption("Click a row to select it, then Edit or Delete.")

        def _delete_event(event_id: str) -> bool:
            ev = portfolio.get_event(event_id)
            portfolio.remove_event(event_id)  # linked records keep their dates, just unlinked
            _clear_simulation_cache()
            st.success(f"Deleted event '{ev.name}'. Linked records kept their current dates.")
            return True

        _render_selectable_table(kind, df, [ev.event_id for ev in portfolio.events], _delete_event,
                                 delete_label="this event")
    else:
        st.info("No events yet. Click 'Add new event' below, e.g. 'Retirement', July 2026.")

    if st.button("➕ Add new event", key="event_add_new_btn"):
        _editor_open_new(kind)
        st.rerun()

    editor = _editor_get(kind)
    if editor is None:
        return
    mode, record_id = editor["mode"], editor["record_id"]
    existing = portfolio.get_event(record_id) if mode == "edit" else None
    if mode == "edit" and existing is None:
        _editor_close(kind)
        return

    def field(name: str):
        return _editor_widget_key(kind, name)

    st.markdown(f"**Editing '{existing.name}'**" if existing else "**New event**")
    if existing:
        linked = portfolio.records_linked_to_event(record_id)
        if linked:
            st.caption(f"Changing the date moves: {', '.join(linked)}")
    # Outside the form so the form can switch fields (forms don't rerun until Save).
    timing_options = ["fixed", "age"] if portfolio.people else ["fixed"]
    if not portfolio.people:
        st.caption("To have this event follow someone's age (e.g. 'Phil turns 65'), give a personal "
                   "entity a birthday on the Entities tab first.")
    timing = st.radio(
        "When", timing_options, horizontal=True, key=field("timing"),
        index=1 if existing and existing.is_age_based and portfolio.people else 0,
        format_func=lambda t: "A fixed date" if t == "fixed" else "When a person reaches an age",
    )
    people_names = {p.person_id: p.name for p in portfolio.people}
    with st.form(f"event_editor_form_{mode}_{record_id}"):
        col1, col2, col3 = st.columns(3)
        with col1:
            name = st.text_input("Name", value=existing.name if existing else "", key=field("name"))
        if timing == "fixed":
            person_id, age = None, None
            with col2:
                year = st.number_input("Year", step=1, key=field("year"),
                                       value=int(existing.year) if existing else int(portfolio.assumptions.start_year))
            with col3:
                month = st.selectbox("First month of the change", list(range(1, 13)), key=field("month"),
                                     index=(existing.month - 1) if existing else 0,
                                     format_func=lambda m: MONTH_NAMES[m - 1])
        else:
            pids = list(people_names)
            with col2:
                person_id = st.selectbox("Person", pids, format_func=people_names.get, key=field("person"),
                                         index=pids.index(existing.person_id)
                                         if existing and existing.person_id in pids else 0)
            with col3:
                age = st.number_input("Age", min_value=0, max_value=120, step=1, key=field("age"),
                                      value=int(existing.age) if existing and existing.age is not None else 65)
            year, month = portfolio.get_person(person_id).date_at_age(int(age))
            st.caption("The date follows the person's birthday month at that age.")
        notes = st.text_area("Notes", value=existing.notes if existing else "", key=field("notes"))
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
            return
        try:
            age_fields = dict(person_id=person_id, age=int(age) if age is not None else None)
            if existing:
                portfolio.replace_event(record_id, Event(event_id=record_id, name=name, year=int(year),
                                                         month=int(month), notes=notes, **age_fields))
            else:
                event_id = _generate_id("event", name, [ev.event_id for ev in portfolio.events])
                portfolio.add_event(Event(event_id=event_id, name=name, year=int(year),
                                          month=int(month), notes=notes, **age_fields))
            _clear_simulation_cache()
            _editor_close(kind)
            st.rerun()
        except ValidationError as exc:
            st.error(str(exc))


# ---------------------------------------------------------------------------
# Liabilities tab
# ---------------------------------------------------------------------------

_KIND_LABELS = {"loan": "Loan (amortizing)", "credit_line": "Credit line (interest-only)"}


def _monthly_payment_label(li) -> str:
    if isinstance(li, Loan):
        return format_currency(li.scheduled_monthly_payment())
    return format_currency(li.balance * li.annual_interest_rate / 12) + " (interest)"


def render_liabilities_tab(portfolio: Portfolio) -> None:
    st.header("Liabilities")
    st.caption(
        "Loans and credit lines. Payments are calculated for you each year: "
        "don't also enter them as cashflows, or they'll be counted twice."
    )

    kind = "liability"
    asset_name_by_id = {a.asset_id: a.name for a in portfolio.assets}
    entity_name_by_id = {e.entity_id: e.name for e in portfolio.entities}

    if portfolio.liabilities:
        df = pd.DataFrame(
            [
                {
                    "Name": li.name,
                    "Kind": _KIND_LABELS.get(li.kind, li.kind),
                    "Type": li.liability_type.value,
                    "Lender": li.lender,
                    "Balance": format_currency(li.balance),
                    "Rate": f"{li.annual_interest_rate:.3%}",
                    "As of": li.start_year,
                    "Monthly Payment": _monthly_payment_label(li),
                    "Secured By": asset_name_by_id.get(li.secured_by_asset_id, ""),
                    "Entity": entity_name_by_id.get(li.entity_id, ""),
                    "Active": li.active,
                }
                for li in portfolio.liabilities
            ]
        )
        total = sum(li.balance for li in portfolio.liabilities if li.active)
        st.caption(f"Total owed (active): {format_currency(total)}. Click a row to select it, then Edit or Delete.")

        def _delete_liability(liability_id: str) -> bool:
            li = portfolio.get_liability(liability_id)
            referencing = [b.name for b in portfolio.bundles if liability_id in b.liability_ids]
            if referencing:
                st.error(
                    f"Can't delete '{li.name}': it's referenced by Bundle(s) "
                    f"{', '.join(referencing)}. Remove it from those Bundles first."
                )
                return False
            portfolio.remove_liability(liability_id)
            _clear_simulation_cache()
            st.success(f"Deleted '{li.name}'.")
            return True

        _render_selectable_table(
            kind, df, [li.liability_id for li in portfolio.liabilities], _delete_liability,
            delete_label="this liability",
        )
    else:
        st.info("No liabilities yet. Click 'Add new liability' below to add a loan or credit line.")

    if st.button("➕ Add new liability", key="liability_add_new_btn"):
        _editor_open_new(kind)
        st.rerun()

    editor = _editor_get(kind)
    if editor is not None:
        _render_liability_editor(portfolio, editor)


def _render_liability_editor(portfolio: Portfolio, editor: dict) -> None:
    kind = "liability"
    mode = editor["mode"]
    record_id = editor["record_id"]
    existing = portfolio.get_liability(record_id) if mode == "edit" else None

    if mode == "edit" and existing is None:
        st.warning("That liability no longer exists.")
        _editor_close(kind)
        return

    st.markdown(f"**Editing '{existing.name}'**" if mode == "edit" else "**New liability**")

    def field(name: str):
        return _editor_widget_key(kind, name)

    # Kind sits OUTSIDE the form so the form can show the right fields for it
    # (widgets inside an st.form don't rerun the page until Save).
    kind_values = list(_KIND_LABELS)
    liability_kind = st.radio(
        "Kind", kind_values, format_func=_KIND_LABELS.get, horizontal=True,
        index=kind_values.index(existing.kind) if existing else 0, key=field("kind"),
    )

    asset_options = [None] + [a.asset_id for a in portfolio.assets]
    entity_options = [None] + [e.entity_id for e in portfolio.entities]
    asset_names = {a.asset_id: a.name for a in portfolio.assets}
    entity_names = {e.entity_id: e.name for e in portfolio.entities}
    type_values = [t.value for t in LiabilityType]

    with st.form(f"liability_editor_form_{mode}_{record_id}"):
        col1, col2, col3 = st.columns(3)
        with col1:
            name = st.text_input("Name", value=existing.name if existing else "", key=field("name"))
            liability_type = st.selectbox(
                "Type", type_values,
                index=type_values.index(existing.liability_type.value) if existing else 0,
                key=field("liability_type"),
            )
            lender = st.text_input("Lender", value=existing.lender if existing else "", key=field("lender"))
        with col2:
            balance = st.number_input(
                "Balance owed", min_value=0.0, step=1000.0,
                value=float(existing.balance) if existing else 0.0, key=field("balance"),
            )
            rate_pct = st.number_input(
                "Interest rate (%)", min_value=0.0, max_value=99.0, step=0.125, format="%.3f",
                value=float(existing.annual_interest_rate * 100) if existing else 6.0, key=field("rate"),
            )
            start_year = st.number_input(
                "Balance as of (year)", step=1,
                value=int(existing.start_year) if existing else int(portfolio.assumptions.start_year),
                key=field("start_year"),
            )
        with col3:
            if liability_kind == "loan":
                term_months = st.number_input(
                    "Months left to pay (0 = use payment)", min_value=0, step=12,
                    value=int(getattr(existing, "term_months", None) or 0) if existing else 360,
                    key=field("term_months"),
                )
                monthly_payment = st.number_input(
                    "Monthly payment from statement (0 = calculate)", min_value=0.0, step=10.0,
                    value=float(getattr(existing, "monthly_payment", None) or 0.0) if existing else 0.0,
                    key=field("monthly_payment"),
                )
            else:
                credit_limit = st.number_input(
                    "Credit limit (0 = none)", min_value=0.0, step=1000.0,
                    value=float(getattr(existing, "credit_limit", None) or 0.0) if existing else 0.0,
                    key=field("credit_limit"),
                )
            secured_by = st.selectbox(
                "Secured by asset", asset_options,
                index=asset_options.index(existing.secured_by_asset_id)
                if existing and existing.secured_by_asset_id in asset_options else 0,
                format_func=lambda aid: "(none)" if aid is None else asset_names.get(aid, aid),
                key=field("secured_by"),
            )
            entity_id = st.selectbox(
                "Owed by entity", entity_options,
                index=entity_options.index(existing.entity_id)
                if existing and existing.entity_id in entity_options else 0,
                format_func=lambda eid: "(none)" if eid is None else entity_names.get(eid, eid),
                key=field("entity_id"),
            )
        ev_col1, ev_col2 = st.columns(2)
        with ev_col1:
            li_start_event = _event_picker("Starts at event (sets start year)", portfolio,
                                           existing.start_event_id if existing else None, field("start_event"))
        with ev_col2:
            li_end_event = _event_picker("Paid off at event (sets payoff year)", portfolio,
                                         existing.end_event_id if existing else None, field("end_event"))
        active = st.checkbox("Active", value=existing.active if existing else True, key=field("active"))
        notes = st.text_area("Notes", value=existing.notes if existing else "", key=field("notes"))

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
            return
        try:
            liability_id = record_id if mode == "edit" else _generate_id(
                "liability", name, [li.liability_id for li in portfolio.liabilities]
            )
            common = dict(
                liability_id=liability_id, name=name, liability_type=liability_type, lender=lender,
                balance=balance, annual_interest_rate=rate_pct / 100, start_year=int(start_year),
                secured_by_asset_id=secured_by, entity_id=entity_id, active=active, notes=notes,
                start_event_id=li_start_event, end_event_id=li_end_event,
                payoff_year=existing.payoff_year if existing and not li_end_event else None,
            )
            if liability_kind == "loan":
                liability = Loan(
                    **common,
                    term_months=int(term_months) or None,
                    monthly_payment=monthly_payment or None,
                )
            else:
                liability = CreditLine(**common, credit_limit=credit_limit or None)

            if mode == "new":
                portfolio.add_liability(liability)
                st.success(f"Added '{name}'.")
            else:
                portfolio.replace_liability(record_id, liability)
                st.success(f"Saved changes to '{name}'.")
            _clear_simulation_cache()
            _editor_close(kind)
            st.rerun()
        except ValidationError as exc:
            st.error(str(exc))


def render_entities_tab(portfolio: Portfolio) -> None:
    st.header("Entities")
    st.caption(
        "An Entity is a legal/tax holding structure: you personally, joint, an LLC, "
        "an LP, a trust. Assets, liabilities and bundles can be attributed to one, "
        "and scenarios can be scoped to run against just one Entity."
    )
    entity_kind = "entity"

    if portfolio.entities:
        df = pd.DataFrame(
            [
                {
                    "Name": e.name,
                    "Type": e.entity_type.value,
                    "People (role)": ", ".join(f"{p.name} ({r.value.replace('_', ' ')})"
                                               for p, r in portfolio.people_in(e.entity_id)),
                    "Born": (f"{MONTH_NAMES[p.birth_month - 1]} {p.birth_year}"
                             if (p := _personal_person(portfolio, e)) else ""),
                    f"Age in {portfolio.assumptions.start_year}": (p.age_in(portfolio.assumptions.start_year) if p else None),
                    "Notes": e.notes,
                }
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
            portfolio.remove_entity(entity_id)  # also removes its memberships
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
    render_people_section(portfolio)
    st.divider()
    render_memberships_section(portfolio)



def render_bundles_tab(portfolio: Portfolio) -> None:
    st.header("Bundles")
    st.caption(
        "A Bundle groups related assets, liabilities and household cashflows under an "
        "Entity -- e.g. \"4392 Brix\" (the house, its loan, rent and expenses) or "
        "\"Phil earnings\" (just a salary). Cashflows and loans linked to a bundled asset "
        "come along automatically. Scenarios can be scoped to run against one Bundle."
    )
    bundle_kind = "bundle"
    entity_name_lookup = {e.entity_id: e.name for e in portfolio.entities}
    asset_name_by_id = {a.asset_id: a.name for a in portfolio.assets}
    flow_name_by_id = {cf.flow_id: cf.name for cf in portfolio.cashflows}

    if portfolio.bundles:
        df = pd.DataFrame(
            [
                {
                    "Bundle ID": b.bundle_id,
                    "Name": b.name,
                    "Entity": entity_name_lookup.get(b.entity_id, b.entity_id),
                    "Assets": ", ".join(asset_name_by_id.get(aid, aid) for aid in b.asset_ids),
                    "Standalone Cashflows": ", ".join(
                        flow_name_by_id.get(fid, fid) for fid in b.cashflow_ids
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

    person = _personal_person(portfolio, existing) if existing else None
    type_values = [t.value for t in EntityType]

    # Plain widgets (not st.form) so picking "personal" shows the birthday fields right away.
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Name", value=existing.name if existing else "", key=field("name"))
        entity_type = st.selectbox(
            "Entity type", type_values, key=field("entity_type"),
            index=type_values.index(existing.entity_type.value) if existing else 0,
        )
    with col2:
        notes = st.text_area("Notes", value=existing.notes if existing else "", key=field("notes"))

    if entity_type == EntityType.PERSONAL.value:
        c1, c2, c3 = st.columns(3)
        with c1:
            birth_year = st.number_input("Birth year (0 = skip)", min_value=0, max_value=2100, step=1,
                                         value=person.birth_year if person else 0, key=field("birth_year"))
        with c2:
            birth_month = st.selectbox("Birth month", list(range(1, 13)), format_func=lambda m: MONTH_NAMES[m - 1],
                                       index=(person.birth_month - 1) if person else 0, key=field("birth_month"))
        with c3:
            life_expectancy = st.number_input("Plan to age", min_value=50, max_value=120, step=1,
                                              value=person.life_expectancy if person else 92,
                                              key=field("life_expectancy"))
        if birth_year:
            start = portfolio.assumptions.start_year
            st.info(f"Age in {start}: **{start - int(birth_year)}** (birthday in {MONTH_NAMES[int(birth_month) - 1]}). "
                    f"Planning to age {int(life_expectancy)} runs through {int(birth_year) + int(life_expectancy)}.")
    else:
        birth_year = 0

    col_save, col_cancel = st.columns([1, 1])
    with col_save:
        save_clicked = st.button("💾 Save entity", type="primary", key=field("save"))
    with col_cancel:
        cancel_clicked = st.button("Cancel", key=field("cancel"))

    if cancel_clicked:
        _editor_close(kind)
        st.rerun()

    if save_clicked:
        if not name.strip():
            st.error("Name is required.")
        else:
            try:
                person_id = None
                if birth_year:
                    # Create or update the Person this personal entity represents.
                    person_id = person.person_id if person else _generate_id(
                        "person", name, [p.person_id for p in portfolio.people])
                    new_person = Person(person_id=person_id, name=name, birth_year=int(birth_year),
                                        birth_month=int(birth_month), life_expectancy=int(life_expectancy))
                    if person:
                        portfolio.replace_person(person_id, new_person)
                    else:
                        portfolio.add_person(new_person)

                if mode == "new":
                    entity_id = _generate_id("entity", name, [e.entity_id for e in portfolio.entities])
                    portfolio.add_entity(Entity(entity_id=entity_id, name=name, entity_type=entity_type, notes=notes))
                    st.success(f"Added entity '{name}'.")
                else:
                    entity_id = record_id
                    portfolio.replace_entity(record_id, Entity(entity_id=record_id, name=name,
                                                               entity_type=entity_type, notes=notes))
                    st.success(f"Saved changes to '{name}'.")
                # A personal entity's birthday belongs to its owner: make sure that link exists.
                if person_id and not any(m.key == f"{entity_id}|{person_id}|owner" for m in portfolio.memberships):
                    portfolio.add_membership(Membership(entity_id=entity_id, person_id=person_id, role="owner"))
                _clear_simulation_cache()
                _editor_close(kind)
                st.rerun()
            except ValidationError as exc:
                st.error(str(exc))

    if mode == "edit":
        st.divider()
        referencing_bundles = [b.name for b in portfolio.bundles if b.entity_id == record_id]
        referencing_assets = [a.name for a in portfolio.assets if a.entity_id == record_id]
        referencing_liabilities = [li.name for li in portfolio.liabilities if li.entity_id == record_id]
        blockers = referencing_bundles + referencing_assets + referencing_liabilities
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
                        portfolio.remove_entity(record_id)  # also removes its memberships
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
    flow_name_by_id = {cf.flow_id: cf.name for cf in portfolio.household_level_cashflows()}
    entity_ids = [e.entity_id for e in portfolio.entities]

    def field(name: str):
        return _editor_widget_key(kind, name)

    defaults = {
        "name": existing.name if existing else "",
        "entity_choice": existing.entity_id if existing else (entity_ids[0] if entity_ids else None),
        "asset_ids": list(existing.asset_ids) if existing else [],
        "flow_ids": list(existing.cashflow_ids) if existing else [],
        "liability_ids": list(existing.liability_ids) if existing else [],
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
                options=[cf.flow_id for cf in portfolio.household_level_cashflows()],
                default=[fid for fid in defaults["flow_ids"] if fid in flow_name_by_id],
                format_func=lambda fid: flow_name_by_id.get(fid, fid), key=field("flow_ids"),
            )
            liability_name_by_id = {li.liability_id: li.name for li in portfolio.liabilities}
            selected_liabilities = st.multiselect(
                "Liabilities in this bundle (loans secured by a bundled asset "
                "travel automatically with it)",
                options=list(liability_name_by_id),
                default=[lid for lid in defaults["liability_ids"] if lid in liability_name_by_id],
                format_func=lambda lid: liability_name_by_id.get(lid, lid), key=field("liability_ids"),
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
                    cashflow_ids=list(selected_flows),
                    liability_ids=list(selected_liabilities),
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
                    "Event moves": len(s.event_overrides),
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

    _render_event_override_tool(portfolio)

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


def _render_event_override_tool(portfolio: Portfolio) -> None:
    """'Retire two years later' = a scenario that moves the Retirement event."""
    if not (portfolio.scenarios and portfolio.events):
        return
    with st.expander("Move an event in a scenario (e.g. retire later)"):
        scenario_ids = [s.scenario_id for s in portfolio.scenarios]
        scenario_names = {s.scenario_id: s.name for s in portfolio.scenarios}
        sid = st.selectbox("Scenario", scenario_ids, format_func=lambda x: scenario_names.get(x, x),
                           key="event_override_scenario")
        eid = _event_picker("Event", portfolio, portfolio.events[0].event_id, "event_override_event")
        ev = portfolio.get_event(eid) if eid else None
        if ev is None:
            return
        if ev.is_age_based:
            person = portfolio.get_person(ev.person_id)
            new_age = st.number_input(f"New age for {person.name}", min_value=0, max_value=120,
                                      value=int(ev.age), step=1, key="event_override_age")
            if st.button("Save event move", key="event_override_save"):
                scenario = portfolio.get_scenario(sid)
                scenario.event_overrides[eid] = {"age": int(new_age)}
                _clear_simulation_cache()
                st.success(f"In '{scenario.name}', {ev.name} happens at age {new_age}.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                new_year = st.number_input("New year", value=int(ev.year), step=1, key="event_override_year")
            with col2:
                new_month = st.selectbox("New month", list(range(1, 13)), index=ev.month - 1,
                                         format_func=lambda m: MONTH_NAMES[m - 1], key="event_override_month")
            if st.button("Save event move", key="event_override_save"):
                scenario = portfolio.get_scenario(sid)
                scenario.event_overrides[eid] = {"year": int(new_year), "month": int(new_month)}
                _clear_simulation_cache()
                st.success(f"In '{scenario.name}', {ev.name} moves to {MONTH_NAMES[new_month - 1]} {new_year}.")
        current = portfolio.get_scenario(sid).event_overrides
        if current:
            st.caption("Event moves in this scenario: " + "; ".join(
                f"{portfolio.get_event(k).name if portfolio.get_event(k) else k} -> "
                + (f"age {v['age']}" if "age" in v else f"{MONTH_NAMES[v.get('month', 1) - 1]} {v.get('year')}")
                for k, v in current.items()))


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
                "Ages": ", ".join(f"{n} {a}" for n, a in r.ages.items()),
                "Gross Income": r.total_gross_income,
                "Expenses": r.total_expenses,
                "Est. Tax": r.total_estimated_tax,
                "Surplus/(Deficit)": r.surplus_or_deficit,
                "Withdrawal": r.withdrawal_amount,
                "Reinvestment": r.reinvestment_amount,
                "Debt Service": r.total_debt_service,
                "Total Assets": r.total_assets,
                "Total Liabilities": r.total_liabilities,
                "Total Net Worth": r.total_net_worth,
                "Liquid Net Worth": r.liquid_net_worth,
                "Illiquid Net Worth": r.illiquid_net_worth,
            }
            for r in chosen.annual_results
        ]
    )
    currency_cols = [c for c in numeric_df.columns if c not in ("Year", "Ages")]
    display_df = numeric_df.copy()
    for col in currency_cols:
        display_df[col] = display_df[col].apply(format_currency)
    if not numeric_df["Ages"].any():  # no people defined yet
        display_df = display_df.drop(columns=["Ages"])
    st.dataframe(display_df, use_container_width=True)
    st.line_chart(numeric_df.set_index("Year")[["Total Assets", "Total Liabilities", "Total Net Worth"]])

    all_warnings = [(r.year, w) for r in chosen.annual_results for w in r.warnings]
    if all_warnings:
        st.subheader("Warnings")
        for year, w in all_warnings:
            st.warning(f"Year {year}: {w}")


def render_monte_carlo_section(portfolio: Portfolio) -> None:
    st.divider()
    st.header("Monte Carlo")
    st.caption(
        "Runs one scenario many times with random market years (each asset's growth swings "
        "around your rate by its volatility) and a random long-run inflation rate. "
        "Success = every year's shortfall could be covered from liquid assets. "
        "Asset swings are independent here, so real bad years can be worse than the 10th percentile."
    )
    scenarios = portfolio.scenarios or [scenario_engine.make_base_case_scenario()]
    names = {s.scenario_id: s.name for s in scenarios}
    col1, col2, col3 = st.columns(3)
    with col1:
        sid = st.selectbox("Scenario", list(names), format_func=names.get, key="mc_scenario")
    with col2:
        n_runs = st.number_input("Runs", min_value=100, max_value=10_000, value=1_000, step=100, key="mc_runs")
    with col3:
        seed = st.number_input("Seed (0 = new random draw each time)", min_value=0, value=42, step=1,
                               key="mc_seed")

    with st.expander("Volatility assumptions (yearly swing, in percentage points)"):
        vol = {}
        cols = st.columns(4)
        for i, (asset_type, default) in enumerate(montecarlo.DEFAULT_VOLATILITY.items()):
            with cols[i % 4]:
                vol[asset_type] = st.number_input(
                    asset_type.replace("_", " "), min_value=0.0, max_value=60.0, step=1.0,
                    value=default * 100, key=f"mc_vol_{asset_type}",
                ) / 100
        inflation_vol = st.number_input(
            "Inflation (long-run rate spread)", min_value=0.0, max_value=5.0, step=0.25,
            value=montecarlo.DEFAULT_INFLATION_VOLATILITY * 100, key="mc_vol_inflation",
        ) / 100

    if st.button("Run Monte Carlo", type="primary", key="mc_run_btn"):
        bar = st.progress(0.0, text="Running...")
        result = montecarlo.run_monte_carlo(
            portfolio, next(s for s in scenarios if s.scenario_id == sid),
            n_runs=int(n_runs), seed=int(seed) or None, volatility=vol,
            inflation_volatility=inflation_vol,
            progress=lambda done, total: bar.progress(done / total, text=f"Run {done:,} of {total:,}"),
        )
        bar.empty()
        st.session_state["mc_result"] = result

    result = st.session_state.get("mc_result")
    if result is None:
        return
    m1, m2, m3 = st.columns(3)
    final = result.percentiles()
    m1.metric("Success rate", f"{result.success_rate:.0%}", help="Runs that never ran short of liquid money")
    m2.metric(f"Median net worth, {result.years[-1]}", format_currency(final[50][-1]))
    m3.metric(f"10th percentile, {result.years[-1]}", format_currency(final[10][-1]))

    rows = result.summary_rows()
    chart_df = pd.DataFrame(rows).set_index("Year")[
        ["Net worth, 10th pct", "Net worth, median", "Net worth, 90th pct"]
    ]
    st.line_chart(chart_df)
    display = pd.DataFrame(rows)
    for col in display.columns[1:]:
        display[col] = display[col].apply(format_currency)
    st.dataframe(display, use_container_width=True, hide_index=True)

    failures = result.failure_year_counts()
    if failures:
        st.caption("First year runs ran short: " + ", ".join(f"{y}: {n} runs" for y, n in failures.items()))


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

def render_quick_add(portfolio: Portfolio) -> None:
    with st.expander("✨ Quick add with Claude: describe it in plain English"):
        if not quick_add.api_key_configured():
            st.info(
                "Quick add needs an Anthropic API key. Create one at console.anthropic.com, then in "
                "PowerShell run:  setx ANTHROPIC_API_KEY \"your-key\"  and restart PowerShell and the app."
            )
            return
        st.caption(
            "Claude drafts the records; you review them before anything is added. What you type is "
            "sent to the Anthropic API along with the names (not amounts) of your existing records."
        )
        text = st.text_area(
            "Description",
            placeholder="4392 Brix rental worth $295k with a Nation Star loan of $194,613 at 4.25%, "
                        "25 years left; rent $1,150 a month",
            key="quick_add_text",
        )
        if st.button("Draft records", key="quick_add_draft"):
            with st.spinner("Asking Claude..."):
                try:
                    st.session_state["quick_add_result"] = quick_add.draft_records(text, portfolio)
                except Exception as exc:  # network, key, or API errors -- show, don't crash
                    st.error(f"Quick add failed: {exc}")

        result = st.session_state.get("quick_add_result")
        if not result:
            return
        for q in result.questions:
            st.warning(f"Claude asks: {q}")
        accepted = []
        for i, d in enumerate(result.drafts):
            label = f"{d.record_type.replace('_', ' ').title()}: {d.name}"
            if d.ok:
                keep = st.checkbox(f"Add {label}", value=True, key=f"quick_add_keep_{i}")
                shown = {k: v for k, v in d.record.to_dict().items() if v not in (None, "", [], {}, False)}
                st.json(shown, expanded=False)
                for a in d.assumptions:
                    st.caption(f"Assumed: {a}")
                if keep:
                    accepted.append(d)
            else:
                st.error(f"{label} can't be added: " + "; ".join(d.errors))
        if accepted and st.button(f"Add {len(accepted)} record(s)", type="primary", key="quick_add_apply"):
            for msg in quick_add.add_drafts(portfolio, accepted):
                st.success(msg)
            _clear_simulation_cache()
            st.session_state.pop("quick_add_result", None)


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

    render_quick_add(portfolio)

    # Tab order, left to right. Each tab is (label, function that draws it).
    tab_layout = [
        ("Entities", render_entities_tab),
        ("Bundles", render_bundles_tab),
        ("Assets", render_assets_tab),
        ("Liabilities", render_liabilities_tab),
        ("Cashflows", render_cashflows_tab),
        ("Events", render_events_tab),
        ("Assumptions", render_assumptions_tab),
        ("Scenarios", render_scenarios_tab),
        ("Simulation", lambda p: (render_simulation_tab(p), render_monte_carlo_section(p))),
        ("Export", render_export_tab),
    ]
    for tab, (_label, render) in zip(st.tabs([label for label, _ in tab_layout]), tab_layout):
        with tab:
            render(portfolio)


if __name__ == "__main__":
    main()
