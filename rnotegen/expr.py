"""Tiny arithmetic expressions for coordinates: 20mm + 3*colw, (page_width - margin)/2, ...

Supports numbers with units (px mm cm in pt %), variables, + - * /,
parentheses and unary minus. No function calls, no attribute access.
"""

from __future__ import annotations

import re

UNITS = {"px": 1.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0, "pt": 96 / 72}
_TOKEN = re.compile(r"\s*(?:(?P<num>(?:\d+\.?\d*|\.\d+))(?P<unit>px|mm|cm|in|pt|%)?|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>[-+*/()]))")


class ExprError(ValueError):
    pass


def evaluate(text: str, variables: dict[str, float], percent_base: float | None = None) -> float:
    toks = []
    pos = 0
    s = text.strip()
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m or m.end() == pos:
            raise ExprError(f"cannot read {s[pos:]!r} in expression {text!r}")
        pos = m.end()
        if m.group("num") is not None:
            v = float(m.group("num"))
            unit = m.group("unit")
            if unit == "%":
                if percent_base is None:
                    raise ExprError(f"'%' is not allowed here: {text!r}")
                v = v / 100.0 * percent_base
            elif unit:
                v *= UNITS[unit]
            toks.append(("num", v))
        elif m.group("name") is not None:
            name = m.group("name")
            if name not in variables:
                known = ", ".join(sorted(variables)) or "none"
                raise ExprError(f"unknown name {name!r} in {text!r} (variables: {known})")
            toks.append(("num", float(variables[name])))
        else:
            toks.append(("op", m.group("op")))
    if not toks:
        raise ExprError("empty expression")
    p = _Parser(toks, text)
    v = p.expr()
    if p.i != len(toks):
        raise ExprError(f"unexpected {toks[p.i][1]!r} in {text!r}")
    return v


class _Parser:
    def __init__(self, toks, text):
        self.t = toks
        self.i = 0
        self.text = text

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expr(self) -> float:
        v = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            r = self.term()
            v = v + r if op == "+" else v - r
        return v

    def term(self) -> float:
        v = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            r = self.unary()
            if op == "/" and r == 0:
                raise ExprError(f"division by zero in {self.text!r}")
            v = v * r if op == "*" else v / r
        return v

    def unary(self) -> float:
        if self.peek() == ("op", "-"):
            self.take()
            return -self.unary()
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.atom()

    def atom(self) -> float:
        kind, val = self.take()
        if kind == "num":
            return val
        if (kind, val) == ("op", "("):
            v = self.expr()
            if self.take() != ("op", ")"):
                raise ExprError(f"missing ')' in {self.text!r}")
            return v
        raise ExprError(f"unexpected {val!r} in {self.text!r}" if val else f"incomplete expression {self.text!r}")


def split_top(s: str, sep: str) -> list[str]:
    """Split on sep outside parentheses."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def split_size(s: str) -> tuple[str, str] | None:
    """'200x80', '(w-10)x80', '50mmx30mm' -> ('200', '80'). The 'x' must follow a digit, unit or ')'."""
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch in "x×X" and depth == 0 and i > 0:
            prev = s[:i]
            if re.search(r"(\d|\)|\d(px|mm|cm|in|pt|%))$", prev) and i + 1 < len(s):
                return s[:i], s[i + 1:]
    return None


_INTERP = re.compile(r"\{([^{}]+)\}")


def interpolate(text: str, variables: dict[str, float]) -> str:
    """Replace {expr} with its value when expr is a valid expression over defined variables."""
    if "{" not in text or not variables:
        return text

    def rep(m):
        inner = m.group(1)
        if not re.fullmatch(r"[\w\s.+\-*/()]+", inner) or not re.search(r"[A-Za-z_]", inner):
            return m.group(0)
        try:
            v = evaluate(inner, variables)
        except ExprError:
            return m.group(0)
        return str(int(v)) if abs(v - round(v)) < 1e-9 else f"{v:g}"

    return _INTERP.sub(rep, text)
