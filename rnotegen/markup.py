"""Inline markup for text: a small, forgiving Markdown subset.

Supported:  **bold**  __bold__  *italic*  _italic_  ~~strike~~  `code`
            <u>underline</u>  <b>..</b>  <i>..</i>  <s>..</s>
            <color=red>colored</color>   [link text](https://...)
Backslash escapes a marker:  \\*not italic\\*
Unmatched markers are kept literally, so "5 * 3" stays "5 * 3".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .colors import Color, parse_color


@dataclass
class Span:
    start: int  # character offsets into the plain text
    end: int
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    mono: bool = False
    color: Color | None = None
    attrs: dict = field(default_factory=dict)


_SPECIALS = set("*_~`<[\\]")
_TAG_RE = re.compile(r"<(u|b|i|s|strong|em|del|code)>", re.IGNORECASE)
_COLOR_RE = re.compile(r"<color\s*=\s*\"?([^>\"]+)\"?\s*>", re.IGNORECASE)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def parse_inline(s: str, link_color: Color | None = None) -> tuple[str, list[Span]]:
    out: list[str] = []
    spans: list[Span] = []
    _parse(s, out, spans, link_color)
    return "".join(out), spans


def _find_close(s: str, marker: str, start: int) -> int:
    i = start
    while True:
        j = s.find(marker, i)
        if j == -1:
            return -1
        if j > 0 and s[j - 1] == "\\":
            i = j + 1
            continue
        if j > start and not s[j - 1].isspace():
            # For single-char markers make sure we don't match half of a double marker.
            if len(marker) == 1 and j + 1 < len(s) and s[j + 1] == marker:
                i = j + 2
                continue
            return j
        i = j + 1


def _parse(s: str, out: list[str], spans: list[Span], link_color: Color | None) -> None:
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] in _SPECIALS:
            out.append(s[i + 1])
            i += 2
            continue
        if c in "*_~":
            run = 1
            while i + run < n and s[i + run] == c:
                run += 1
            if run == 3 and c in "*_" and i + 3 < n and not s[i + 3].isspace():
                close = s.find(c * 3, i + 3)
                if close > i + 3 and not s[close - 1].isspace():
                    _wrap(s[i + 3 : close], out, spans, link_color, bold=True, italic=True)
                    i = close + 3
                    continue
            if run >= 3:  # '___' or '***' fill-in lines stay literal
                out.extend(s[i : i + run])
                i += run
                continue
        if c == "`":
            close = s.find("`", i + 1)
            if close > i + 1:
                start = len(out)
                out.extend(s[i + 1 : close])
                spans.append(Span(start, len(out), mono=True))
                i = close + 1
                continue
        for marker, kind in (("**", "bold"), ("__", "bold"), ("~~", "strike")):
            if s.startswith(marker, i) and i + 2 < n and not s[i + 2].isspace():
                close = _find_close(s, marker, i + 2)
                if close != -1:
                    _wrap(s[i + 2 : close], out, spans, link_color, **{kind: True})
                    i = close + 2
                    break
        else:
            if c in "*_" and i + 1 < n and not s[i + 1].isspace() and s[i + 1] != c:
                # '_' only opens at a word boundary (snake_case stays literal)
                if c == "*" or i == 0 or not s[i - 1].isalnum():
                    close = _find_close(s, c, i + 1)
                    if close != -1 and (c == "*" or close + 1 >= n or not s[close + 1].isalnum()):
                        _wrap(s[i + 1 : close], out, spans, link_color, italic=True)
                        i = close + 1
                        continue
            if c == "<":
                m = _TAG_RE.match(s, i)
                if m:
                    tag = m.group(1).lower()
                    close = s.lower().find(f"</{tag}>", m.end())
                    if close != -1:
                        kind = {"u": "underline", "b": "bold", "strong": "bold", "i": "italic", "em": "italic",
                                "s": "strike", "del": "strike", "code": "mono"}[tag]
                        _wrap(s[m.end() : close], out, spans, link_color, **{kind: True})
                        i = close + len(tag) + 3
                        continue
                m = _COLOR_RE.match(s, i)
                if m:
                    close = s.lower().find("</color>", m.end())
                    if close != -1:
                        try:
                            col = parse_color(m.group(1))
                        except ValueError:
                            col = None
                        _wrap(s[m.end() : close], out, spans, link_color, color=col)
                        i = close + len("</color>")
                        continue
            if c == "[":
                m = _LINK_RE.match(s, i)
                if m:
                    _wrap(m.group(1), out, spans, link_color, underline=True, color=link_color)
                    i = m.end()
                    continue
            out.append(c)
            i += 1
            continue
        continue


def _wrap(inner: str, out: list[str], spans: list[Span], link_color: Color | None, **attrs) -> None:
    start = len(out)
    _parse(inner, out, spans, link_color)
    spans.append(Span(start, len(out), **attrs))


def char_flags(text: str, spans: list[Span]) -> tuple[list[bool], list[bool]]:
    bold = [False] * len(text)
    mono = [False] * len(text)
    for sp in spans:
        for k in range(sp.start, min(sp.end, len(text))):
            if sp.bold:
                bold[k] = True
            if sp.mono:
                mono[k] = True
    return bold, mono


def byte_offsets(text: str) -> list[int]:
    """Map character index -> UTF-8 byte offset (len(text)+1 entries)."""
    offs = [0]
    acc = 0
    for ch in text:
        acc += len(ch.encode("utf-8"))
        offs.append(acc)
    return offs
