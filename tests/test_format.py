"""The .rnote output must match what Rnote 0.13/0.14 deserializes."""

import math

from conftest import load_rnote

from rnotegen.builder import Builder
from rnotegen.colors import parse_color
from rnotegen.markup import parse_inline
from rnotegen.model import (
    Arrow, CubBez, Document, Ellipse, Line, PenEl, Polygon, Polyline, QuadBez, Rect, ShapeEl, Style, TextEl,
    VectorImageEl,
)
from rnotegen.rnoteformat import FORMAT_VERSION, write_document


def all_elements_doc():
    d = Document()
    d.page.layout = "fixed_size"
    text, spans = parse_inline("Hi **bold** `mono` ü <color=red>red</color>")
    d.elements += [
        TextEl(text, 10, 20, spans=spans, max_width=300, align="center"),
        ShapeEl(Rect(10, 50, 100, 40, angle=30), Style(fill=parse_color("pink"))),
        ShapeEl(Ellipse(200, 200, 40, 20), Style(rough=True, fill=parse_color("gold"), fill_style="hachure")),
        ShapeEl(Line((0, 0), (10, 10)), Style(dash="dotted")),
        ShapeEl(Arrow((0, 0), (50, 0)), Style()),
        ShapeEl(Polyline([(0, 0), (5, 5), (10, 0)]), Style()),
        ShapeEl(Polygon([(0, 0), (5, 5), (10, 0)]), Style(fill=parse_color("blue"))),
        ShapeEl(QuadBez((0, 0), (5, 5), (10, 0)), Style()),
        ShapeEl(CubBez((0, 0), (5, 5), (8, 5), (10, 0)), Style()),
        PenEl([(0, 0, 0.5), (5, 5, 0.5)], width=3),
        PenEl([(0, 0, 0.5), (5, 5, 0.5)], width=14, highlighter=True),
        VectorImageEl('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>', 0, 0, 20, 20, (10, 10)),
    ]
    return d


def test_wrapper_and_slotmaps(tmp_path, check_with_rnote):
    d = all_elements_doc()
    p = tmp_path / "all.rnote"
    write_document(d, str(p))
    j = load_rnote(p)
    assert j["version"] == FORMAT_VERSION
    snap = j["data"]["engine_snapshot"]
    assert set(snap) == {"document", "camera", "stroke_components", "chrono_components", "chrono_counter"}
    sc, cc = snap["stroke_components"], snap["chrono_components"]
    # slotmap: first slot is a vacant sentinel; occupied slots have odd versions
    assert sc[0] == {"value": None, "version": 0} and cc[0] == {"value": None, "version": 0}
    assert len(sc) == len(cc) == len(d.elements) + 1
    assert all(s["version"] == 1 and s["value"] for s in sc[1:])
    assert [c["value"]["t"] for c in cc[1:]] == list(range(1, len(d.elements) + 1))
    assert snap["chrono_counter"] == len(d.elements)
    layers = [c["value"]["layer"] for c in cc[1:]]
    assert layers[-1] == "image" and layers[-2] == "highlighter" and layers[0] == {"user_layer": 0}
    check_with_rnote(p)


def test_document_config_layout(tmp_path):
    d = all_elements_doc()
    p = tmp_path / "doc.rnote"
    write_document(d, str(p))
    doc = load_rnote(p)["data"]["engine_snapshot"]["document"]
    cfg = doc["config"]
    assert set(cfg) == {"format", "background", "layout"}
    assert cfg["layout"] == "fixed_size"
    assert math.isclose(cfg["format"]["width"], 793.701, abs_tol=0.01)
    assert doc["height"] >= cfg["format"]["height"]


def test_transforms_are_nalgebra_affine(tmp_path):
    d = Document()
    d.elements = [ShapeEl(Rect(0, 0, 100, 50, angle=90), Style()), TextEl("x", 7, 9)]
    p = tmp_path / "t.rnote"
    write_document(d, str(p))
    sc = load_rnote(p)["data"]["engine_snapshot"]["stroke_components"]
    rect = sc[1]["value"]["shapestroke"]["shape"]["rect"]
    assert rect["cuboid"] == {"half_extents": [50.0, 25.0]}
    a = rect["transform"]["affine"]
    assert len(a) == 9 and a[2] == 0 and a[5] == 0 and a[8] == 1
    assert (a[6], a[7]) == (50.0, 25.0)  # translation = rect center
    assert abs(a[0]) < 1e-9 and math.isclose(a[1], 1.0)  # rotated by 90 degrees (column-major)
    t = sc[2]["value"]["textstroke"]["transform"]["affine"]
    assert t == [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 7.0, 9.0, 1.0]


def test_text_ranges_are_utf8_byte_offsets(tmp_path):
    text, spans = parse_inline("ü **b**")
    d = Document(elements=[TextEl(text, 0, 0, spans=spans)])
    p = tmp_path / "u.rnote"
    write_document(d, str(p))
    ts = load_rnote(p)["data"]["engine_snapshot"]["stroke_components"][1]["value"]["textstroke"]
    assert ts["text"] == "ü b"
    (attr,) = ts["text_style"]["ranged_text_attributes"]
    assert attr == {"range": {"start": 3, "end": 4}, "attribute": {"font_weight": 700}}


def test_styles(tmp_path):
    d = Document(elements=[
        ShapeEl(Line((0, 0), (1, 1)), Style(dash="dotted", cap="straight")),
        ShapeEl(Rect(0, 0, 1, 1), Style(rough=True, fill=parse_color("red"), fill_style="crosshatch")),
    ])
    p = tmp_path / "s.rnote"
    write_document(d, str(p), seed=3)
    sc = load_rnote(p)["data"]["engine_snapshot"]["stroke_components"]
    smooth = sc[1]["value"]["shapestroke"]["style"]["smooth"]
    assert smooth["line_style"] == "dotted" and smooth["line_cap"] == "rounded"  # dotted needs rounded caps
    rough = sc[2]["value"]["shapestroke"]["style"]["rough"]
    assert rough["fill_style"] == "crosshatch" and isinstance(rough["seed"], int)


def test_script_output_loads_in_rnote(tmp_path, check_with_rnote):
    doc = Builder().run_script('h1 "T"\nbox "A" id=a\nbox "B" right_of=a id=b\nconnect a -> b "x"\n'
                               'mermaid\ngraph LR\n  A-->B\nend\n')
    p = tmp_path / "s.rnote"
    write_document(doc, str(p))
    check_with_rnote(p)
