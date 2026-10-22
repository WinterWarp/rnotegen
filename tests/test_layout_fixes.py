"""Flow layout: page breaks, keep-with-next, reserved space, and placement warnings."""

import math

import pytest

from rnotegen.builder import Builder
from rnotegen.model import TextEl

LETTER = "document size=letter layout=pages\n"
PH = 1056.0  # US letter page height in px
SVG_BLOCK = ('svg width=300\n<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300">'
             '<rect width="300" height="300" fill="gold"/></svg>\nend\n')
BLOCKS = {
    "group": "group\n  rect 0,0 300x300 id=blk\nend\n",
    "image": SVG_BLOCK,
    "table": "table\n|a|b|\n|-|-|\n" + "".join(f"|{i}|x|\n" for i in range(12)) + "end\n",
    "callout": 'callout "' + "word " * 200 + '"\n',
    "code": "code\n" + "".join(f"line {i}\n" for i in range(14)) + "end\n",
}


def build(src, base_dir="."):
    b = Builder(base_dir=base_dir)
    doc = b.run_script(src)
    return b, doc


def page_of(y):
    return math.floor(y / PH)


def text_el(doc, text):
    return next(e for e in doc.elements if isinstance(e, TextEl) and e.text == text)


# ---------------------------------------------------------------- bug 3: x= without y=


def test_x_without_y_breaks_pages_like_the_flow():
    b, _ = build(LETTER + "space 900\nrect size=300x120 x=100 id=r\n")
    r = b.ids["r"].bbox()
    assert r.x0 == 100
    assert page_of(r.y0) == 1 and r.y0 >= PH + 56 - 1  # moved to the top of page 2, like the flow
    assert not any("crosses a page boundary" in w for w in b.warnings), b.warnings


@pytest.mark.parametrize("cmd", ['text "label" x=100 id=t', "group x=100 id=t\n  rect 0,0 200x200\nend",
                                 'box "B" x=100 size=200x200 id=t'])
def test_x_only_placement_never_straddles_a_page(cmd):
    b, _ = build(LETTER + "space 960\n" + cmd + "\n")
    bb = b.ids["t"].bbox()
    assert page_of(bb.y0) == page_of(bb.y1 - 1), (bb.y0, bb.y1)


# ---------------------------------------------------------------- bug 4: headings keep with the next block


@pytest.mark.parametrize("level", ["h1", "h2", "h3", "h4"])
@pytest.mark.parametrize("with_para", [False, True])
@pytest.mark.parametrize("kind", list(BLOCKS))
def test_heading_moves_with_the_block_after_it(kind, with_para, level):
    para = 'para "One short line under the heading."\n' if with_para else ""
    _, doc = build(LETTER + f'space 780\n{level} "Heading"\n' + para + BLOCKS[kind])
    heading = text_el(doc, "Heading")
    block = [e for e in doc.elements if not (isinstance(e, TextEl) and e.text in ("Heading", "One short line under the heading."))]
    first = min(e.bbox().y0 for e in block)
    assert page_of(first) == 1, "the block moved to the next page"
    assert page_of(heading.y) == 1, "the heading stayed behind"
    assert heading.y < first
    if with_para:
        p = text_el(doc, "One short line under the heading.")
        assert page_of(p.y) == 1 and heading.y < p.y < first


def test_long_paragraph_is_not_dragged_along_with_its_heading():
    long_para = 'para "' + "words in a long paragraph " * 60 + '"\n'
    _, doc = build(LETTER + 'space 300\nh2 "Heading"\n' + long_para + BLOCKS["group"])
    assert page_of(text_el(doc, "Heading").y) == 0


def test_heading_alone_on_a_page_top_is_not_moved_again():
    # heading at the top of page 2 followed by a block taller than the rest of the page: no empty page
    _, doc = build(LETTER + 'para "page one"\nnewpage\nh2 "Heading"\ngroup\n  rect 0,0 300x930\nend\n')
    assert page_of(text_el(doc, "Heading").y) == 1


# ---------------------------------------------------------------- bug 5: reserved blank space


