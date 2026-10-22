import pytest

from rnotegen.dsl import ScriptError, parse_script, tokenize


def toks(line):
    return [(t.value, t.quoted) for t in tokenize(line, 1)]


def test_tokenize_variants():
    assert toks('text "Hello, world" at=10,20 bold') == [
        ("text", False), ("Hello, world", True), ("at=10,20", False), ("bold", False)]
    assert toks("rect (10, 20) 200x100") == [("rect", False), ("10,20", False), ("200x100", False)]
    assert toks('box "x" at = (1, 2) fill = "#ff0000"') == [
        ("box", False), ("x", True), ("at=1,2", False), ("fill=#ff0000", True)]
    assert toks("line 0, 0 100 , 100") == [("line", False), ("0,0", False), ("100,100", False)]
    assert toks(r'text "a \"q\" \n b"') == [("text", False), ('a "q" \n b', True)]


def test_comments_and_continuation():
    st = parse_script('# c\n// c2\ntext "a #b" color=#333 # trailing\nline 0,0 \\\n  10,10\n')
    assert [s.cmd for s in st] == ["text", "line"]
    assert st[0].kw["color"].value == "#333"
    assert [t.value for t in st[1].pos] == ["0,0", "10,10"]


def test_aliases_and_unknown_command():
    st = parse_script('rectangle 0,0 10x10\noval center=5,5 radius=3\np "x"\n')
    assert [s.cmd for s in st] == ["rect", "ellipse", "para"]
    with pytest.raises(ScriptError) as e:
        parse_script('\n\nrecktangle 0,0 10x10')
    assert e.value.line == 3 and "rect" in e.value.hint


def test_mermaid_block_with_subgraph_ends():
    src = "mermaid\nflowchart TD\n  subgraph A\n    x-->y\n  end\n  subgraph B\n    z\n  end\nend\ntext \"after\"\n"
    st = parse_script(src)
    assert [s.cmd for s in st] == ["mermaid", "text"]
    assert st[0].body[-1].strip() == "end" and len(st[0].body) == 7


def test_sequence_blocks_and_fences():
    src = "mermaid\nsequenceDiagram\n  loop x\n    A->>B: hi\n  end\nend\n"
    assert len(parse_script(src)[0].body) == 4
    fenced = "mermaid\n```mermaid\ngraph TD\n  A-->B\n```\nend\ntext \"x\"\n"
    st = parse_script(fenced)
    assert st[0].body == ["graph TD", "  A-->B"] and st[1].cmd == "text"


def test_text_block_and_string_form():
    st = parse_script('text at=1,1\nline one\nline two\nend\ntext "inline"\n')
    assert st[0].body == ["line one", "line two"] and st[1].body is None


def test_group_nesting():
    st = parse_script("group at=1,1 id=g\n  rect 0,0 1x1\n  group\n    text \"x\"\n  end\nend\n")
    assert st[0].cmd == "group" and [c.cmd for c in st[0].children] == ["rect", "group"]


def test_missing_end_is_reported_at_block_start():
    with pytest.raises(ScriptError) as e:
        parse_script('h1 "x"\nmermaid\ngraph TD\n  A-->B\n')
    assert e.value.line == 2 and "end" in e.value.msg


def test_markdown_block_ignores_end_in_code_fence():
    st = parse_script("markdown\n```\nend\n```\nend\n")
    assert st[0].body == ["```", "end", "```"]
