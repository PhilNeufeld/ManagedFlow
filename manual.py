"""
manual.py

ONE manual, two editions. docs/manual_source.md holds the text; lines like
{{example:assets}} are filled in from a portfolio:

  generic edition  -- the fictional example household (example_data.py); safe for GitHub
  private edition  -- your own portfolio file; kept in outputs/ (git-ignored)

build_blocks(portfolio, ...) -> a list of blocks (headings, paragraphs, lists,
tables, code, notes). to_markdown() renders them for the Help tab and
docs/USER_MANUAL.md; write_docx() renders them as a Word document.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import List, Optional

import explorer
from models import CreditLine, Loan, Portfolio, format_currency

ROOT_DIR = Path(__file__).resolve().parent
SOURCE = ROOT_DIR / "docs" / "manual_source.md"
ROADMAP = ROOT_DIR / "ROADMAP.md"
MONTHS = explorer.MONTHS

# --- parsing a small Markdown subset into blocks ------------------------------------------------

def parse(text: str) -> List[tuple]:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    blocks, para, lst, code, table = [], [], None, None, None

    def flush():
        nonlocal para, lst, table
        if para:
            blocks.append(("p", " ".join(para)))
            para = []
        if lst:
            blocks.append(lst)
            lst = None
        if table:
            blocks.append(("table", table[0], table[1:]))
            table = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if code is not None:
            if line.strip().startswith("```"):
                blocks.append(("code", code))
                code = None
            else:
                code.append(line)
            continue
        s = line.strip()
        if s.startswith("```"):
            flush()
            code = []
        elif not s:
            flush()
        elif m := re.match(r"^\{\{example:(\w+)\}\}$", s):
            flush()
            blocks.append(("example", m.group(1)))
        elif m := re.match(r"^(#{1,3})\s+(.*)", s):
            flush()
            blocks.append(("h", len(m.group(1)), m.group(2)))
        elif s.startswith("|"):
            if para or lst:
                flush()
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            table = (table or []) + [cells]
        elif s.startswith("> "):
            flush()
            blocks.append(("note", s[2:]))
        elif m := re.match(r"^- (?:\[( |x)\] )?(.*)", s):
            if para or table:
                flush()
            if lst is None or lst[0] != "bullets":
                if lst:
                    blocks.append(lst)
                lst = ("bullets", [])
            mark = {"x": "✓ ", " ": "☐ "}.get(m.group(1), "")
            lst[1].append(mark + m.group(2))
        elif m := re.match(r"^\d+\.\s+(.*)", s):
            if para or table:
                flush()
            if lst is None or lst[0] != "steps":
                if lst:
                    blocks.append(lst)
                lst = ("steps", [])
            lst[1].append(m.group(1))
        elif lst is not None and raw.startswith(" "):
            lst[1][-1] += " " + s          # continuation of a list item
        else:
            if lst:
                blocks.append(lst)
                lst = None
            para.append(s)
    flush()
    return blocks


# --- examples from a portfolio --------------------------------------------------------------------

class _Ctx:
    def __init__(self, p: Portfolio, private: bool):
        self.p = p
        self.private = private
        self.where = "your plan" if private else "the example household"
        scenario = next((s for s in p.scenarios if s.is_base_case), p.scenarios[0] if p.scenarios else None)
        self.nodes = explorer.build_tree(p)
        self.result = explorer.run(p, scenario) if scenario else None
        self.start = p.assumptions.start_year


def _money(v) -> str:
    return format_currency(v)


def _pct(x: float) -> str:
    """0.15 -> '15%', 0.0626985 -> '6.26985%', 0.003104 -> '0.3104%'."""
    s = f"{x * 100:.5f}".rstrip("0").rstrip(".")
    return f"{s}%"


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def ex_household(c: _Ctx):
    p = c.p
    kind = "your portfolio" if c.private else "the fictional example household"
    return [("note", f"**Examples in this edition come from {kind}: {p.household.name or 'Household'}** -- "
                     f"{len(p.people)} people, {len(p.entities)} entities, {len(p.assets)} assets, "
                     f"{len(p.liabilities)} liabilities, {len(p.cashflows)} cashflows; plan "
                     f"{p.assumptions.start_year}-{p.assumptions.end_year}. Amounts are your share unless noted.")]


def ex_classes(c: _Ctx):
    p = c.p
    first = lambda xs, f, none="(none yet)": f(xs[0]) if xs else none  # noqa: E731
    names = {e.entity_id: e.name for e in p.entities}
    people = {x.person_id: x.name for x in p.people}
    ent_own = [o for o in p.ownerships if o.owned_type == "entity" and o.percent < 1]
    pension = [cf for cf in p.cashflows if cf.flow_type.value == "pension_income"] or p.cashflows
    loans = [li for li in p.liabilities if isinstance(li, Loan)]
    lines = [li for li in p.liabilities if isinstance(li, CreditLine)]
    non_personal = [e for e in p.entities if e.entity_type.value not in ("personal", "joint")] or p.entities
    a = p.assumptions

    def cf_text(cf):
        freq = "/mo" if cf.frequency.value == "monthly" else "/yr"
        g = f", {cf.annual_growth_rate:.0%} growth" if cf.annual_growth_rate else ""
        return f"{cf.name}, {_money(cf.amount)}{freq}{g}"

    rows = [
        ["Person", "A member of the household; ages drive events", "Birth year and month, plan-to age",
         ", ".join(f"{x.name} ({MONTHS[x.birth_month - 1]} {x.birth_year})" for x in p.people[:2]) or "(none yet)"],
        ["Entity", "A holding structure", "Type: personal, joint, LLC, partnership, trust...",
         first(non_personal, lambda e: f"{e.name} ({e.entity_type.value})")],
        ["Membership", "Links a person to an entity, with a role", "Role: owner, trustee, manager...",
         first(p.memberships, lambda m: f"{people.get(m.person_id, m.person_id)} is {m.role.value} of "
                                        f"{names.get(m.entity_id, m.entity_id)}")],
        ["Ownership", "An entity owning a % of another entity or an asset", "Owner, owned item, percent",
         first(ent_own or p.ownerships, lambda o: f"{names.get(o.owner_entity_id)} owns {_pct(o.percent)} of "
               f"{names.get(o.owned_id) or (p.get_asset(o.owned_id).name if p.get_asset(o.owned_id) else o.owned_id)}")],
        ["Collection (Bundle)", "An optional group of records under one entity", "Entity; assets, cashflows, loans",
         first(p.bundles, lambda b: f"{b.name} ({len(b.asset_ids)} assets)")],
        ["Asset", "Something you own, with a value that can grow", "Value, growth, liquidity, tax, depreciation",
         first(p.assets, lambda x: f"{x.name}, {_money(x.initial_value)}")],
        ["Liability: Loan", "An amortizing debt", "Balance, rate, months left or payment, original terms",
         first(loans, lambda li: f"{li.name}, {_money(li.balance)} at {li.annual_interest_rate:.2%}")],
        ["Liability: Credit line", "Interest-only debt", "Balance, rate, limit",
         first(lines, lambda li: f"{li.name}, {_money(li.balance)}")],
        ["Cashflow", "Money in or out each year", "Type, amount, frequency, growth, start/end, asset, person",
         first(pension, cf_text)],
        ["Event", "A dated moment other records hang on", "Year and month, or a person's age",
         first(sorted(p.events, key=lambda e: (e.year, e.month)), lambda e: f"{MONTHS[e.month - 1]} {e.year}: {e.name}")],
        ["Actual", "A recorded amount for a year or month", "Kind, status, class, linked cashflow",
         first(p.actuals, lambda x: f"{x.year} {x.name} {_money(x.amount)}, {x.kind}, {x.status}"
                                    + (f", {x.record_class}" if x.record_class else ""))],
        ["Assumptions", "Plan-wide settings", "Years, inflation, tax method, filing status, state",
         f"{a.start_year}-{a.end_year}, {a.inflation_rate:.1%} inflation, {a.tax_method}, {a.state}"],
        ["Scenario", "A what-if on top of the plan", "Overrides, event overrides, use actuals, view",
         "; ".join(s.name for s in p.scenarios[:4]) or "(none yet)"],
    ]
    return [("table", ["Class", "What it is", "Key fields", f"Example from {c.where}"], rows)]


def _tree_lines(c: _Ctx, node_id: str, prefix: str, last: bool, out: list, parent_factor: float):
    n = c.nodes[node_id]
    if n.kind in ("Cashflow", "Liability"):
        return
    rel = n.factor / parent_factor if parent_factor else 1.0
    pct = f" {_pct(rel)}" if n.kind in ("Entity", "Asset") and node_id != explorer.ROOT and rel < 0.99999 else ""
    yours = (f"  -> {_pct(n.factor)} yours on this path"
             if n.factor < 0.99999 and pct and abs(n.factor - rel) > 1e-9 else "")
    extra = ""
    if n.kind == "Asset":
        kids = [c.nodes[k].kind for k in n.children]
        bits = [_n(kids.count("Cashflow"), "cashflow") if kids.count("Cashflow") else "",
                _n(kids.count("Liability"), "loan") if kids.count("Liability") else ""]
        extra = f"  ({', '.join(b for b in bits if b)})" if any(bits) else ""
    if n.kind == "Household" and node_id != explorer.ROOT and n.children and all(
            c.nodes[k].kind == "Cashflow" for k in n.children):
        labels = [c.nodes[k].label for k in n.children]
        extra = ": " + ", ".join(labels[:3]) + (f", +{len(labels) - 3} more" if len(labels) > 3 else "")
    label = f"{n.label}  [{n.kind}]{pct}{yours}{extra}"
    out.append(label if node_id == explorer.ROOT else prefix + ("└── " if last else "├── ") + label)
    kids = [k for k in n.children if c.nodes[k].kind not in ("Cashflow", "Liability")]
    for i, k in enumerate(kids):
        child_prefix = "" if node_id == explorer.ROOT else prefix + ("    " if last else "│   ")
        _tree_lines(c, k, child_prefix, i == len(kids) - 1, out, n.factor)


def ex_ownership_tree(c: _Ctx):
    out = []
    _tree_lines(c, explorer.ROOT, "", True, out, 1.0)
    return [("code", out)]


def ex_share(c: _Ctx):
    groups = {}
    for n in c.nodes.values():
        if n.kind in ("Entity", "Asset") and n.ref_id:
            groups.setdefault((n.kind, n.ref_id), []).append(n)
    multi = [v for v in groups.values() if len(v) > 1]
    if not multi:
        return []
    paths = max(multi, key=len)
    parts = []
    for n in paths:
        trail = [x.label for x in explorer.path(c.nodes, n.node_id)[1:-1]]
        parts.append(f"{_pct(n.factor)} (via {' > '.join(trail)})")
    total = sum(n.factor for n in paths)
    return [("p", f"**Worked example: your share of {paths[0].label}.** " + " + ".join(parts)
                  + f" = {_pct(total)}.")]


def _cf_line(c: _Ctx, cf, factor: float) -> str:
    freq = "/month" if cf.frequency.value == "monthly" else (" one-time" if cf.frequency.value == "one_time" else "/yr")
    sign = cf.direction.value == "outflow"
    amt = _money(cf.amount * factor)
    amt = f"({amt})" if sign else amt
    bits = [f"{amt}{freq}"]
    if cf.annual_growth_rate:
        bits.append(f"+{cf.annual_growth_rate:.0%}/yr")
    evs = {e.event_id: e for e in c.p.events}
    if cf.start_event_id in evs:
        e = evs[cf.start_event_id]
        bits.append(f"from {MONTHS[e.month - 1]} {e.year} ({e.name})")
    elif cf.start_year > c.start:
        bits.append(f"from {cf.start_year}")
    if cf.end_event_id in evs:
        e = evs[cf.end_event_id]
        bits.append(f"until {e.name}")
    elif cf.end_year:
        bits.append(f"through {cf.end_year}")
    return " · ".join(bits)


def _asset_block(c: _Ctx, node) -> List[str]:
    a = c.p.get_asset(node.ref_id)
    lines = [f"{a.name}  [Asset]  {_money(a.initial_value * node.factor)}"
             + (f" (your {_pct(node.factor)})" if node.factor < 0.99999 else "")
             + (f" · +{a.annual_growth_rate:.0%}/yr" if a.annual_growth_rate else "")
             + (f" · depreciation {_money(a.depreciable_basis / a.recovery_years * node.factor)}/yr"
                if a.depreciable_basis and a.recovery_years else "")]
    kids = node.children
    for i, k in enumerate(kids):
        n = c.nodes[k]
        branch = "└── " if i == len(kids) - 1 else "├── "
        if n.kind == "Cashflow":
            cf = c.p.get_cashflow(n.ref_id)
            lines.append(f"{branch}{cf.name}  [Cashflow]  {_cf_line(c, cf, n.factor)}")
        else:
            li = c.p.get_liability(n.ref_id)
            detail = f"{_money(li.balance * n.factor)} · {li.annual_interest_rate:.2%}"
            if isinstance(li, Loan):
                left = li.months_left()
                detail += f" · {left} payments left" if left is not None else ""
            else:
                detail += " · interest-only"
            lines.append(f"{branch}{li.name}  [Liability]  {detail}")
    return lines


def ex_assets(c: _Ctx, limit: int = 4):
    seen, nodes = set(), []
    for n in c.nodes.values():
        if n.kind == "Asset" and n.ref_id not in seen and n.children:
            seen.add(n.ref_id)
            nodes.append(n)
    has_loan = [n for n in nodes if any(c.nodes[k].kind == "Liability" for k in n.children)]
    nodes.sort(key=lambda n: -len(n.children))
    picked = (has_loan[:1] + [n for n in nodes if n not in has_loan[:1]])[:limit]
    out = []
    for n in picked:
        out += _asset_block(c, n) + [""]
    return [("code", out[:-1])] if out else [("p", "(No assets with cashflows yet.)")]


def ex_collections(c: _Ctx):
    p = c.p
    if not p.bundles:
        return [("note", "This portfolio has no collections yet. An illustration:"),
                ("code", ["Rentals  [Collection]  owned by an LLC",
                          "├── 12 Main St duplex  [Asset]  + rent, expenses, mortgage",
                          "├── 48 Oak Ave house   [Asset]  + rent, expenses, mortgage",
                          "└── Landlord umbrella policy  [Cashflow]  covers both",
                          "",
                          "Retirement accounts  [Collection]  owned by you",
                          "├── IRA  [Asset]",
                          "└── Brokerage  [Asset]  (surpluses land here)"])]
    names = {e.entity_id: e.name for e in p.entities}
    out = []
    for b in p.bundles[:4]:
        out.append(f"{b.name}  [Collection]  owned by {names.get(b.entity_id, b.entity_id)}")
        members = []
        for aid in b.asset_ids:
            a = p.get_asset(aid)
            if a:
                k = len(p.cashflows_for_asset(aid))
                l = len(p.liabilities_secured_by(aid))
                members.append(f"{a.name}  [Asset]  {_money(a.initial_value)}"
                               + (f"  + {_n(k, 'cashflow')}" if k else "") + (f", {_n(l, 'loan')}" if l else ""))
        members += [f"{p.get_cashflow(f).name}  [Cashflow]" for f in b.cashflow_ids if p.get_cashflow(f)]
        members += [f"{p.get_liability(x).name}  [Liability]" for x in b.liability_ids if p.get_liability(x)]
        for i, m in enumerate(members):
            out.append(("└── " if i == len(members) - 1 else "├── ") + m)
        out.append("")
    return [("code", out[:-1])]


def ex_events(c: _Ctx):
    if c.result is None:
        return []
    years = range(c.start, c.start + 3)
    evs = sorted(c.result.working_portfolio.events, key=lambda e: (e.year, e.month))
    rows = [[str(y), " · ".join(f"{MONTHS[e.month - 1]}: {e.name}" for e in evs if e.year == y) or "-"]
            for y in years]
    return [("p", f"Example of the Events row, from {c.where}:"), ("table", ["Year", "Events"], rows)]


def ex_earnings_test(c: _Ctx):
    if c.result is None:
        return []
    seen, notes = set(), []
    for r in c.result.annual_results:
        for w in r.warnings:
            m = re.search(r"of (.+?)'s \$", w)
            if "earnings test" in w and m and m.group(1) not in seen:
                seen.add(m.group(1))
                notes.append(f"**In {c.where} ({r.year}):** {w}")
    return [("note", n) for n in notes]


def ex_roadmap(c: _Ctx):
    blocks = parse(ROADMAP.read_text(encoding="utf-8")) if ROADMAP.exists() else []
    return [b for b in blocks if not (b[0] == "h" and b[1] == 1)]


def ex_tasks(c: _Ctx, tasks_path: Optional[Path] = None):
    if not c.private or tasks_path is None or not tasks_path.exists():
        return []
    blocks = [b for b in parse(tasks_path.read_text(encoding="utf-8")) if not (b[0] == "h" and b[1] == 1)]
    return [("h", 2, "Your open data tasks")] + blocks


EXAMPLES = {"household": ex_household, "classes": ex_classes, "ownership_tree": ex_ownership_tree,
            "share": ex_share, "assets": ex_assets, "collections": ex_collections, "events": ex_events,
            "earnings_test": ex_earnings_test, "roadmap": ex_roadmap, "tasks": ex_tasks}


def build_blocks(portfolio: Portfolio, private: bool = False, tasks_path: Optional[Path] = None,
                 source: Path = SOURCE) -> List[tuple]:
    c = _Ctx(portfolio, private)
    out = []
    for b in parse(source.read_text(encoding="utf-8")):
        if b[0] != "example":
            out.append(b)
        elif b[1] == "tasks":
            out += ex_tasks(c, tasks_path)
        elif b[1] in EXAMPLES:
            out += EXAMPLES[b[1]](c)
    return out


def sections(blocks) -> List[tuple]:
    """[(title, blocks)] split at level-1 headings."""
    out = []
    for b in blocks:
        if b[0] == "h" and b[1] == 1:
            out.append((b[2], []))
        elif out:
            out[-1][1].append(b)
    return out


# --- Markdown (Help tab, docs/USER_MANUAL.md) ------------------------------------------------------------

def to_markdown(blocks) -> str:
    md = []
    for b in blocks:
        kind = b[0]
        if kind == "h":
            md.append("#" * b[1] + " " + b[2])
        elif kind == "p":
            md.append(b[1])
        elif kind == "note":
            md.append("> " + b[1])
        elif kind == "bullets":
            md.append("\n".join("- " + x for x in b[1]))
        elif kind == "steps":
            md.append("\n".join(f"{i}. {x}" for i, x in enumerate(b[1], 1)))
        elif kind == "code":
            md.append("```\n" + "\n".join(b[1]) + "\n```")
        elif kind == "table":
            head, rows = b[1], b[2]
            md.append("\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                                + ["| " + " | ".join(str(x).replace("|", "/") for x in r) + " |" for r in rows]))
    return "\n\n".join(md) + "\n"


# --- Word ------------------------------------------------------------------------------------------------

def write_docx(blocks, target, title: str = "ManagedFlow User Manual", subtitle: str = "", edition: str = ""):
    """Write blocks to a .docx (path or file-like). Needs python-docx."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor, Inches

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Inches(1))
    st = doc.styles
    st["Normal"].font.name = "Arial"
    st["Normal"].font.size = Pt(10.5)
    st["Normal"].element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    for name, size, color in (("Heading 1", 17, "1F3864"), ("Heading 2", 13, "2A78D6"), ("Heading 3", 11, "000000")):
        s = st[name]
        s.font.name, s.font.size, s.font.bold = "Arial", Pt(size), True
        s.font.color.rgb = RGBColor.from_string(color)
        s.paragraph_format.space_before, s.paragraph_format.space_after = Pt(12), Pt(6)
        s.paragraph_format.keep_with_next = True

    def runs(par, text, size=None, font=None):
        for i, part in enumerate(re.split(r"\*\*", text)):
            if not part:
                continue
            r = par.add_run(part)
            r.bold = i % 2 == 1
            if size:
                r.font.size = Pt(size)
            if font:
                r.font.name = font
        return par

    def shade(el, fill):
        sh = OxmlElement("w:shd")
        sh.set(qn("w:val"), "clear"); sh.set(qn("w:color"), "auto"); sh.set(qn("w:fill"), fill)
        el.append(sh)

    def field(par, code):
        r = par.add_run()
        for tag, attr in (("w:fldChar", "begin"), ("w:instrText", None), ("w:fldChar", "separate"),
                          ("w:t", None), ("w:fldChar", "end")):
            el = OxmlElement(tag)
            if tag == "w:fldChar":
                el.set(qn("w:fldCharType"), attr)
            elif tag == "w:instrText":
                el.set(qn("xml:space"), "preserve"); el.text = code
            else:
                el.text = " "
            r._r.append(el)

    # title page
    p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(150)
    r = p.add_run("ManagedFlow"); r.bold = True; r.font.size = Pt(32)
    p = doc.add_paragraph(); r = p.add_run("User Manual"); r.font.size = Pt(20)
    r.font.color.rgb = RGBColor.from_string("52514E")
    p = doc.add_paragraph(); r = p.add_run("Household assets, cash flows, events and retirement transitions")
    r.italic = True; r.font.size = Pt(12)
    for label, value in (("Edition", edition), ("", subtitle)):
        if value:
            runs(doc.add_paragraph(), f"**{label}:** {value}" if label else value)
    p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(30)
    r = p.add_run("Tax figures are estimates, not tax advice. Check with a CPA."); r.italic = True
    r.font.size = Pt(9); r.font.color.rgb = RGBColor.from_string("52514E")
    doc.add_page_break()
    doc.add_heading("Contents", level=1)
    field(doc.add_paragraph(), 'TOC \\o "1-2" \\h \\z \\u')
    p = doc.add_paragraph(); r = p.add_run("In Word: right-click here and choose Update Field to fill in the contents.")
    r.italic = True; r.font.size = Pt(9)

    # footer page number
    fp = sec.footer.paragraphs[0]; fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = fp.add_run("ManagedFlow User Manual  ·  page "); r.font.size = Pt(8)
    field(fp, "PAGE")

    first_h1 = True
    for b in blocks:
        kind = b[0]
        if kind == "h":
            if b[1] == 1:
                doc.add_page_break() if not first_h1 or True else None
                first_h1 = False
            doc.add_heading(b[2], level=b[1])
        elif kind == "p":
            runs(doc.add_paragraph(), b[1])
        elif kind == "note":
            par = runs(doc.add_paragraph(), b[1], size=9.5)
            pf = par.paragraph_format; pf.left_indent = Inches(0.2); pf.space_after = Pt(8)
            pbdr = OxmlElement("w:pBdr"); left = OxmlElement("w:left")
            for k, v in (("w:val", "single"), ("w:sz", "18"), ("w:space", "8"), ("w:color", "2A78D6")):
                left.set(qn(k), v)
            pbdr.append(left); par._p.get_or_add_pPr().append(pbdr)
        elif kind == "bullets":
            for x in b[1]:
                par = runs(doc.add_paragraph(style="List Bullet"), x)
                par.paragraph_format.space_after = Pt(2)
        elif kind == "steps":
            for i, x in enumerate(b[1], 1):
                par = runs(doc.add_paragraph(), f"{i}.\t{x}")
                pf = par.paragraph_format
                pf.left_indent, pf.first_line_indent, pf.space_after = Inches(0.4), Inches(-0.25), Pt(2)
        elif kind == "code":
            for i, line in enumerate(b[1]):
                par = doc.add_paragraph()
                pf = par.paragraph_format
                pf.space_after = pf.space_before = Pt(0); pf.line_spacing = 1.0
                pf.left_indent = Inches(0.08)
                r = par.add_run(line or " "); r.font.name = "Consolas"; r.font.size = Pt(8)
                r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
                shade(par._p.get_or_add_pPr(), "F3F4F6")
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
        elif kind == "table":
            head, rows = b[1], b[2]
            t = doc.add_table(rows=1, cols=len(head)); t.style = "Table Grid"
            lens = [max([len(str(h))] + [len(str(r[i])) if i < len(r) else 0 for r in rows]) for i, h in enumerate(head)]
            lens = [min(max(n, 6), 60) for n in lens]
            total = sum(lens)
            widths = [Inches(6.5 * n / total) for n in lens]
            for i, h in enumerate(head):
                cell = t.rows[0].cells[i]; cell.width = widths[i]
                cell.paragraphs[0].text = ""
                rr = cell.paragraphs[0].add_run(h); rr.bold = True; rr.font.size = Pt(9)
                rr.font.color.rgb = RGBColor.from_string("FFFFFF")
                shade(cell._tc.get_or_add_tcPr(), "1F3864")
            for row in rows:
                cells = t.add_row().cells
                for i in range(len(head)):
                    cells[i].width = widths[i]
                    cells[i].paragraphs[0].text = ""
                    runs(cells[i].paragraphs[0], str(row[i]) if i < len(row) else "", size=9)
            doc.add_paragraph()
    doc.save(target)
    return target


def docx_bytes(blocks, **kw) -> bytes:
    buf = io.BytesIO()
    write_docx(blocks, buf, **kw)
    return buf.getvalue()
