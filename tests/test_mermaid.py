import itertools

import pytest

from rnotegen.mermaid import MermaidError, render_mermaid
from rnotegen.mermaid.common import preprocess
from rnotegen.mermaid.flowchart import FlowParser
from rnotegen.model import ShapeEl, TextEl


def parse_flow(src):
    p = FlowParser()
    p.parse(preprocess(src))
    return p


def test_flowchart_node_shapes_and_links():
    p = parse_flow("""flowchart LR
        a[rect] --> b(round) --- c([stadium]) -.-> d[[sub]] ==> e[(db)]
        f((circle)) --o g>flag] --x h{diamond}
        i{{hex}} <--> j[/para/] & k[\\alt\\]
        l[/trap\\] -- text --> m[\\inv/]
        n -->|pipe label| o
        p -. dotted label .-> q
        r == thick label ==> s
        t ---> u
        v@{ shape: cyl, label: "New syntax" }
    """)
    kinds = {k: n.kind for k, n in p.nodes.items()}
    assert kinds["a"] == "rect" and kinds["b"] == "round" and kinds["c"] == "stadium"
    assert kinds["d"] == "subroutine" and kinds["e"] == "cylinder" and kinds["f"] == "circle"
    assert kinds["g"] == "asymmetric" and kinds["h"] == "diamond" and kinds["i"] == "hexagon"
    assert kinds["j"] == "parallelogram" and kinds["k"] == "parallelogram_alt"
    assert kinds["l"] == "trapezoid" and kinds["m"] == "trapezoid_alt" and kinds["v"] == "cylinder"
    assert p.nodes["v"].label == "New syntax"
    e = {(x.u, x.v): x for x in p.edges}
    assert e[("a", "b")].kind == "normal" and e[("b", "c")].end == "none"
    assert e[("c", "d")].kind == "dotted" and e[("d", "e")].kind == "thick"
    assert e[("f", "g")].end == "circle_open" and e[("g", "h")].end == "cross"
    assert e[("i", "j")].start == "arrow" and e[("i", "k")].end == "arrow"
    assert e[("l", "m")].label == "text" and e[("n", "o")].label == "pipe label"
    assert e[("p", "q")].label == "dotted label" and e[("p", "q")].kind == "dotted"
    assert e[("r", "s")].label == "thick label" and e[("t", "u")].minlen == 2


def test_flowchart_subgraphs_and_styles():
    p = parse_flow("""graph TD
        A --> B
        subgraph one [First group]
          direction LR
          B --> C
        end
        classDef hot fill:#f00,stroke:#900
        class C hot
        style A fill:#0f0
        C:::hot --> D
    """)
    sub = p.root.subs[0]
    assert sub.title == "First group" and set(sub.nodes) == {"B", "C"} and sub.direction == "LR"
    assert "hot" in p.nodes["C"].classes and p.nodes["A"].css["fill"] == "#0f0"


def _node_boxes(d):
    # node shapes are the filled shapes with a label centered inside; use all closed shapes with fill
    return [e.bbox() for e in d.elements if isinstance(e, ShapeEl) and e.style.fill is not None and e.bbox().w > 30]


def test_flowchart_layout_has_no_overlapping_nodes():
    d = render_mermaid("""flowchart TD
        A[Start] --> B{Check}
        B -->|yes| C[Do it]
        B -->|no| D[Skip]
        C --> E[End]
        D --> E
        E --> A
        subgraph S
          F --> G
          G --> H
        end
        A --> F
        H --> E
    """)
    boxes = [e.bbox() for e in d.elements if isinstance(e, ShapeEl) and e.style.fill is not None
             and e.style.stroke is not None and 30 < e.bbox().w < 300]
    for a, b in itertools.combinations(boxes, 2):
        if a.x0 <= b.x0 and a.y0 <= b.y0 and a.x1 >= b.x1 and a.y1 >= b.y1:
            continue  # cluster containing a node
        if b.x0 <= a.x0 and b.y0 <= a.y0 and b.x1 >= a.x1 and b.y1 >= a.y1:
            continue
        assert not a.intersects(b, tol=1.0), (a, b)


@pytest.mark.parametrize("src", [
    "sequenceDiagram\n actor U as User\n U->>+S: hi\n S-->>-U: ok\n Note over U,S: n\n loop x\n U-)S: ping\n end\n alt a\n U-xS: no\n else b\n U->>S: yes\n end",
    "stateDiagram-v2\n [*] --> A\n A --> B: go\n state B {\n  [*] --> C\n  C --> [*]\n }\n B --> [*]\n state f <<fork>>\n A --> f\n note right of A : hello",
    "classDiagram\n class A {\n  <<interface>>\n  +int x\n  +foo() void\n }\n A <|-- B\n B \"1\" *-- \"many\" C : has\n C ..> D\n D : +bar()$",
    "erDiagram\n A ||--o{ B : has\n B }|..|{ C : uses\n A {\n  string id PK\n  int n FK \"comment\"\n }",
    "mindmap\n  root((Root))\n    A\n      A1\n    B[Square]\n    C(Round)",
    "pie showData title T\n  \"a\" : 1\n  \"b\" : 2.5",
    "timeline\n title T\n section S\n  2020 : a : b\n  2021 : c",
    "gantt\n dateFormat YYYY-MM-DD\n section S\n A :a1, 2024-01-01, 5d\n B :after a1, 3d\n C :milestone, 2024-01-10, 0d",
])
def test_all_diagram_types_render(src):
    d = render_mermaid(src)
    assert d.elements and d.bbox.w > 10 and d.bbox.h > 10
    assert abs(d.bbox.x0) < 1e-6 and abs(d.bbox.y0) < 1e-6
    assert any(isinstance(e, TextEl) for e in d.elements)


def test_errors_have_line_numbers():
    with pytest.raises(MermaidError) as e:
        render_mermaid("flowchart TD\n  A --> B\n  A ??? C\n")
    assert e.value.line == 3
    with pytest.raises(MermaidError):
        render_mermaid("flowchart TD\n subgraph x\n A\n")
    with pytest.raises(MermaidError) as e:
        render_mermaid("journeyX\n")
    assert "supported" in str(e.value)


def test_front_matter_title_and_init_theme():
    d = render_mermaid("---\ntitle: Hello\n---\n%%{init: {'theme': 'forest'}}%%\ngraph TD\n A-->B\n")
    assert d.title == "Hello"
    assert any(isinstance(e, TextEl) and e.text == "Hello" for e in d.elements)
