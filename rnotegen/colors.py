"""Color parsing: CSS names, hex, rgb()/rgba(), and a few aliases."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Color:
    """An RGBA color with components in 0.0..1.0 (Rnote's representation)."""

    r: float
    g: float
    b: float
    a: float = 1.0

    def with_alpha(self, a: float) -> "Color":
        return Color(self.r, self.g, self.b, max(0.0, min(1.0, a)))

    def to_json(self) -> dict:
        return {"r": round(self.r, 3), "g": round(self.g, 3), "b": round(self.b, 3), "a": round(self.a, 3)}

    def to_hex(self) -> str:
        return "#%02x%02x%02x" % (round(self.r * 255), round(self.g * 255), round(self.b * 255))

    def to_svg(self) -> str:
        """Returns an SVG paint value (alpha is emitted separately via opacity)."""
        return self.to_hex()

    def to_spec(self) -> str:
        """Inverse of parse_color, used when decompiling."""
        if self.a >= 0.999:
            return self.to_hex()
        return self.to_hex() + "%02x" % round(self.a * 255)

    def mix(self, other: "Color", t: float) -> "Color":
        return Color(
            self.r + (other.r - self.r) * t,
            self.g + (other.g - self.g) * t,
            self.b + (other.b - self.b) * t,
            self.a + (other.a - self.a) * t,
        )

    @staticmethod
    def from_json(d: dict | None) -> "Color | None":
        if d is None:
            return None
        return Color(float(d.get("r", 0)), float(d.get("g", 0)), float(d.get("b", 0)), float(d.get("a", 1)))


BLACK = Color(0, 0, 0)
WHITE = Color(1, 1, 1)

CSS_COLORS = {
    "aliceblue": "f0f8ff", "antiquewhite": "faebd7", "aqua": "00ffff", "aquamarine": "7fffd4",
    "azure": "f0ffff", "beige": "f5f5dc", "bisque": "ffe4c4", "black": "000000",
    "blanchedalmond": "ffebcd", "blue": "0000ff", "blueviolet": "8a2be2", "brown": "a52a2a",
    "burlywood": "deb887", "cadetblue": "5f9ea0", "chartreuse": "7fff00", "chocolate": "d2691e",
    "coral": "ff7f50", "cornflowerblue": "6495ed", "cornsilk": "fff8dc", "crimson": "dc143c",
    "cyan": "00ffff", "darkblue": "00008b", "darkcyan": "008b8b", "darkgoldenrod": "b8860b",
    "darkgray": "a9a9a9", "darkgreen": "006400", "darkgrey": "a9a9a9", "darkkhaki": "bdb76b",
    "darkmagenta": "8b008b", "darkolivegreen": "556b2f", "darkorange": "ff8c00", "darkorchid": "9932cc",
    "darkred": "8b0000", "darksalmon": "e9967a", "darkseagreen": "8fbc8f", "darkslateblue": "483d8b",
    "darkslategray": "2f4f4f", "darkslategrey": "2f4f4f", "darkturquoise": "00ced1", "darkviolet": "9400d3",
    "deeppink": "ff1493", "deepskyblue": "00bfff", "dimgray": "696969", "dimgrey": "696969",
    "dodgerblue": "1e90ff", "firebrick": "b22222", "floralwhite": "fffaf0", "forestgreen": "228b22",
    "fuchsia": "ff00ff", "gainsboro": "dcdcdc", "ghostwhite": "f8f8ff", "gold": "ffd700",
    "goldenrod": "daa520", "gray": "808080", "green": "008000", "greenyellow": "adff2f",
    "grey": "808080", "honeydew": "f0fff0", "hotpink": "ff69b4", "indianred": "cd5c5c",
    "indigo": "4b0082", "ivory": "fffff0", "khaki": "f0e68c", "lavender": "e6e6fa",
    "lavenderblush": "fff0f5", "lawngreen": "7cfc00", "lemonchiffon": "fffacd", "lightblue": "add8e6",
    "lightcoral": "f08080", "lightcyan": "e0ffff", "lightgoldenrodyellow": "fafad2", "lightgray": "d3d3d3",
    "lightgreen": "90ee90", "lightgrey": "d3d3d3", "lightpink": "ffb6c1", "lightsalmon": "ffa07a",
    "lightseagreen": "20b2aa", "lightskyblue": "87cefa", "lightslategray": "778899", "lightslategrey": "778899",
    "lightsteelblue": "b0c4de", "lightyellow": "ffffe0", "lime": "00ff00", "limegreen": "32cd32",
    "linen": "faf0e6", "magenta": "ff00ff", "maroon": "800000", "mediumaquamarine": "66cdaa",
    "mediumblue": "0000cd", "mediumorchid": "ba55d3", "mediumpurple": "9370db", "mediumseagreen": "3cb371",
    "mediumslateblue": "7b68ee", "mediumspringgreen": "00fa9a", "mediumturquoise": "48d1cc",
    "mediumvioletred": "c71585", "midnightblue": "191970", "mintcream": "f5fffa", "mistyrose": "ffe4e1",
    "moccasin": "ffe4b5", "navajowhite": "ffdead", "navy": "000080", "oldlace": "fdf5e6",
    "olive": "808000", "olivedrab": "6b8e23", "orange": "ffa500", "orangered": "ff4500",
    "orchid": "da70d6", "palegoldenrod": "eee8aa", "palegreen": "98fb98", "paleturquoise": "afeeee",
    "palevioletred": "db7093", "papayawhip": "ffefd5", "peachpuff": "ffdab9", "peru": "cd853f",
    "pink": "ffc0cb", "plum": "dda0dd", "powderblue": "b0e0e6", "purple": "800080",
    "rebeccapurple": "663399", "red": "ff0000", "rosybrown": "bc8f8f", "royalblue": "4169e1",
    "saddlebrown": "8b4513", "salmon": "fa8072", "sandybrown": "f4a460", "seagreen": "2e8b57",
    "seashell": "fff5ee", "sienna": "a0522d", "silver": "c0c0c0", "skyblue": "87ceeb",
    "slateblue": "6a5acd", "slategray": "708090", "slategrey": "708090", "snow": "fffafa",
    "springgreen": "00ff7f", "steelblue": "4682b4", "tan": "d2b48c", "teal": "008080",
    "thistle": "d8bfd8", "tomato": "ff6347", "turquoise": "40e0d0", "violet": "ee82ee",
    "wheat": "f5deb3", "white": "ffffff", "whitesmoke": "f5f5f5", "yellow": "ffff00",
    "yellowgreen": "9acd32",
}

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FUNC_RE = re.compile(r"^(rgba?)\(\s*([^)]*)\)$", re.IGNORECASE)

NONE_WORDS = {"none", "transparent", "no", "off", "false", "null"}


class ColorError(ValueError):
    pass


def parse_color(text: str) -> Color | None:
    """Parse a color spec. Returns None for 'none'/'transparent'.

    Accepts: CSS names (red, steelblue), #rgb, #rgba, #rrggbb, #rrggbbaa,
    rgb(255,0,0), rgba(255,0,0,0.5), and an optional '/alpha' suffix (red/0.5).
    """
    s = text.strip().strip('"').strip("'")
    if not s:
        raise ColorError("empty color")
    low = s.lower().replace(" ", "").replace("_", "").replace("-", "")
    if low in NONE_WORDS:
        return None
    alpha = None
    if "/" in s and not s.lower().startswith("rgb"):
        s, a = s.rsplit("/", 1)
        alpha = _parse_alpha(a)
        low = s.lower().replace(" ", "").replace("_", "").replace("-", "")
    color: Color | None = None
    if low in CSS_COLORS:
        color = _hex(CSS_COLORS[low])
    else:
        m = _HEX_RE.match(s.strip())
        if m:
            color = _hex(m.group(1))
        else:
            m = _FUNC_RE.match(s.strip())
            if m:
                parts = [p.strip() for p in re.split(r"[,\s/]+", m.group(2)) if p.strip()]
                if len(parts) not in (3, 4):
                    raise ColorError(f"bad color {text!r}")
                rgb = []
                for p in parts[:3]:
                    if p.endswith("%"):
                        rgb.append(float(p[:-1]) / 100.0)
                    else:
                        rgb.append(float(p) / 255.0)
                a = _parse_alpha(parts[3]) if len(parts) == 4 else 1.0
                color = Color(*rgb, a)
    if color is None:
        import difflib

        near = difflib.get_close_matches(low, list(CSS_COLORS), n=1)
        hint = f" (did you mean '{near[0]}'?)" if near else ""
        raise ColorError(f"unknown color {text!r}{hint}; use a CSS name like 'steelblue' or hex like '#3366cc'")
    if alpha is not None:
        color = color.with_alpha(alpha)
    return color


def _parse_alpha(a: str) -> float:
    a = a.strip()
    if a.endswith("%"):
        return float(a[:-1]) / 100.0
    v = float(a)
    return v / 255.0 if v > 1.0 else v


def _hex(h: str) -> Color:
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
    return Color(r, g, b, a)
