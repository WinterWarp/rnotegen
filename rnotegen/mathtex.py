"""TeX math as vector paths, through matplotlib's mathtext (an optional dependency).

The glyph outlines are written as plain SVG paths, so the result needs no
fonts, has no figure background, and knows exactly where its baseline is.

mathtext is not TeX. fix_mathtext() rewrites a few inputs that it renders
badly:

- ``\\int`` followed by ``\\,`` (or directly by the integrand): the sign's
  overhang swallows the thin space, so the integrand touches it. A bare
  ``\\int`` gets ``\\!\\!\\!\\quad`` instead, ``\\int`` with limits gets ``\\ ``.
- ``x\\sin x`` renders as "xsin x": a thin space goes before ``\\sin``, ``\\cos``,
  ``\\ln``, ... when a letter, digit, ``}``, ``)`` or ``]`` comes right before.
- ``\\left[ ... \\right]_a^b`` can draw broken brackets; around content that is
  one line high, plain ``[\\, ... \\,]_a^b`` brackets have the same size.
- ``0.\\overline{3}``: mathtext puts 0.2 em after a "." unless a digit follows,
  which leaves a gap before the overline. ``\\hspace{-0.2}`` takes it back.
"""

from __future__ import annotations

import functools
import re

# matplotlib mathtext's operator names (rendered upright, with no space before them)
FUNCTIONS = ("arccos arcsin arctan arg cos cosh cot coth csc deg det dim exp gcd hom inf ker lg lim liminf limsup "
             "ln log max min Pr sec sin sinh sup tan tanh").split()
INTEGRALS = ("int", "iint", "iiint", "oint", "oiint", "oiiint")
# spacing a user wrote on purpose after an integral: left alone
SPACING = ("\\!", "\\,", "\\:", "\\;", "\\>", "\\ ", "\\quad", "\\qquad", "\\enspace", "\\thinspace", "\\hspace")
# commands that are ordinary symbols: a function name right after one needs a thin space (\pi\sin x)
ORDINARY = set("""alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi
rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi
Omega infty partial hbar ell nabla imath jmath aleph""".split())
# content that makes \left[ ... \right] taller than one line
TALL = re.compile(r"\\(?:[dtc]?frac|[dt]?binom|sqrt|sum|prod|coprod|i*int|o+i*nt|big\w+|left|over|atop|choose|"
                  r"stackrel|overset|underset|genfrac|substack|matrix|overbrace|underbrace)(?![A-Za-z])")
FONTSETS = {"cm": "cm", "tex": "cm", "latex": "cm", "computer_modern": "cm", "stix": "stix", "stixsans": "stixsans",
            "stix_sans": "stixsans", "dejavusans": "dejavusans", "dejavu_sans": "dejavusans", "sans": "dejavusans",
            "sans_serif": "dejavusans", "dejavuserif": "dejavuserif", "dejavu_serif": "dejavuserif", "serif": "dejavuserif"}

_FUNC_RE = re.compile(r"\\(" + "|".join(sorted(FUNCTIONS, key=len, reverse=True)) + r")(?![A-Za-z])")
_INT_RE = re.compile(r"\\(" + "|".join(sorted(INTEGRALS, key=len, reverse=True)) + r")(?![A-Za-z])")
_DELIM_RE = re.compile(r"\\(left|right)(?![A-Za-z])\s*(\\[A-Za-z]+|\\.|.)?")
_DECIMAL_RE = re.compile(r"(?<=\d)\.(?=\s*\\(?:overline|underline|bar|dot|ddot|hat|tilde|widehat|widetilde|vec|mathrm|"
                         r"mathbf|mathit|mathsf|mathtt|boldsymbol)(?![A-Za-z])\s*\{?\s*\d)")


class MathError(ValueError):
    pass


class MathUnavailable(ImportError):
    pass


def math_fontset(name: str) -> str:
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    if key not in FONTSETS:
        raise ValueError(f"unknown math font {name!r}; use cm (TeX look), stix, stixsans, sans or serif")
    return FONTSETS[key]


# ---------------------------------------------------------------------- quirk rewrites


def fix_mathtext(s: str) -> str:
    """Rewrite TeX that mathtext renders badly (see the module docstring). Text outside $...$ stays as it is."""
    if not re.search(r"(?<!\\)\$", s):
        return _fix(s)
    parts = re.split(r"(?<!\\)\$", s)
    return "$".join(_fix(p) if i % 2 else p for i, p in enumerate(parts))


def _fix(t: str) -> str:
    t = _fix_integrals(t)
    t = _fix_function_names(t)
    t = _fix_brackets(t)
    return _DECIMAL_RE.sub(r".\\hspace{-0.2}", t)


def _skip_space(t: str, i: int) -> int:
    while i < len(t) and t[i] in " \t\n":
        i += 1
    return i


def _skip_argument(t: str, i: int) -> int:
    """End of the TeX argument starting at i: {...}, \\command or one character."""
    i = _skip_space(t, i)
    if i >= len(t):
        return i
    if t[i] == "{":
        depth = 0
        for k in range(i, len(t)):
            if t[k] == "{" and (k == 0 or t[k - 1] != "\\"):
                depth += 1
            elif t[k] == "}" and t[k - 1] != "\\":
                depth -= 1
                if depth == 0:
                    return k + 1
        return len(t)
    if t[i] == "\\":
        m = re.match(r"\\([A-Za-z]+|.)", t[i:])
        return i + (m.end() if m else 1)
    return i + 1


