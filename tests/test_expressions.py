import math

import pytest

from rnotegen.builder import Builder, BuildError
from rnotegen.dsl import tokenize
from rnotegen.expr import ExprError, evaluate, interpolate, split_size
from rnotegen.mermaid.common import preprocess
from rnotegen.mermaid.flowchart import FlowParser
from rnotegen.model import TextEl


def test_evaluate():
    v = {"w": 100.0}
    assert evaluate("2*w + 10", v) == 210
    assert evaluate("(w - 10)/3", v) == 30
    assert math.isclose(evaluate("10mm", v), 10 / 25.4 * 96)
    assert evaluate("-w+5", v) == -95
    assert evaluate("50%", v, percent_base=200) == 100
    with pytest.raises(ExprError):
        evaluate("w +", v)
    with pytest.raises(ExprError):
        evaluate("nope*2", v)


def test_split_size_and_interpolate():
    assert split_size("200x80") == ("200", "80")
    assert split_size("(w-10)x80") == ("(w-10)", "80")
    assert split_size("50mmx30mm") == ("50mm", "30mm")
    assert split_size("120") is None
    assert interpolate("Day {i+1} {a, b} {x}", {"i": 2}) == "Day 3 {a, b} {x}"


def test_spaced_expressions_are_joined():
    vals = [t.value for t in tokenize("rect at=margin + i*colw, 80 size=(colw - 8)x600 fill=red", 1)]
    assert vals == ["rect", "at=margin+i*colw,80", "size=(colw-8)x600", "fill=red"]
    assert [t.value for t in tokenize("let c = (page_width - 2*margin) / 7", 1)] == ["let", "c=(page_width-2*margin)/7"]
    assert [t.value for t in tokenize("line 10,10 -5,20", 1)] == ["line", "10,10", "-5,20"]


def test_let_and_repeat():
    b = Builder()
    doc = b.run_script("document size=a4 orientation=landscape\nlet colw = (page_width - 2*margin) / 7\n"
                       "repeat 7 i\n  rect at=margin + i*colw, 80 size=(colw - 8)x600 id=col{i}\n"
                       "  text \"Day {i+1}\" center=margin + i*colw + colw/2, 100\nend\n"
                       "repeat 2 n from=1\n  bullet \"Point {n}\"\nend\n")
    cols = [b.ids[f"col{i}"].bbox() for i in range(7)]
    assert math.isclose(cols[0].x0, doc.page.margin - 1, abs_tol=1.1)
    assert math.isclose(cols[6].x1, doc.page.width - doc.page.margin - 8 + 1, abs_tol=1.1)
    texts = [e.text for e in doc.elements if isinstance(e, TextEl)]
    assert "Day 1" in texts and "Day 7" in texts and "Point 1" in texts and "Point 2" in texts
    assert not b.warnings


def test_unknown_variable_is_an_error():
    with pytest.raises(BuildError) as e:
        Builder().run_script("rect at=colw*2,10 size=5x5\n")
    assert "colw" in str(e.value.errors[0])


def test_table_never_splits_words():
    rows = "| # | Decision | Owner | Status |\n|---|---|---|---|\n" + \
           "| 1 | " + "long words here " * 12 + "| Tomas | Pending review of the thing |\n"
    b = Builder()
    doc = b.run_script("table\n" + rows + "end\n")
    owner = [e for e in doc.elements if isinstance(e, TextEl) and e.text == "Owner"][0]
    assert owner.measure()[2] == 1


def test_mermaid_click_as_node_id():
    p = FlowParser()
    p.parse(preprocess("flowchart TD\n  a --> click\n  click -->|yes| b\n  click a callback\n"))
    assert ("click", "b") in [(e.u, e.v) for e in p.edges]
    assert p.warnings  # the real click directive is reported as ignored
