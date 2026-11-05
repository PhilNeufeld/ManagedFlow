"""
quick_add.py

Describe something in plain English; Claude drafts the records; YOU review
them; only then are they added.

    "Debbie's CalPERS, $1,700 a month starting at Debbie retires, 2% COLA, taxable"
    "4392 Brix rental worth $295k, Nation Star loan $194,613 at 4.25%, $1,150/mo rent"

How it works
------------
1. draft_records() sends your text plus a short description of the
   portfolio (existing assets, entities, events, loans -- names and ids
   only) to Claude through the Anthropic API.
2. Claude must answer by calling one tool, `propose_records`, whose
   input_schema mirrors ManagedFlow's own fields -- so the answer comes back
   as structured data, not prose. (This is the "structured output via tool
   use" pattern.)
3. Every drafted record is rebuilt with the real model classes, so the
   same validation the app uses runs on it. Problems are reported per
   record; nothing is added yet.
4. add_drafts() adds the records you accept.

Claude never invents ids: the app generates them. Records in one request
can refer to each other by a short "ref" (e.g. the rent cashflow points at
the house drafted in the same sentence).

Setup: create an API key at console.anthropic.com and store it in the
ANTHROPIC_API_KEY environment variable. Text you type here is sent to the
API, so include only what you're comfortable sending.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from models import (
    _key,
    Asset, AssetType, Cashflow, CashflowDirection, CreditLine, Event, FlowType, Frequency,
    LiabilityType, LiquidityCategory, Loan, Portfolio, TaxCategory, ValidationError,
)

DEFAULT_MODEL = os.environ.get("MANAGEDFLOW_MODEL", "claude-haiku-5-5")
TOOL_NAME = "propose_records"
RECORD_TYPES = ["event", "asset", "loan", "credit_line", "cashflow"]
LINK_FIELDS = ["asset_id", "secured_by_asset_id", "entity_id", "start_event_id", "end_event_id"]
RATE_FIELDS = ["annual_growth_rate", "annual_interest_rate"]


def _enum(values) -> Dict[str, Any]:
    return {"type": "string", "enum": [v.value for v in values]}


RECORD_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "record_type": {"type": "string", "enum": RECORD_TYPES},
        "ref": {"type": "string", "description": "Short label (r1, r2...) so other records in this answer can link to it."},
        "name": {"type": "string"},
        # cashflow
        "flow_type": _enum(FlowType),
        "direction": _enum(CashflowDirection),
        "amount": {"type": "number", "description": "Dollars PER PERIOD of `frequency` (a $1,150/month rent is 1150 with frequency monthly)."},
        "frequency": _enum(Frequency),
        "start_year": {"type": "integer"}, "start_month": {"type": "integer", "minimum": 1, "maximum": 12},
        "end_year": {"type": "integer"}, "end_month": {"type": "integer", "minimum": 1, "maximum": 12},
        "annual_growth_rate": {"type": "number", "description": "FRACTION per year: 2% is 0.02."},
        "inflation_adjusted": {"type": "boolean"},
        "taxable": {"type": "boolean"},
        "tax_category": _enum(TaxCategory),
        # asset
        "asset_type": _enum(AssetType),
        "owner": {"type": "string"},
        "initial_value": {"type": "number"},
        "liquidity_category": _enum(LiquidityCategory),
        # loan / credit line
        "liability_type": _enum(LiabilityType),
        "lender": {"type": "string"},
        "balance": {"type": "number"},
        "annual_interest_rate": {"type": "number", "description": "FRACTION per year: 6% is 0.06."},
        "term_months": {"type": "integer", "description": "Months LEFT to pay."},
        "monthly_payment": {"type": "number"},
        "credit_limit": {"type": "number"},
        # event
        "year": {"type": "integer"}, "month": {"type": "integer", "minimum": 1, "maximum": 12},
        # links: an existing id from the context, or the ref of another record in this answer
        **{f: {"type": "string"} for f in LINK_FIELDS},
        "notes": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"},
                        "description": "Anything you guessed rather than read from the text."},
    },
    "required": ["record_type", "name"],
}

TOOL = {
    "name": TOOL_NAME,
    "description": "Propose ManagedFlow records (events, assets, loans, credit lines, cashflows) "
                   "for the user to review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "records": {"type": "array", "items": RECORD_SCHEMA},
            "questions": {"type": "array", "items": {"type": "string"},
                          "description": "What you'd need to know to do better, if anything was unclear."},
        },
        "required": ["records"],
    },
}


def portfolio_context(portfolio: Portfolio) -> str:
    """Names and ids only -- enough for Claude to link records, nothing more."""
    def lines(title, items):
        return f"{title}:\n" + ("\n".join(f"  - {i}" for i in items) if items else "  (none)")
    return "\n".join([
        f"Today: {date.today().isoformat()}. Plan years: {portfolio.assumptions.start_year}-"
        f"{portfolio.assumptions.end_year}.",
        lines("Assets (id: name)", [f"{a.asset_id}: {a.name}" for a in portfolio.assets]),
        lines("Entities (id: name)", [f"{e.entity_id}: {e.name}" for e in portfolio.entities]),
        lines("Events (id: name, month/year)", [f"{e.event_id}: {e.name}, {e.month}/{e.year}" for e in portfolio.events]),
        lines("Liabilities (id: name)", [f"{li.liability_id}: {li.name}" for li in portfolio.liabilities]),
    ])


SYSTEM_PROMPT = """You turn a household's plain-English description of its finances into \
records for ManagedFlow, a personal planning app, by calling the propose_records tool exactly once.

