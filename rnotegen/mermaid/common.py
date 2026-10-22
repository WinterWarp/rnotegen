"""Helpers shared by the Mermaid diagram converters."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, replace

from ..colors import Color, parse_color
from ..diagram import TextSpec, Theme, get_theme
from ..model import BBox, Element, Style
from ..textmetrics import resolve_family


class MermaidError(ValueError):
    def __init__(self, msg: str, line: int | None = None):
        super().__init__(msg)
        self.line = line  # 1-based line inside the mermaid source


@dataclass
class Options:
    theme: Theme = field(default_factory=lambda: get_theme("default"))
    font: str = "sans-serif"
    font_size: float = 16.0
    rough: bool = False
    wrap: float = 200.0  # max label width before wrapping
    curved: bool = False
    native_arrows: bool = True
    background: Color = field(default_factory=lambda: Color(1, 1, 1))
    direction: str | None = None  # override for flowcharts

    def text(self, color: Color | None = None, size: float | None = None, bold: bool = False) -> TextSpec:
        return TextSpec(font=self.font, size=size or self.font_size, color=color or self.theme.text, bold=bold)

    def node_style(self, **over) -> Style:
        return self.theme.node_style(rough=self.rough or self.theme.rough, **over)

    def edge_style(self, **over) -> Style:
        return self.theme.edge_style(rough=self.rough or self.theme.rough, **over)


@dataclass
class Diagram:
    elements: list[Element]
    bbox: BBox
    kind: str
    title: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Source:
    lines: list[tuple[int, str]]  # (1-based line number, text) without comments/front matter
    title: str | None
    theme: str | None
    direction_hint: str | None = None


_INIT_RE = re.compile(r"%%\{\s*init\s*:(.*)\}%%", re.S)


def preprocess(src: str) -> Source:
    text = src.replace("\r\n", "\n").replace("\t", "    ")
    title = None
    theme = None
    lines = text.split("\n")
    out: list[tuple[int, str]] = []
    i = 0
    # skip leading blank lines, then YAML front matter
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip() == "---":
        j = i + 1
        while j < len(lines) and lines[j].strip() != "---":
            m = re.match(r"\s*title\s*:\s*(.+)$", lines[j])
            if m:
                title = m.group(1).strip().strip('"').strip("'")
            m = re.match(r"\s*theme\s*:\s*(\w+)", lines[j])
            if m:
                theme = m.group(1)
            j += 1
        i = j + 1
    for k in range(i, len(lines)):
        line = lines[k]
        m = _INIT_RE.search(line)
        if m:
            tm = re.search(r"['\"]?theme['\"]?\s*:\s*['\"](\w+)['\"]", m.group(1))
            if tm:
                theme = tm.group(1)
            continue
        s = line.strip()
        if s.startswith("%%"):
            continue
        # trailing comments
        if "%%" in line and '"' not in line:
            line = line[: line.index("%%")]
        out.append((k + 1, line.rstrip()))
    return Source(out, title, theme)


def clean_label(s: str) -> str:
    """Mermaid label text -> our markup text."""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    if len(s) >= 2 and s[0] == "`" and s[-1] == "`":
        s = s[1:-1]  # markdown string
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"#(\w+);", lambda m: html.unescape(f"&{m.group(1)};") if not m.group(1).isdigit() else chr(int(m.group(1))), s)
    s = html.unescape(s) if "&" in s else s
    s = re.sub(r"\bfa[bsrl]?:fa-[\w-]+\s*", "", s)
    s = s.replace("\\n", "\n")
    return s.strip()


_CSS_LEN = re.compile(r"^\s*([\d.]+)\s*(px)?\s*$")


def parse_css_style(spec: str) -> dict[str, str]:
    """'fill:#f9f,stroke:#333,stroke-width:4px' -> dict."""
    out: dict[str, str] = {}
    for part in re.split(r"[,;](?![^(]*\))", spec):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        out[k.strip().lower()] = v.strip()
    return out


def apply_css(style: Style, text_color: Color | None, css: dict[str, str]) -> tuple[Style, Color | None, bool]:
    """Apply mermaid CSS-ish properties. Returns (style, text color, bold)."""
    bold = False
    for k, v in css.items():
        try:
            if k == "fill":
                style = replace(style, fill=parse_color(v))
            elif k == "stroke":
                style = replace(style, stroke=parse_color(v))
            elif k == "stroke-width":
                m = _CSS_LEN.match(v)
                if m:
                    style = replace(style, width=float(m.group(1)))
            elif k == "stroke-dasharray":
                style = replace(style, dash="dashed_narrow" if v.strip() not in ("0", "none") else "solid")
            elif k == "color":
                text_color = parse_color(v)
            elif k == "font-weight":
                bold = v.strip() in ("bold", "bolder", "600", "700", "800", "900")
        except ValueError:
            pass
    return style, text_color, bold


def split_statements(line: str) -> list[str]:
    """Split on ';' that are outside quotes and brackets."""
    out, cur, depth, q = [], [], 0, False
    for ch in line:
        if ch == '"':
            q = not q
        elif not q and ch in "([{":
            depth += 1
        elif not q and ch in ")]}":
            depth = max(0, depth - 1)
        if ch == ";" and not q and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return [s for s in (x.strip() for x in out) if s]


def make_options(theme: str | None = None, font: str | None = None, font_size: float | None = None,
                 rough: bool = False, wrap: float | None = None, curved: bool = False,
                 background: Color | None = None, native_arrows: bool = True) -> Options:
    o = Options(theme=get_theme(theme))
    if font:
        o.font = resolve_family(font)
    else:
        o.font = o.theme.font
    if font_size:
        o.font_size = font_size
    o.rough = rough
    if wrap:
        o.wrap = wrap
    o.curved = curved
    o.native_arrows = native_arrows
    if background is not None:
        o.background = background
    return o
