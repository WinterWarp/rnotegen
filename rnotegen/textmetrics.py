"""Approximate text measurement.

Rnote lays text out with Pango using whatever font the user's system resolves.
We cannot know that font, so widths are estimated from DejaVu metrics, which
are among the widest common Linux defaults. Over-estimating is deliberate: it
keeps labels inside their boxes and keeps flowing content from overlapping.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass

# Advance widths for ASCII 32..126 in 1/100 em (measured from DejaVu fonts).
_WIDTHS = {
    "sans": (32, 40, 46, 84, 64, 95, 78, 28, 39, 39, 50, 84, 32, 36, 32, 34, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 34, 34, 84, 84, 84, 53, 100, 68, 69, 70, 77, 63, 58, 78, 75, 30, 30, 66, 56, 86, 75, 79, 60, 79, 70, 64, 61, 73, 68, 99, 68, 61, 68, 39, 34, 39, 84, 50, 50, 61, 64, 55, 64, 62, 35, 64, 63, 28, 28, 58, 28, 97, 63, 61, 64, 64, 41, 52, 39, 63, 59, 82, 59, 59, 52, 64, 34, 64, 84),
    "sans_bold": (35, 46, 52, 84, 70, 100, 87, 31, 46, 46, 52, 84, 38, 42, 38, 36, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 40, 40, 84, 84, 84, 58, 100, 77, 76, 73, 83, 68, 68, 82, 84, 37, 37, 78, 64, 100, 84, 85, 73, 85, 77, 72, 68, 81, 77, 110, 77, 72, 72, 46, 36, 46, 84, 50, 50, 68, 72, 59, 72, 68, 44, 72, 71, 34, 34, 66, 34, 104, 71, 69, 72, 72, 49, 60, 48, 71, 65, 92, 64, 65, 58, 71, 36, 71, 84),
    "serif": (32, 40, 46, 84, 64, 95, 89, 28, 39, 39, 50, 84, 32, 34, 32, 34, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 34, 34, 84, 84, 84, 54, 100, 72, 74, 76, 80, 73, 69, 80, 87, 40, 40, 75, 66, 102, 88, 82, 67, 82, 75, 68, 67, 84, 72, 103, 71, 66, 70, 39, 34, 39, 84, 50, 50, 60, 64, 56, 64, 59, 37, 64, 64, 32, 31, 61, 32, 95, 64, 60, 64, 64, 48, 51, 40, 64, 56, 86, 56, 56, 53, 64, 34, 64, 84),
    "serif_bold": (35, 44, 52, 84, 70, 95, 90, 31, 47, 47, 52, 84, 35, 42, 35, 36, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 37, 37, 84, 84, 84, 59, 100, 78, 84, 80, 87, 76, 71, 85, 94, 47, 47, 87, 70, 111, 91, 87, 75, 87, 83, 72, 74, 87, 78, 112, 78, 71, 73, 47, 36, 47, 84, 50, 50, 65, 70, 61, 70, 64, 43, 70, 73, 38, 36, 69, 38, 106, 73, 67, 70, 70, 53, 56, 46, 73, 58, 86, 60, 58, 57, 64, 36, 64, 84),
}

# Estimated line height as a multiple of the font size. DejaVu needs ~1.17,
# Noto Sans ~1.36. We pick a value in between that errs on the safe side.
LINE_HEIGHT = 1.3

MONO_WIDTH = 0.61

# Rnote (piet-cairo) lays out centered/right-aligned text relative to its own
# widest line and then shifts the block so it starts at the text origin. So
# centering a label on a point needs the *real* rendered width. Typical
# desktop fonts (Inter/Adwaita Sans, Noto Sans, Cantarell) render ~5% narrower
# than the DejaVu-based estimate, so centering uses this factor.
CENTER_WIDTH_FACTOR = 0.95

# When fontconfig and Pillow are available, text is measured with the fonts
# installed on this machine (the ones Rnote will most likely use). Widths get
# a small safety margin for hinting/kerning differences.
REAL_FONT_SAFETY = 1.03


class _RealFont:
    def __init__(self, font):
        self.font = font
        self.cache: dict[str, float] = {}

    def em(self, ch: str) -> float:
        w = self.cache.get(ch)
        if w is None:
            try:
                w = self.font.getlength(ch) / 200.0
            except Exception:
                w = 0.6
            eaw = unicodedata.east_asian_width(ch)
            if eaw in ("W", "F"):
                w = max(w, 1.0)  # usually rendered by a fallback CJK font
            elif ord(ch) >= 0x1F000:
                w = max(w, 1.2)
            if ch == "\t":
                w = 2.0
            self.cache[ch] = w
        return w


def metrics_mode() -> str:
    """'fonts' when real font metrics are used, else 'table'. Set RNOTEGEN_METRICS=table to force estimates."""
    return "fonts" if _font_for("sans-serif", False, False) is not None else "table"


@functools.lru_cache(maxsize=128)
def _font_for(family: str, bold: bool, italic: bool):
    if os.environ.get("RNOTEGEN_METRICS", "auto").lower() in ("table", "off", "estimate", "0", "no"):
        return None
    exe = shutil.which("fc-match")
    if not exe:
        return None
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    pat = family + (":weight=bold" if bold else "") + (":slant=italic" if italic else "")
    try:
        path = subprocess.run([exe, "-f", "%{file}", pat], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not path or not os.path.exists(path):
        return None
    try:
        return _RealFont(ImageFont.truetype(path, 200))
    except Exception:
        return None


def width_fn(family: str, bold: bool = False, mono: bool = False):
    """Returns ch -> width in em for the given font."""
    fam = "monospace" if mono else family
    rf = _font_for(fam, bold, False)
    if rf is not None:
        return lambda ch: rf.em(ch) * REAL_FONT_SAFETY
    cls = "mono" if mono else family_class(family)
    return lambda ch: char_width(ch, cls, bold)


def center_factor(family: str = "sans-serif") -> float:
    """Expected rendered width / estimated width (used to center text)."""
    if _font_for("monospace" if family_class(family) == "mono" else family, False, False) is not None:
        return 1.0 / REAL_FONT_SAFETY
    return CENTER_WIDTH_FACTOR

GENERIC_FAMILIES = {
    "sans": "sans-serif", "sans-serif": "sans-serif", "sansserif": "sans-serif", "sans_serif": "sans-serif",
    "serif": "serif", "mono": "monospace", "monospace": "monospace", "code": "monospace",
    "cursive": "cursive", "handwriting": "cursive",
}


def resolve_family(name: str) -> str:
    """Map short names (sans, serif, mono) to fontconfig generic families."""
    return GENERIC_FAMILIES.get(name.strip().lower(), name.strip())


def family_class(family: str) -> str:
    f = family.lower()
    if "mono" in f or "code" in f or "courier" in f or "consol" in f:
        return "mono"
    if "sans" in f or f in ("cantarell", "inter", "arial", "helvetica", "roboto", "ubuntu", "noto sans"):
        return "sans"
    if "serif" in f or "times" in f or "georgia" in f:
        return "serif"
    return "sans"


def char_width(ch: str, cls: str, bold: bool) -> float:
    """Width of one character in em."""
    o = ord(ch)
    if cls == "mono":
        w = MONO_WIDTH
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            w *= 2
        return w
    if 32 <= o <= 126:
        key = cls + ("_bold" if bold else "")
        return _WIDTHS[key][o - 32] / 100.0
    if ch == "\t":
        return 2.0
    if unicodedata.combining(ch):
        return 0.0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F"):
        return 1.05
    if o >= 0x1F000:  # emoji and pictographs
        return 1.25
    return 0.68 if bold else 0.62


@dataclass
class Run:
    """A span of text with uniform measurement-relevant attributes."""

    text: str
    bold: bool = False
    mono: bool = False
    size: float | None = None  # None = base size


def measure_runs(runs: list[Run], family: str, size: float, bold: bool = False) -> float:
    total = 0.0
    for run in runs:
        fn = width_fn(family, bold or run.bold, run.mono)
        total += sum(fn(c) for c in run.text) * (run.size or size)
    return total


def text_width(text: str, family: str = "sans-serif", size: float = 16.0, bold: bool = False) -> float:
    return measure_runs([Run(text)], family, size, bold)


def wrap_ranges(
    text: str,
    family: str,
    size: float,
    max_width: float | None,
    bold: bool = False,
    char_bold: list[bool] | None = None,
    char_mono: list[bool] | None = None,
) -> list[tuple[int, int, float]]:
    """Greedy word wrap similar to Pango's.

    Returns (start, end, width) per line; start/end index into `text`
    (trailing spaces and the newline are excluded). char_bold / char_mono
    optionally mark characters that render bold / monospace.
    """
    fns = {(b, m): width_fn(family, b, m) for b in (False, True) for m in (False, True)}

    def cw(i: int, c: str) -> float:
        b = bold or (char_bold[i] if char_bold else False)
        m = char_mono[i] if char_mono else False
        return fns[(b, m)](c) * size

    out: list[tuple[int, int, float]] = []
    base = 0
    for para in text.split("\n"):
        n = len(para)
        widths = [cw(base + i, c) for i, c in enumerate(para)]

        def emit(a: int, b: int) -> None:
            while b > a and para[b - 1] == " ":
                b -= 1
            out.append((base + a, base + b, sum(widths[a:b])))

        if max_width is None or max_width <= 0 or n == 0:
            emit(0, n)
            base += n + 1
            continue
        line_start = 0
        line_w = 0.0
        i = 0
        while i < n:
            j = i
            while j < n and para[j] != " ":
                j += 1
            k = j
            while k < n and para[k] == " ":
                k += 1
            word_w = sum(widths[i:j])
            space_w = sum(widths[j:k])
            if line_w + word_w <= max_width or i == line_start:
                if word_w > max_width and i == line_start:
                    # break a long word character by character
                    acc = 0.0
                    m = i
                    while m < j:
                        if acc + widths[m] > max_width and m > i:
                            emit(line_start, m)
                            line_start = m
                            acc = 0.0
                        acc += widths[m]
                        m += 1
                    line_w = acc + space_w
                else:
                    line_w += word_w + space_w
                i = k
            else:
                emit(line_start, i)
                line_start = i
                line_w = 0.0
        emit(line_start, n)
        base += n + 1
    return out


def wrap_lines(text: str, family: str, size: float, max_width: float | None, bold: bool = False,
               char_bold: list[bool] | None = None, char_mono: list[bool] | None = None) -> list[tuple[str, float]]:
    return [(text[a:b], w) for a, b, w in wrap_ranges(text, family, size, max_width, bold, char_bold, char_mono)]


def measure_text(
    text: str,
    family: str = "sans-serif",
    size: float = 16.0,
    max_width: float | None = None,
    bold: bool = False,
    char_bold: list[bool] | None = None,
    char_mono: list[bool] | None = None,
) -> tuple[float, float, int]:
    """Returns (width, height, line_count) of the laid out text."""
    lines = wrap_lines(text, family, size, max_width, bold, char_bold, char_mono)
    width = max((w for _, w in lines), default=0.0)
    if max_width:
        width = min(width, max_width) if width <= max_width else width
    height = len(lines) * size * LINE_HEIGHT
    return width, height, len(lines)
