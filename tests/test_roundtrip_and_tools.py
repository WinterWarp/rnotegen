import json

from conftest import load_rnote

from rnotegen.api import build, detect_kind
from rnotegen.decompile import decompile
from rnotegen.markdown import parse_markdown
from rnotegen.mcp_server import handle
from rnotegen.preview import document_svg


SCRIPT = """document size=a4 layout=pages pattern=grid
h1 "Title" rule
para "Some **bold** and *italic* and `code` text."
bullet "item"
box "A" at=100,400 id=a shape=round fill=#eef
box "B" right_of=a id=b shape=diamond
connect a -> b "yes" dashed
circle center=400,600 radius=30 fill=gold rough
pen 100,700 150,720 200,700 color=blue width=3
highlight 100,750 300,750
"""


def _close(x, y):
    if isinstance(x, dict):
        return x.keys() == y.keys() and all(_close(x[k], y[k]) for k in x)
    if isinstance(x, list):
        return len(x) == len(y) and all(_close(a, b) for a, b in zip(x, y))
    if isinstance(x, float) or isinstance(y, float):
        return abs(x - y) < 0.11
    if isinstance(x, int) and isinstance(y, int) and x > 2**32:
        return True  # rough-style seeds
    return x == y


def test_decompile_roundtrip(tmp_path):
    out = tmp_path / "a.rnote"
    res = build(SCRIPT, "script", str(out))
    assert res.ok, res.report()
    script, summary = decompile(str(out), pen="full")
    assert summary["strokes"]["textstroke"] >= 5
    out2 = tmp_path / "b.rnote"
    res2 = build(script, "script", str(out2))
    assert res2.ok, res2.report()
    a = [s["value"] for s in load_rnote(out)["data"]["engine_snapshot"]["stroke_components"][1:]]
    b = [s["value"] for s in load_rnote(out2)["data"]["engine_snapshot"]["stroke_components"][1:]]
    assert len(a) == len(b)
    # the decompiler emits strokes in Rnote's draw order (highlighter strokes below the rest)
    remaining = list(b)
    for x in a:
        match = next((y for y in remaining if _close(x, y)), None)
        assert match is not None, json.dumps(x)[:200]
        remaining.remove(match)


def test_append_into_existing(tmp_path, check_with_rnote):
    base = tmp_path / "base.rnote"
    assert build('text "first" at=60,60', "script", str(base)).ok
    res = build('para "second"', "script", str(tmp_path / "out.rnote"), into=str(base))
    assert res.ok
    snap = load_rnote(tmp_path / "out.rnote")["data"]["engine_snapshot"]
    texts = [s["value"]["textstroke"]["text"] for s in snap["stroke_components"][1:]]
    assert texts == ["first", "second"]
    ys = [s["value"]["textstroke"]["transform"]["affine"][7] for s in snap["stroke_components"][1:]]
    assert ys[1] > ys[0] + 20  # placed below the existing content
    assert snap["chrono_counter"] == 2
    check_with_rnote(tmp_path / "out.rnote")


def test_markdown_ops():
    ops = parse_markdown("# H\n\ntext\nmore\n\n- a\n  - b\n1. c\n- [ ] d\n> q\n\n```py\nx\n```\n---\n|a|b|\n|-|:-:|\n|1|2|\n")
    kinds = [o.kind for o in ops]
    assert kinds == ["heading", "para", "bullet", "bullet", "bullet", "todo", "quote", "code", "hr", "table"]
    assert ops[1].text == "text more" and ops[3].level == 2 and ops[4].marker == "1."
    assert ops[-1].aligns == ["left", "center"] and ops[-1].rows == [["a", "b"], ["1", "2"]]


def test_detect_kind():
    assert detect_kind("x.md", "") == "markdown"
    assert detect_kind(None, "graph TD\nA-->B") == "mermaid"
    assert detect_kind(None, "sequenceDiagram\n") == "mermaid"
    assert detect_kind(None, 'h1 "x"') == "script"


def test_preview_svg_contains_text_and_shapes():
    res = build(SCRIPT, "script", None)
    svg = document_svg(res.document)
    assert svg.startswith("<svg") and "Title" in svg and "<polygon" in svg and "<polyline" in svg  # rough circle = jittered polylines


def test_mcp_protocol(tmp_path):
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    assert r["result"]["protocolVersion"] == "2024-11-05" and "tools" in r["result"]["capabilities"]
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in tools} >= {"rnote_build", "rnote_reference", "rnote_from_mermaid", "rnote_read"}
    out = tmp_path / "m.rnote"
    r = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "rnote_build", "arguments": {"script": 'h1 "x"', "output_path": str(out), "preview": False}}})
    assert r["result"]["isError"] is False and out.exists()
    r = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
        "name": "rnote_check", "arguments": {"script": "rect 1,1"}}})
    assert r["result"]["isError"] is True and "line 1" in r["result"]["content"][0]["text"]
    assert handle({"jsonrpc": "2.0", "id": 5, "method": "nope"})["error"]["code"] == -32601
