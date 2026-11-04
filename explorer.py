"""
explorer.py

Drill-down view of the plan: entity > sub-entities > collections (bundles) >
assets, liabilities and cashflows, with each one's numbers over time.

build_tree()      the hierarchy, following ownership %. An entity owned along
                  several paths (LVM: via Neufeld Ventures directly, via NV's
                  GP interest in NFLP, and via the trust's NFLP interest)
                  appears once per path, each carrying that path's share,
                  so the paths add up to your total share.
node_series()     one row per year for a node: assets, liabilities, net worth,
                  cash in, cash out, net cash flow -- your share along that path.
icicle_layout()   rectangles for the clickable chart (focus node on the left,
                  what it holds to the right).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import simulation
from models import CashflowDirection, Portfolio, Scenario

# Classes and their colors (validated categorical palette, fixed order).
CLASSES = ["Entity", "Collection", "Asset", "Liability", "Cashflow", "Household"]
CLASS_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
# Label ink on each fill (dark ink on the light fills, white on the dark ones).
CLASS_INK = ["#ffffff", "#0b0b0b", "#0b0b0b", "#0b0b0b", "#0b0b0b", "#ffffff"]
COLOR = dict(zip(CLASSES, CLASS_COLORS))
INK = dict(zip(CLASSES, CLASS_INK))
ROOT = "root"
FIRST_COL = 0.55   # width of the focus column, relative to the others


@dataclass
class Node:
    node_id: str
    label: str
    kind: str                 # one of CLASSES
    ref_id: Optional[str]     # entity_id / bundle_id / asset_id / liability_id / flow_id
    factor: float             # your share along this path (multiplies 100% amounts)
    parent_id: Optional[str] = None
    total_share: float = 1.0  # your share of the item over ALL paths (what the simulation applied)
    children: List[str] = field(default_factory=list)


def build_tree(p: Portfolio) -> Dict[str, Node]:
    nodes: Dict[str, Node] = {}

    def total_share(kind, ref_id) -> float:
        if kind == "Asset":
            return p.asset_share(ref_id)
        if kind == "Liability":
            return p.liability_share(p.get_liability(ref_id))
        if kind == "Cashflow":
            cf = p.get_cashflow(ref_id)
            return p.asset_share(cf.asset_id) if cf and cf.asset_id else 1.0
        return 1.0

    def add(parent: Optional[str], key: str, label: str, kind: str, ref_id, factor: float) -> str:
        node_id = f"{parent}/{key}" if parent else key
        nodes[node_id] = Node(node_id, label, kind, ref_id, factor, parent, total_share=total_share(kind, ref_id))
        if parent:
            nodes[parent].children.append(node_id)
        return node_id

    asset_links = {}
    for o in p.ownerships:
        if o.owned_type == "asset":
            asset_links.setdefault(o.owned_id, []).append(o)
    bundled = {}   # (entity_id) -> sets of ids in its bundles
    for b in p.bundles:
        s = bundled.setdefault(b.entity_id, {"assets": set(), "liabilities": set(), "flows": set()})
        s["assets"] |= set(b.asset_ids); s["liabilities"] |= set(b.liability_ids); s["flows"] |= set(b.cashflow_ids)

    def add_asset(parent, asset, factor):
        nid = add(parent, "a:" + asset.asset_id, asset.name, "Asset", asset.asset_id, factor)
        for cf in p.cashflows_for_asset(asset.asset_id):
            add(nid, "c:" + cf.flow_id, cf.name, "Cashflow", cf.flow_id, factor)
        for li in p.liabilities_secured_by(asset.asset_id):
            add(nid, "l:" + li.liability_id, li.name, "Liability", li.liability_id, factor)

    def add_entity(parent, entity, factor, seen):
        nid = add(parent, "e:" + entity.entity_id, entity.name, "Entity", entity.entity_id, factor)
        seen = seen | {entity.entity_id}
        for o in p.ownerships:
            if o.owner_entity_id == entity.entity_id and o.owned_type == "entity" and o.owned_id not in seen:
                child = p.get_entity(o.owned_id)
                if child:
                    add_entity(nid, child, factor * o.percent, seen)
        in_bundles = bundled.get(entity.entity_id, {"assets": set(), "liabilities": set(), "flows": set()})
        for b in p.bundles_for_entity(entity.entity_id):
            bid = add(nid, "b:" + b.bundle_id, b.name, "Collection", b.bundle_id, factor)
            for aid in b.asset_ids:
                a = p.get_asset(aid)
                if a:
                    add_asset(bid, a, factor * _asset_factor(a, entity.entity_id, asset_links))
            for lid in b.liability_ids:
                li = p.get_liability(lid)
                if li and not li.secured_by_asset_id:
                    add(bid, "l:" + lid, li.name, "Liability", lid, factor)
            for fid in b.cashflow_ids:
                cf = p.get_cashflow(fid)
                if cf and not cf.asset_id:
                    add(bid, "c:" + fid, cf.name, "Cashflow", fid, factor)
        for a in p.assets:
            if a.asset_id in in_bundles["assets"]:
                continue
            links = asset_links.get(a.asset_id)
            if links:
                for o in links:
                    if o.owner_entity_id == entity.entity_id:
                        add_asset(nid, a, factor * o.percent)
            elif a.entity_id == entity.entity_id:
                add_asset(nid, a, factor)
        for li in p.liabilities:
            if li.entity_id == entity.entity_id and not li.secured_by_asset_id and li.liability_id not in in_bundles["liabilities"]:
                add(nid, "l:" + li.liability_id, li.name, "Liability", li.liability_id, factor)

    add(None, ROOT, "Whole plan", "Household", None, 1.0)
    owned = {o.owned_id for o in p.ownerships if o.owned_type == "entity"}
    for e in p.entities:
        if e.entity_id not in owned:
            add_entity(ROOT, e, 1.0, set())

    # Household level: cashflows with no asset (by person), and anything not held by an entity.
    bundled_flows = set().union(*[s["flows"] for s in bundled.values()]) if bundled else set()
    household_flows = [cf for cf in p.cashflows if not cf.asset_id and cf.flow_id not in bundled_flows]
    loose_assets = [a for a in p.assets if not a.entity_id and a.asset_id not in asset_links
                    and not any(a.asset_id in s["assets"] for s in bundled.values())]
    loose_liabs = [li for li in p.liabilities if not li.entity_id and not li.secured_by_asset_id]
    if household_flows or loose_assets or loose_liabs:
        hh = add(ROOT, "household", "Household income & spending", "Household", None, 1.0)
        names = {x.person_id: x.name for x in p.people}
        groups: Dict[str, List] = {}
        for cf in household_flows:
            groups.setdefault(names.get(cf.person_id, "Shared"), []).append(cf)
        for who in sorted(groups, key=lambda w: (w == "Shared", w)):
            gid = add(hh, "p:" + who, who, "Household", None, 1.0)
            for cf in groups[who]:
                add(gid, "c:" + cf.flow_id, cf.name, "Cashflow", cf.flow_id, 1.0)
        for a in loose_assets:
            add_asset(hh, a, 1.0)
        for li in loose_liabs:
            add(hh, "l:" + li.liability_id, li.name, "Liability", li.liability_id, 1.0)
    return nodes


def _asset_factor(asset, entity_id, asset_links) -> float:
    links = asset_links.get(asset.asset_id)
    if not links:
        return 1.0
    return sum(o.percent for o in links if o.owner_entity_id == entity_id) or 1.0


def path(nodes: Dict[str, Node], node_id: str) -> List[Node]:
    out = []
    while node_id:
        out.append(nodes[node_id])
        node_id = nodes[node_id].parent_id
    return list(reversed(out))


# --- numbers over time ------------------------------------------------------------------------------

def run(p: Portfolio, scenario: Scenario):
    """Simulate the scenario in YOUR-SHARE view (even if it's set to 100%: a 100% view
    would add the whole of every property, and tax it, which doesn't sum along paths);
    each node then takes its path's portion."""
    if scenario.ownership_view == "full":
        scenario = copy.deepcopy(scenario)
        scenario.ownership_view = "share"
    return simulation.run_simulation(p, scenario)


def _scale(n: Node, share_view: bool) -> float:
    """Results come in your share (share view) or at 100% (full view). A node on one
    of several ownership paths gets its path's portion of that."""
    if not share_view:
        return n.factor
    return n.factor / n.total_share if n.total_share else 0.0


SERIES = ["Assets", "Liabilities", "Net worth", "Cash in", "Cash out", "Net cash flow"]


def node_series(nodes: Dict[str, Node], node_id: str, result, share_view: bool = True) -> List[dict]:
    """Per year, your share along this node's path. Assets count their own
    income and expenses (so their cashflows aren't counted twice); loans count
    interest + principal as cash out."""
    wp = result.working_portfolio
    infl = wp.assumptions.inflation_rate
    leaves = {"Asset": {}, "Liability": {}, "Cashflow": {}}

    def collect(nid, inside_asset=False):
        n = nodes[nid]
        if n.kind == "Asset":
            leaves["Asset"][nid] = n
            inside_asset = True
        elif n.kind == "Liability":
            leaves["Liability"][nid] = n
        elif n.kind == "Cashflow" and not inside_asset:
            leaves["Cashflow"][nid] = n
        for c in n.children:
            collect(c, inside_asset)
    collect(node_id)
    root_kind = nodes[node_id].kind
    # Surpluses are reinvested into the first liquid asset and shortfalls drawn from
    # assets AFTER each asset's year is simulated: add them to get year-end values.
    reinvest_target = next((a.asset_id for a in wp.assets
                            if a.active and a.liquidity_category.value == "liquid"), None)
    rows = []
    for r in result.annual_results:
        ar = {x.asset_id: x for x in r.asset_results}
        lr = {x.liability_id: x for x in r.liability_results}
        row = {k: 0.0 for k in SERIES}
        row["Year"] = r.year
        extra = {}
        for n in leaves["Asset"].values():
            x = ar.get(n.ref_id)
            if x:
                value = x.ending_value - r.withdrawal_breakdown.get(n.ref_id, 0.0)
                if n.ref_id == reinvest_target:
                    value += r.reinvestment_amount
                row["Assets"] += value * _scale(n, share_view)
                row["Cash in"] += x.gross_income * _scale(n, share_view)
                row["Cash out"] += x.gross_expense * _scale(n, share_view)
        for n in leaves["Liability"].values():
            x = lr.get(n.ref_id)
            if x:
                row["Liabilities"] += x.ending_balance * _scale(n, share_view)
                row["Cash out"] += (x.interest_paid + x.principal_paid) * _scale(n, share_view)
                if root_kind == "Liability":
                    extra = {"Interest": x.interest_paid * _scale(n, share_view), "Principal": x.principal_paid * _scale(n, share_view)}
        for n in leaves["Cashflow"].values():
            cf = wp.get_cashflow(n.ref_id)
            if cf is None or not simulation._flow_active_in_year(cf, r.year):
                continue
            amount = cf.annual_amount_in_year(r.year, infl) * _scale(n, share_view)
            if cf.direction == CashflowDirection.INFLOW:
                row["Cash in"] += amount
            elif cf.direction == CashflowDirection.OUTFLOW:
                row["Cash out"] += amount
        if root_kind == "Cashflow":   # a single cashflow inside an asset: show it on its own
            n = nodes[node_id]
            cf = wp.get_cashflow(n.ref_id)
            row["Cash in"] = row["Cash out"] = 0.0
            if cf is not None and simulation._flow_active_in_year(cf, r.year):
                amount = cf.annual_amount_in_year(r.year, infl) * _scale(n, share_view)
                row["Cash in" if cf.direction == CashflowDirection.INFLOW else "Cash out"] = amount
        if node_id == ROOT:      # the whole household: income tax is paid at this level
            row["Income tax"] = r.total_estimated_tax
            row["Cash out"] += r.total_estimated_tax
        row["Net worth"] = row["Assets"] - row["Liabilities"]
        row["Net cash flow"] = row["Cash in"] - row["Cash out"]
        row.update(extra)
        rows.append(row)
    return rows


def node_value(nodes, node_id, result, year: int, measure: str = "count", share_view: bool = True) -> float:
    """Size of a node in the chart: 'count' = number of items under it; 'dollars' =
    assets + liabilities + |cash in| + |cash out| for `year` (your share)."""
    n = nodes[node_id]
    if measure == "count":
        return float(max(1, sum(node_value(nodes, c, result, year, "count") for c in n.children)))
    row = next((r for r in node_series(nodes, node_id, result, share_view) if r["Year"] == year), None)
    if row is None:
        return 0.0
    return row["Assets"] + row["Liabilities"] + abs(row["Cash in"]) + abs(row["Cash out"])


def icicle_layout(nodes, focus_id: str, result, year: int, measure: str = "count", depth: int = 3,
                  share_view: bool = True) -> List[dict]:
    """Rectangles: focus node in column 0 spanning the full height, its children in
    column 1 split by size, and so on `depth` levels deep."""
    rects = []
    cache: Dict[str, float] = {}

    def size(nid):
        if nid not in cache:
            cache[nid] = node_value(nodes, nid, result, year, measure, share_view)
        return cache[nid]

    def place(nid, level, y0, y1):
        n = nodes[nid]
        x0 = 0.0 if level == 0 else FIRST_COL + (level - 1)       # the focus column is narrower
        x1 = FIRST_COL if level == 0 else x0 + 1
        rects.append({"node_id": nid, "label": n.label, "kind": n.kind, "x0": x0, "x1": x1,
                      "y0": y0, "y1": y1, "share": n.factor, "children": len(n.children)})
        if level >= depth or not n.children:
            return
        sizes = [size(c) for c in n.children]
        total = sum(sizes)
        floor = total * 0.004 if total else 1.0          # keep zero-dollar items visible and clickable
        sizes = [max(s, floor) for s in sizes]
        total = sum(sizes)
        y = y0
        for c, s in zip(n.children, sizes):
            h = (y1 - y0) * s / total
            place(c, level + 1, y, y + h)
            y += h

    place(focus_id, 0, 0.0, 1.0)
    return rects
