import math

import pytest

from rnotegen.builder import Builder, BuildError
from rnotegen.model import Arrow, Polygon, Rect, ShapeEl, TextEl


def build(src):
    b = Builder()
    doc = b.run_script(src)
    return b, doc


def test_exact_positions_and_anchors():
    b, doc = build("rect at=100,200 size=50x40 id=r\nrect at=300,300 size=20x20 anchor=center id=c\n"
                   "rect at=10mm,1in size=10x10 id=u\n")
    r = b.ids["r"].bbox()
    assert (r.x0, r.y0, r.w, r.h) == (100, 200, 50, 40)
    c = b.ids["c"].bbox()
    assert (c.x0, c.y0) == (290, 290)
    u = b.ids["u"].bbox()
    assert math.isclose(u.x0, 10 / 25.4 * 96) and math.isclose(u.y0, 96)


def test_relative_placement_and_references():
    b, _ = build('box "A" at=100,100 size=100x50 id=a\nbox "B" below=a gap=30 id=b\n'
                 'box "C" right_of=a gap=10 align=top id=c\ntext "t" at=a.bottomleft+0,5 id=t\n')
    a, bb, c = b.ids["a"].bbox(), b.ids["b"].bbox(), b.ids["c"].bbox()
    assert bb.y0 == a.y1 + 30 and math.isclose(bb.cx, a.cx)
    assert c.x0 == a.x1 + 10 and c.y0 == a.y0
    t = b.ids["t"].bbox()
    assert (t.x0, t.y0) == (a.x0, a.y1 + 5)


def test_flow_paginates_without_crossing_pages():
    src = "document size=a4 layout=pages\n" + "\n".join(f'para "Paragraph {i} ' + "word " * 60 + '"' for i in range(40))
    b, doc = build(src)
    ph = doc.page.height
    assert doc.page_count() > 2
    for e in doc.elements:
        bb = e.bbox()
        assert math.floor(bb.y0 / ph) == math.floor((bb.y1 - 0.01) / ph), "element crosses a page boundary"
        assert bb.y0 >= math.floor(bb.y0 / ph) * ph + doc.page.margin - 0.01


def test_page_command_sets_origin():
    b, doc = build("page 2\nrect at=10,10 size=5x5 id=r\n")
    assert b.ids["r"].bbox().y0 == doc.page.height + 10
    assert doc.page_count() == 2


def test_connect_clips_to_outline():
    b, doc = build('box "A" at=0,0 size=100x50 id=a\nbox "B" at=300,0 size=100x50 id=b shape=ellipse\nconnect a -> b\n')
    arrow = [e for e in doc.elements if isinstance(e, ShapeEl) and isinstance(e.shape, Arrow)][0]
    assert math.isclose(arrow.shape.start[0], 100) and math.isclose(arrow.shape.tip[0], 300)


def test_connect_elbow_route_and_flags():
    b, doc = build('box "A" at=0,0 size=100x50 id=a\nbox "B" at=300,200 size=100x50 id=b\n'
                   'connect a -> b "go" elbow dashed\n')
    assert not b.warnings
    texts = [e for e in doc.elements if isinstance(e, TextEl) and e.text == "go"]
    assert texts


def test_unknown_option_warns_with_suggestion():
    b, _ = build('box "x" at=10,10 colr=red\n')
    assert any("did you mean 'color'" in w for w in b.warnings)


def test_errors_are_collected_with_line_numbers():
    with pytest.raises(BuildError) as e:
        build('text "ok"\nrect 10,10\nconnect nope -> also\n')
    lines = sorted(err.line for err in e.value.errors)
    assert lines == [2, 3]


def test_off_page_warning():
    b, _ = build("document size=a4 layout=pages\nrect at=700,100 size=200x50\n")
    assert any("right page edge" in w for w in b.warnings)


def test_rounded_rect_is_polygon_and_label():
    b, doc = build('rect at=10,10 size=100x50 radius=10 label="Hi"\n')
    assert isinstance(doc.elements[0].shape, Polygon)
    assert any(isinstance(e, TextEl) and e.text == "Hi" for e in doc.elements)


def test_group_relative_coordinates():
    b, doc = build("group at=100,100 id=g\n  rect at=10,10 size=5x5\nend\n")
    r = [e for e in doc.elements if isinstance(e, ShapeEl) and isinstance(e.shape, Rect)][0]
    assert (r.shape.x, r.shape.y) == (110, 110)
    assert b.ids["g"].bbox().x0 < 111


def test_mermaid_block_fits_column():
    b, doc = build("mermaid\nflowchart LR\n  A[" + "very long label " * 3 + "] --> B --> C --> D --> E\nend\n")
    bb = doc.content_bbox()
    assert bb.x1 <= doc.page.width - doc.page.margin + 1


def test_markdown_block_and_lists():
    b, doc = build("markdown\n# Title\n- one\n- [x] two\n| a | b |\n|---|---|\n| 1 | 2 |\nend\n")
    texts = [e.text for e in doc.elements if isinstance(e, TextEl)]
    assert "Title" in texts and "one" in texts and "☑" in texts and "2" in texts


def test_dark_background_switches_ink():
    b, doc = build('document background=#202020\ntext "x" at=1,1\n')
    t = doc.elements[0]
    assert t.color.r > 0.8
