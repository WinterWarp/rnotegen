"""Parser for rnotegen scripts (.rns).

One command per line:   COMMAND [values...] [key=value...] [flags...]
Block commands (mermaid, markdown, text without a string, group, ...) take
the following lines as their body, up to a line containing only `end`.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field


class ScriptError(Exception):
    def __init__(self, line: int, msg: str, hint: str | None = None):
        super().__init__(msg)
        self.line = line
        self.msg = msg
        self.hint = hint

    def __str__(self) -> str:
        s = f"line {self.line}: {self.msg}"
        if self.hint:
            s += f"\n    hint: {self.hint}"
        return s


@dataclass
class Token:
    value: str
    quoted: bool = False
    is_kv: bool = False  # a key=value token whose value was quoted or parenthesized


@dataclass
class Stmt:
    cmd: str
    line: int
    pos: list[Token] = field(default_factory=list)
    kw: dict[str, Token] = field(default_factory=dict)
    kw_lines: dict[str, str] = field(default_factory=dict)  # original key spelling
    body: list[str] | None = None
    body_line: int = 0
    children: list["Stmt"] | None = None
    raw: str = ""


COMMANDS = {
    # setup
    "document": "document", "doc": "document", "setup": "document", "page_setup": "document", "paper": "document",
    "defaults": "defaults", "default": "defaults", "set": "defaults", "style": "defaults",
    "page": "page", "goto_page": "page",
    "newpage": "newpage", "pagebreak": "newpage", "page_break": "newpage", "new_page": "newpage",
    "space": "space", "vspace": "space", "skip": "space", "spacer": "space",
    # text
    "text": "text", "label": "text", "t": "text", "write": "text",
    "para": "para", "p": "para", "paragraph": "para",
    "h1": "h1", "h2": "h2", "h3": "h3", "h4": "h4", "heading": "heading", "title": "h1", "header": "heading",
    "bullet": "bullet", "li": "bullet", "item": "bullet", "list_item": "bullet",
    "todo": "todo", "task": "todo", "checkbox": "todo",
    "quote": "quote", "blockquote": "quote", "callout": "callout", "note": "callout",
    "hr": "hr", "rule": "hr", "divider": "hr", "separator": "hr",
    # shapes
    "rect": "rect", "rectangle": "rect", "square": "rect",
    "ellipse": "ellipse", "oval": "ellipse", "circle": "circle",
    "line": "line", "polyline": "line", "path": "line",
    "arrow": "arrow",
    "polygon": "polygon", "triangle": "polygon",
    "curve": "curve", "quadratic": "curve", "bezier": "bezier", "cubic": "bezier",
    "pen": "pen", "draw": "pen", "freehand": "pen", "stroke": "pen",
    "highlight": "highlight", "highlighter": "highlight", "marker": "highlight",
    # composites
    "box": "box", "node": "box", "shape": "box",
    "connect": "connect", "link": "connect", "edge": "connect", "connector": "connect",
    "group": "group", "frame": "group",
    "let": "let", "var": "let", "const": "let", "variable": "let",
    "repeat": "repeat", "for": "repeat", "loop": "repeat", "times": "repeat",
    # blocks
    "mermaid": "mermaid", "diagram": "mermaid",
    "markdown": "markdown", "md": "markdown",
    "svg": "svg", "image": "image", "img": "image", "picture": "image", "png": "image",
    "table": "table", "code": "code", "codeblock": "code",
    "math": "math", "tex": "math", "latex": "math", "equation": "math", "formula": "math",
}

ALWAYS_BLOCK = {"mermaid", "markdown", "table", "code", "group", "repeat"}
BLOCK_IF_NO_STRING = {"text", "para", "quote", "callout", "math"}

_MERMAID_OPENERS = {
    "flowchart": ("subgraph",), "graph": ("subgraph",),
    "sequencediagram": ("loop", "alt", "opt", "par", "critical", "break", "rect", "box"),
}

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)"


def _strip_comment(line: str) -> str:
    """Remove a trailing ' # comment' / ' // comment' outside quotes."""
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == "\\":
                continue
            if ch == q:
                q = None
            continue
        if ch in "\"'" and (i == 0 or line[i - 1] in " =(,"):
            q = ch
            continue
        if ch == "#" and i > 0 and line[i - 1] in " \t" and (i + 1 == len(line) or line[i + 1] in " \t#"):
            return line[:i].rstrip()
        if ch == "/" and line.startswith("//", i) and i > 0 and line[i - 1] in " \t" and not line[:i].rstrip().endswith(":"):
            return line[:i].rstrip()
    return line


def tokenize(line: str, lineno: int, raw_strings: bool = False) -> list[Token]:
    """raw_strings: backslashes in quoted strings stay as written (TeX), except before the closing quote."""
    toks: list[Token] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch in " \t":
            i += 1
            continue
        if ch in "\"'(":
            tok, i = _read_value(line, i, lineno, raw_strings)
            toks.append(tok)
            continue
        j = i
        while j < n and line[j] not in " \t":
            if line[j] == "=" and j + 1 < n and line[j + 1] in "\"'(":
                break
            j += 1
        if j < n and line[j] == "=":
            val, i = _read_value(line, j + 1, lineno, raw_strings)
            toks.append(Token(line[_word_start(line, j) : j] + "=" + val.value, val.quoted, True))
            continue
        toks.append(Token(line[i:j]))
        i = j
    return _merge(toks)


def _word_start(line: str, j: int) -> int:
    k = j
    while k > 0 and line[k - 1] not in " \t":
        k -= 1
    return k


def _read_value(line: str, i: int, lineno: int, raw: bool = False) -> tuple[Token, int]:
    """Read a quoted string or a parenthesized group starting at line[i]."""
    ch = line[i]
    if ch in "\"'":
        j = i + 1
        buf = []
        while j < len(line) and line[j] != ch:
            if line[j] == "\\" and j + 1 < len(line):
                nxt = line[j + 1]
                if raw:
                    buf.append(nxt if nxt == ch else "\\" + nxt)
                else:
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'"}.get(nxt, "\\" + nxt))
                j += 2
                continue
            buf.append(line[j])
            j += 1
        if j >= len(line):
            raise ScriptError(lineno, f"unclosed {ch} quote", f"close the string with {ch}")
        return Token("".join(buf), True), j + 1
    # a parenthesized point "(10, 20)" or an expression like "(w-10)*2,40": read to the end of the word
    depth, j = 0, i
    while j < len(line):
        ch = line[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and ch in " \t":
            break
        j += 1
    if depth > 0:
        raise ScriptError(lineno, "unclosed '('")
    raw = line[i:j].replace(" ", "").replace("\t", "")
    # "(10, 20)" is a point: drop the parentheses. "(w - 10)" is an expression: keep them.
    if raw.startswith("(") and raw.endswith(")") and _single_group(raw) and "," in raw:
        raw = raw[1:-1]
    return Token(raw), j


def _single_group(s: str) -> bool:
    """True when s is one parenthesized group: '(a,b)' but not '(a)+(b)'."""
    depth = 0
    for k, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and k != len(s) - 1:
                return False
    return True


_OPS = set("+-*/")


def _merge(toks: list[Token]) -> list[Token]:
    """Join 'key = value', 'key= value', 'key =value', 'x, y' and 'x ,y' into single tokens.

    Inside option values, expressions written with spaces ('at=margin + 2*w, 80')
    are joined too.
    """
    out: list[Token] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        prev = out[-1] if out else None
        if prev is not None and not prev.quoted and not t.quoted and "=" in prev.value and not prev.value.endswith("="):
            # continuing an expression inside key=value
            if t.value in _OPS or t.value[0] in "*/+" or prev.value[-1] in _OPS or prev.value[-1] in ",(" \
                    or t.value[0] in ",)":
                if not (t.value[0] == "+" and "=" in t.value):
                    out[-1] = Token(prev.value + t.value, False, prev.is_kv)
                    i += 1
                    continue
        if prev is not None and not prev.quoted and not prev.is_kv:
            if not t.quoted and t.value == "=" and i + 1 < len(toks):
                nxt = toks[i + 1]
                out[-1] = Token(prev.value + "=" + nxt.value, nxt.quoted, nxt.quoted)
                i += 2
                continue
            if prev.value.endswith("=") and prev.value.count("=") == 1:
                out[-1] = Token(prev.value + t.value, t.quoted, t.quoted)
                i += 1
                continue
            if not t.quoted and t.value.startswith("=") and len(t.value) > 1 and "=" not in prev.value:
                out[-1] = Token(prev.value + t.value)
                i += 1
                continue
            if not t.quoted and (prev.value.endswith(",") or t.value.startswith(",")):
                out[-1] = Token(prev.value + t.value)
                i += 1
                continue
        out.append(t)
        i += 1
    return out


_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)=(.*)$", re.S)


def parse_script(text: str) -> list[Stmt]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    stmts, i = _parse_lines(lines, 0, None)
    return stmts


def _parse_lines(lines: list[str], i: int, parent: Stmt | None) -> tuple[list[Stmt], int]:
    out: list[Stmt] = []
    while i < len(lines):
        raw = lines[i]
        lineno = i + 1
        s = raw.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            i += 1
            continue
        if s.lower() in ("end", "end group", "endgroup", "/group", "end repeat", "endrepeat", "/repeat"):
            if parent is not None:
                return out, i + 1
            raise ScriptError(lineno, "'end' without an open block",
                              "remove this line, or check that the block above was opened correctly")
        # line continuation
        while s.endswith("\\") and i + 1 < len(lines):
            i += 1
            s = s[:-1].rstrip() + " " + lines[i].strip()
        s = _strip_comment(s)
        first = re.match(r"[A-Za-z_][\w-]*", s)
        tex = first is not None and COMMANDS.get(first.group(0).lower().replace("-", "_")) == "math"
        toks = tokenize(s, lineno, raw_strings=tex)
        if not toks:
            i += 1
            continue
        head = toks[0]
        word = head.value.lower().replace("-", "_")
        if head.quoted or word not in COMMANDS:
            near = difflib.get_close_matches(word, list(COMMANDS), n=1, cutoff=0.6)
            hint = f"did you mean '{near[0]}'?" if near else "see `rnotegen spec` for the list of commands"
            raise ScriptError(lineno, f"unknown command {head.value!r}", hint)
        st = Stmt(COMMANDS[word], lineno, raw=s)
        for t in toks[1:]:
            m = _KEY_RE.match(t.value) if (t.is_kv or not t.quoted) else None
            if m:
                key = m.group(1).lower().replace("-", "_")
                st.kw[key] = Token(m.group(2), t.quoted)
                st.kw_lines[key] = m.group(1)
            else:
                st.pos.append(t)
        i += 1
        is_block = st.cmd in ALWAYS_BLOCK
        if st.cmd in BLOCK_IF_NO_STRING and not any(t.quoted for t in st.pos):
            is_block = True
        if st.cmd == "svg" and "file" not in st.kw and not any(t.quoted for t in st.pos):
            is_block = True
        if st.cmd in ("group", "repeat"):
            st.children, i = _parse_group(lines, i, st)
        elif is_block:
            st.body, st.body_line, i = _read_body(lines, i, st)
        out.append(st)
    if parent is not None:
        raise ScriptError(parent.line, f"'{parent.cmd}' block is missing its closing 'end'",
                          f"add a line containing only 'end' after the {parent.cmd} body")
    return out, i


def _parse_group(lines: list[str], i: int, st: Stmt) -> tuple[list[Stmt], int]:
    return _parse_lines(lines, i, st)


def _is_end(s: str, cmd: str) -> bool:
    low = s.strip().lower()
    return low in ("end", f"end {cmd}", f"end{cmd}", f"/{cmd}", f"end_{cmd}")


def _read_body(lines: list[str], i: int, st: Stmt) -> tuple[list[str], int, int]:
    start = i
    # skip blank lines before a possible fence
    k = i
    while k < len(lines) and not lines[k].strip():
        k += 1
    m_f = re.match(r"^\s*(```|~~~)\s*([\w+-]*)", lines[k]) if k < len(lines) else None
    # a Markdown body may itself start with a code block; only ```md / ```markdown wraps it
    if m_f and (st.cmd != "markdown" or m_f.group(2).lower() in ("md", "markdown")):
        fence = m_f.group(1)
        body = []
        j = k + 1
        while j < len(lines) and not lines[j].strip().startswith(fence):
            body.append(lines[j])
            j += 1
        if j >= len(lines):
            raise ScriptError(st.line, f"'{st.cmd}' block: code fence {fence} is never closed")
        j += 1
        # optional explicit end after the fence
        m = j
        while m < len(lines) and not lines[m].strip():
            m += 1
        if m < len(lines) and _is_end(lines[m], st.cmd):
            j = m + 1
        return _dedent(body), k + 2, j
    body: list[str] = []
    depth = 0
    openers: tuple[str, ...] = ()
    in_fence = False
    j = i
    while j < len(lines):
        line = lines[j]
        s = line.strip()
        if st.cmd == "mermaid" and not openers and s and not s.startswith("%%") and not s.startswith("---"):
            first = re.split(r"[\s;]", s, maxsplit=1)[0].lower()
            openers = _MERMAID_OPENERS.get(first, ("__none__",))
        if st.cmd in ("markdown", "code") and re.match(r"^(```|~~~)", s):
            in_fence = not in_fence
        if not in_fence and _is_end(s, st.cmd):
            if depth == 0:
                return _dedent(body), start + 1, j + 1
            depth -= 1
        elif st.cmd == "mermaid" and s:
            w = re.split(r"[\s:]", s, maxsplit=1)[0].lower()
            if w in openers:
                depth += 1
        body.append(line)
        j += 1
    hint = f"add a line containing only 'end' after the {st.cmd} body"
    if st.cmd == "mermaid" and depth:
        hint = "a subgraph/loop/alt inside the diagram may be missing its own 'end' (each needs one, plus one final 'end' for the mermaid block)"
    raise ScriptError(st.line, f"'{st.cmd}' block is missing its closing 'end'", hint)


def _dedent(body: list[str]) -> list[str]:
    ind = [len(l) - len(l.lstrip()) for l in body if l.strip()]
    if not ind:
        return body
    m = min(ind)
    return [l[m:] if len(l) >= m else l.lstrip() for l in body]
