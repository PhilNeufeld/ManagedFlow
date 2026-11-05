"""The user manual: one source (docs/manual_source.md), a generic edition from the fictional
example household and a private edition from any portfolio."""
from pathlib import Path

import pytest

import example_data
import manual
from models import Entity, Portfolio, Assumptions, Scenario


@pytest.fixture(scope="module")
def generic():
    p = example_data.build_example_portfolio()
    p.apply_event_dates()
    return manual.build_blocks(p)


def test_parser_reads_the_markdown_subset():
    blocks = manual.parse("# One\n\nSome **bold** text\ncontinued.\n\n- a\n- [x] done\n  more\n\n1. first\n2. second\n\n"
                          "| A | B |\n|---|---|\n| 1 | 2 |\n\n> a note\n\n```\ncode\n```\n{{example:share}}\n")
    kinds = [b[0] for b in blocks]
    assert kinds == ["h", "p", "bullets", "steps", "table", "note", "code", "example"]
    assert blocks[1][1] == "Some **bold** text continued."
    assert blocks[2][1] == ["a", "✓ done more"]
    assert blocks[4][2] == [["1", "2"]]


def test_every_example_marker_is_filled(generic):
    md = manual.to_markdown(generic)
    assert "{{example" not in md
    assert "Harbor Partners LP" in md and "= 20%" in md                  # the two-path share
    assert "Oak Ave mortgage" in md and "earnings test" in md
    titles = [t for t, _ in manual.sections(generic)]
    assert titles[0].startswith("1.") and any(t.startswith("9.") for t in titles)


def test_generic_edition_has_no_private_tasks(tmp_path, generic):
    tasks = tmp_path / "x_tasks.md"
    tasks.write_text("# t\n\n- [ ] secret task\n")
    p = example_data.build_example_portfolio()
    p.apply_event_dates()
    assert "secret task" not in manual.to_markdown(manual.build_blocks(p, private=False, tasks_path=tasks))
    assert "secret task" in manual.to_markdown(manual.build_blocks(p, private=True, tasks_path=tasks))


def test_examples_come_from_the_portfolio_given():
    p = Portfolio(assumptions=Assumptions(start_year=2026, end_year=2027),
                  entities=[Entity(entity_id="z", name="Zed Holdings", entity_type="llc")],
                  scenarios=[Scenario(scenario_id="b", name="B", is_base_case=True)])
    md = manual.to_markdown(manual.build_blocks(p, private=True))
    assert "Zed Holdings" in md and "Harbor" not in md
    assert "no collections yet" in md                                    # falls back to an illustration


def test_word_document_is_written(tmp_path, generic):
    from docx import Document
    out = manual.write_docx(generic, tmp_path / "m.docx", edition="Generic")
    doc = Document(out)
    headings = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
    assert "Contents" in headings and any(h.startswith("9. How do I") for h in headings)
    assert len(doc.tables) >= 5


def test_source_and_generated_names_never_collide_on_windows():
    # Windows treats MANUAL.md and manual.md as the same file: keep the names distinct ignoring case
    docs = Path(manual.ROOT_DIR) / "docs"
    assert manual.SOURCE.name.lower() != "user_manual.md"
    assert (docs / "manual_source.md").exists()