Rules:
- Rates are fractions (6% -> 0.06). Amounts are dollars per period of `frequency`.
- Income is direction "inflow"; expenses and payments are "outflow".
- Loan and credit line PAYMENTS are calculated by the app: never also create a cashflow for them.
- Link to existing records only with ids listed in the context. To link to a record you are \
proposing in this same answer, use its `ref`.
- An event's (year, month) is the first month things change. "Starts at" an event -> start_event_id; \
"ends at"/"until" an event -> end_event_id.
- Leave out fields you can't infer; list anything you guessed in that record's `assumptions`.
- If the text is too vague to draft anything, return no records and ask in `questions`."""


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------

@dataclass
class Draft:
    record_type: str
    raw: Dict[str, Any]
    record: Any = None                # the built model object, if valid
    errors: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.record is not None and not self.errors

    @property
    def name(self) -> str:
        return self.raw.get("name", "(unnamed)")


@dataclass
class DraftResult:
    drafts: List[Draft]
    questions: List[str] = field(default_factory=list)


def _slug_id(prefix: str, name: str, taken: set) -> str:
    base = f"{prefix}_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "item")
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}_{n}", n + 1
    taken.add(candidate)
    return candidate


def _existing_ids(portfolio: Portfolio) -> set:
    return {
        *(a.asset_id for a in portfolio.assets), *(cf.flow_id for cf in portfolio.cashflows),
        *(li.liability_id for li in portfolio.liabilities), *(e.event_id for e in portfolio.events),
        *(e.entity_id for e in portfolio.entities),
    }


_ID_PREFIX = {"event": "event", "asset": "asset", "loan": "liability", "credit_line": "liability", "cashflow": "flow"}
_FIELDS_BY_TYPE = {
    "event": ["name", "year", "month", "notes"],
    "asset": ["name", "asset_type", "owner", "initial_value", "start_year", "annual_growth_rate",
              "liquidity_category", "tax_category", "entity_id", "start_event_id", "end_event_id", "notes"],
    "loan": ["name", "liability_type", "lender", "balance", "annual_interest_rate", "start_year",
             "term_months", "monthly_payment", "secured_by_asset_id", "entity_id",
             "start_event_id", "end_event_id", "notes"],
    "credit_line": ["name", "liability_type", "lender", "balance", "annual_interest_rate", "start_year",
                    "credit_limit", "secured_by_asset_id", "entity_id", "start_event_id", "end_event_id", "notes"],
    "cashflow": ["name", "flow_type", "direction", "amount", "frequency", "start_year", "start_month",
                 "end_year", "end_month", "annual_growth_rate", "inflation_adjusted", "taxable",
                 "tax_category", "asset_id", "start_event_id", "end_event_id", "notes"],
}


def build_drafts(tool_input: Dict[str, Any], portfolio: Portfolio) -> DraftResult:
    """Turn Claude's tool input into validated Draft records (nothing is added)."""
    raws = [r for r in tool_input.get("records", []) if isinstance(r, dict)]
    start = portfolio.assumptions.start_year

    # 1. Give every drafted record the next integer key of its table, and map refs -> keys.
    table_of = {"event": "event", "asset": "asset", "loan": "liability", "credit_line": "liability",
                "cashflow": "cashflow"}
    counters = {t: portfolio.next_id(t) for t in set(table_of.values())}
    ref_to_id: Dict[str, Any] = {}
    ids: List[Optional[int]] = []
    for raw in raws:
        rtype = raw.get("record_type")
        if rtype not in table_of:
            ids.append(None)
            continue
        new_id = counters[table_of[rtype]]
        counters[table_of[rtype]] += 1
        ids.append(new_id)
        if raw.get("ref"):
            ref_to_id[str(raw["ref"])] = new_id

    known = {
        "asset": {a.asset_id for a in portfolio.assets} | {i for r, i in zip(raws, ids) if r.get("record_type") == "asset"},
        "entity": {e.entity_id for e in portfolio.entities},
        "event": {e.event_id for e in portfolio.events} | {i for r, i in zip(raws, ids) if r.get("record_type") == "event"},
    }
    link_kind = {"asset_id": "asset", "secured_by_asset_id": "asset", "entity_id": "entity",
                 "start_event_id": "event", "end_event_id": "event"}

    drafts: List[Draft] = []
    for raw, new_id in zip(raws, ids):
        rtype = raw.get("record_type", "?")
        draft = Draft(record_type=rtype, raw=raw, assumptions=list(raw.get("assumptions") or []))
        drafts.append(draft)
        if new_id is None:
            draft.errors.append(f"Unknown record type '{rtype}'")
            continue

        fields = {k: raw[k] for k in _FIELDS_BY_TYPE[rtype] if raw.get(k) not in (None, "")}

        # Links: resolve refs; drop (and report) anything that points nowhere.
        for f in LINK_FIELDS:
            if f in fields:
                target = ref_to_id.get(str(fields[f]), _key(fields[f]))
                if target in known[link_kind[f]]:
                    fields[f] = target
                else:
                    draft.errors.append(f"{f} '{fields[f]}' doesn't match anything in your portfolio")
                    del fields[f]

        # Rates must be fractions. 6 almost certainly meant 6%, but say so rather than guess.
        for f in RATE_FIELDS:
            if f in fields and abs(fields[f]) >= 1:
                draft.errors.append(f"{f} is {fields[f]}; rates are fractions (6% = 0.06)")

        try:
            if rtype == "event":
                draft.record = Event(event_id=new_id, **fields)
            elif rtype == "asset":
                fields.setdefault("start_year", start)
                fields.setdefault("owner", "")
                fields.setdefault("initial_value", 0.0)
                fields.setdefault("asset_type", AssetType.OTHER.value)
                draft.record = Asset(asset_id=new_id, **fields)
            elif rtype == "loan":
                fields.setdefault("start_year", start)
                fields.setdefault("balance", 0.0)
                fields.setdefault("annual_interest_rate", 0.0)
                draft.record = Loan(liability_id=new_id, **fields)
            elif rtype == "credit_line":
                fields.setdefault("start_year", start)
                fields.setdefault("balance", 0.0)
                fields.setdefault("annual_interest_rate", 0.0)
                draft.record = CreditLine(liability_id=new_id, **fields)
            elif rtype == "cashflow":
                fields.setdefault("start_year", start)
                fields.setdefault("amount", 0.0)
                fields.setdefault("flow_type", FlowType.OTHER.value)
                fields.setdefault("direction", CashflowDirection.INFLOW.value)
                draft.record = Cashflow(flow_id=new_id, **fields)
        except (ValidationError, TypeError, ValueError) as exc:
            draft.errors.append(str(exc))
            draft.record = None

    return DraftResult(drafts=drafts, questions=list(tool_input.get("questions") or []))