def _fix_integrals(t: str) -> str:
    out, last = [], 0
    for m in _INT_RE.finditer(t):
        if m.start() < last:
            continue
        j, limits = m.end(), False
        while True:  # sub- and superscripts (limits)
            k = _skip_space(t, j)
            if k < len(t) and t[k] in "_^":
                j, limits = _skip_argument(t, k + 1), True
            else:
                break
        k = _skip_space(t, j)
        if t.startswith("\\,", k):
            k = _skip_space(t, k + 2)
        rest = t[k:]
        if not rest or rest[0] in "}$&" or rest.startswith(SPACING) or _INT_RE.match(rest):
            continue  # nothing follows, or the spacing was chosen on purpose
        out.append(t[last:j] + ("\\ " if limits else "\\!\\!\\!\\quad "))
        last = k
    return "".join(out) + t[last:]


def _ends_ordinary(t: str, k: int) -> bool:
    """Does t[:k+1] end with an ordinary symbol (letter, digit, closing bracket, Greek letter)?"""
    c = t[k]
    if c in "})]":
        return True
    if not c.isalnum():
        return False
    j = k
    while j >= 0 and t[j].isalpha():
        j -= 1
    if j >= 0 and t[j] == "\\" and j < k:
        return t[j + 1 : k + 1] in ORDINARY  # a command: only symbols, not \cdot or \quad
    return True


def _fix_function_names(t: str) -> str:
    out, last = [], 0
    for m in _FUNC_RE.finditer(t):
        k = m.start() - 1
        while k >= 0 and t[k] in " \t":
            k -= 1
        if k >= 0 and _ends_ordinary(t, k):
            out.append(t[last : m.start()] + "\\,")
            last = m.start()
    return "".join(out) + t[last:]


def _fix_brackets(t: str) -> str:
    stack, pairs = [], []
    for m in _DELIM_RE.finditer(t):
        if m.group(1) == "left":
            stack.append(m)
        elif stack:
            pairs.append((stack.pop(), m))
    edits = []
    for lm, rm in pairs:
        if lm.group(2) != "[" or rm.group(2) != "]":
            continue
        after = _skip_space(t, rm.end())
        inner = t[lm.end() : rm.start()]
        if after < len(t) and t[after] in "_^" and not TALL.search(inner):
            edits += [(lm.start(), lm.end(), "[\\,"), (rm.start(), rm.end(), "\\,]")]
    for a, b, rep in sorted(edits, reverse=True):
        t = t[:a] + rep + t[b:]
    return t


# ---------------------------------------------------------------------- rendering


def render_math(tex: str, size: float, color: str, opacity: float = 1.0, fontset: str = "dejavusans",
                min_ascent: float = 0.0, min_descent: float = 0.0) -> tuple[str, float, float, float]:
    """Render TeX to an SVG of glyph outlines. Returns (svg, width, height, baseline from the top).

    The box is at least min_ascent above and min_descent below the baseline, so that a
    formula is as tall as the text line it stands in. Raises MathUnavailable without
    matplotlib and MathError for TeX that mathtext cannot parse.
    """
    try:
        import matplotlib.textpath  # noqa: F401 (optional dependency, imported when needed)
    except ImportError as e:
        raise MathUnavailable(f"math needs matplotlib ({e})") from None
    return _render(tex, round(size, 4), color, round(opacity, 4), fontset, round(min_ascent, 4), round(min_descent, 4))


@functools.lru_cache(maxsize=512)
def _render(tex, size, color, opacity, fontset, min_ascent, min_descent):
    import numpy as np
    from matplotlib.font_manager import FontProperties
    from matplotlib.path import Path
    from matplotlib.textpath import TextToPath

    s = tex.strip()
    if not re.search(r"(?<!\\)\$", s):
        s = f"${s}$"
    prop = FontProperties(size=size, math_fontfamily=fontset)
    t2p = TextToPath()
    try:
        w, h, d = t2p.get_text_width_height_descent(s, prop, ismath=True)
        glyph_info, glyph_map, rects = t2p.get_glyphs_mathtext(prop, s)
    except ValueError as e:
        msg = " ".join(line.strip() for line in str(e).splitlines() if line.strip() and set(line.strip()) != {"^"})
        raise MathError(msg) from None
    k = size / t2p.FONT_SCALE
    shapes = []  # one path per glyph: overlapping glyphs must not cancel out under the nonzero fill rule
    for gid, x, y, scale in glyph_info:
        verts, codes = glyph_map[gid]
        shapes.append(Path((np.asarray(verts) * scale + [x, y]) * k, codes))
    for verts, codes in rects:
        shapes.append(Path(np.asarray(verts) * k, codes))
    xs = [v[0] for p in shapes for v in p.vertices] or [0.0]
    ys = [v[1] for p in shapes for v in p.vertices] or [0.0]
    ascent = max(h - d, max(ys), min_ascent)
    descent = max(d, -min(ys), min_descent)
    x0 = min(0.0, min(xs))
    width = max(w, max(xs)) - x0
    height = ascent + descent
    paint = f'fill="{color}"' + (f' fill-opacity="{opacity:g}"' if opacity < 1 else "")
    body = []
    for p in shapes:
        cmds = []
        for seg, code in p.iter_segments(simplify=False, curves=True):
            pts = " ".join(f"{seg[i] - x0:.2f} {ascent - seg[i + 1]:.2f}" for i in range(0, len(seg), 2))
            if code == Path.CLOSEPOLY:
                cmds.append("Z")
            elif code != Path.STOP:
                cmds.append({Path.MOVETO: "M", Path.LINETO: "L", Path.CURVE3: "Q", Path.CURVE4: "C"}[code] + pts)
        if cmds:
            body.append(f'<path {paint} d="{"".join(cmds)}"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.3f}" height="{height:.3f}" '
           f'viewBox="0 0 {width:.3f} {height:.3f}">' + "".join(body) + "</svg>")
    return svg, width, height, ascent