def test_group_min_height_keeps_block_and_space_together():
    b, doc = build(LETTER + 'space 760\ngroup min_height=300 id=q\n  para "Problem 1: compute it."\nend\npara "after"\n')
    q = b.ids["q"].bbox()
    problem = text_el(doc, "Problem 1: compute it.")
    assert page_of(problem.y) == 1 and problem.y < PH + 56 + 1  # moved with its space to the top of page 2
    assert q.h == pytest.approx(300) and q.y0 == pytest.approx(problem.y)
    after = text_el(doc, "after")
    assert after.y >= q.y1 and page_of(after.y) == 1


def test_group_min_height_in_flow_reserves_height():
    b, doc = build('group min_height=200 id=g\n  text "a" at=0,0\nend\ngroup min_height=200 id=h\n  text "b" at=0,0\nend\n')
    g, h = b.ids["g"].bbox(), b.ids["h"].bbox()
    assert g.h == pytest.approx(200) and h.y0 >= g.y1


def test_space_keep_moves_with_the_block_before_it():
    b, doc = build(LETTER + 'space 760\npara "Problem 2: draw it." id=p\nspace 250 keep\npara "after"\n')
    p = b.ids["p"].bbox()
    after = text_el(doc, "after")
    assert page_of(p.y0) == 1 and p.y0 < PH + 56 + 1
    assert after.y >= p.y1 + 250 and page_of(after.y) == 1


def test_space_keep_that_fits_is_plain_space():
    b, doc = build(LETTER + 'para "Problem" id=p\nspace 100 keep\npara "after"\n')
    p = b.ids["p"].bbox()
    after = text_el(doc, "after")
    assert page_of(p.y0) == 0 and p.y1 + 100 <= after.y <= p.y1 + 100 + 30


def test_space_without_keep_still_straddles():
    b, doc = build(LETTER + 'space 760\npara "Problem" id=p\nspace 250\npara "after"\n')
    assert page_of(b.ids["p"].bbox().y0) == 0


# ---------------------------------------------------------------- bug 6: warnings


def test_conflicting_placement_option_warning_names_the_winner():
    b, _ = build('text "x" at=300,40 x=24\nrect size=10x10 center=50,50 y=3\nbox "a" at=10,300 id=a\nbox "b" below=a x=5\n')
    assert not any("did you mean 'x'" in w or "did you mean 'y'" in w for w in b.warnings), b.warnings
    assert any(w.startswith("line 1:") and "x= is ignored because at= is set" in w for w in b.warnings), b.warnings
    assert any(w.startswith("line 2:") and "y= is ignored because center= is set" in w for w in b.warnings), b.warnings
    assert any(w.startswith("line 4:") and "x= is ignored because below= is set" in w for w in b.warnings), b.warnings


def test_known_option_without_effect_does_not_suggest_itself():
    b, _ = build("circle center=50,50 radius=10 x=5\n")
    assert len(b.warnings) == 1 and "did you mean" not in b.warnings[0], b.warnings


def test_shape_touching_the_page_top_does_not_cross_a_page():
    b, _ = build(LETTER + "rect 0,0 300x120\nrect 0,936 300x120\n")
    assert not any("crosses a page boundary" in w for w in b.warnings), b.warnings


def test_shape_really_crossing_a_page_is_reported():
    b, _ = build(LETTER + "rect 0,1000 300x120\n")
    assert any(w.startswith("line 2:") and "crosses a page boundary" in w for w in b.warnings), b.warnings


def test_markdown_heading_and_paragraph_move_with_the_table():
    md = "markdown\n## Results\nOne short line.\n\n" + "| a | b |\n|---|---|\n" + "".join(f"| {i} | x |\n" for i in range(12)) + "end\n"
    _, doc = build(LETTER + "space 760\n" + md)
    assert page_of(text_el(doc, "Results").y) == 1 and page_of(text_el(doc, "One short line.").y) == 1


def test_bad_anchor_on_a_rect_is_an_error_not_a_crash():
    from rnotegen.builder import BuildError

    with pytest.raises(BuildError) as e:
        build("rect 10,10 20x20 anchor=foo\n")
    assert "unknown anchor" in str(e.value)