# ---------------------------------------------------------------------------
# Talking to Claude
# ---------------------------------------------------------------------------

def api_key_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def draft_records(text: str, portfolio: Portfolio, client=None, model: str = DEFAULT_MODEL) -> DraftResult:
    """Ask Claude to draft records for `text`. Pass `client` to use a fake in tests."""
    if not text.strip():
        return DraftResult(drafts=[], questions=["Type a description first."])
    if client is None:
        import anthropic  # imported here so the rest of the app works without it
        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": f"{portfolio_context(portfolio)}\n\nDescription:\n{text}"}],
    )
    # Read blocks by type, not position (a model may return other blocks first).
    tool_block = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use" and b.name == TOOL_NAME), None
    )
    if tool_block is None:
        return DraftResult(drafts=[], questions=["Claude didn't return any records. Try rephrasing."])
    return build_drafts(tool_block.input, portfolio)


def add_drafts(portfolio: Portfolio, drafts: List[Draft]) -> List[str]:
    """Add accepted, valid drafts in dependency order (events -> assets ->
    liabilities -> cashflows). Returns a message per record."""
    order = {"event": 0, "asset": 1, "loan": 2, "credit_line": 2, "cashflow": 3}
    messages = []
    for d in sorted((d for d in drafts if d.ok), key=lambda d: order[d.record_type]):
        try:
            if d.record_type == "event":
                portfolio.add_event(d.record)
            elif d.record_type == "asset":
                portfolio.add_asset(d.record)
            elif d.record_type in ("loan", "credit_line"):
                portfolio.add_liability(d.record)
            else:
                portfolio.add_cashflow(d.record)
            messages.append(f"Added {d.record_type.replace('_', ' ')} '{d.name}'")
        except ValidationError as exc:
            messages.append(f"Couldn't add '{d.name}': {exc}")
    return messages
