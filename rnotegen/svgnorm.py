"""Normalize SVG files so that Rnote can render them as vector images.

Rnote does not render a vector image's ``svg_data`` as a document of its own.
``VectorImage::gen_svg`` (rnote-engine, strokes/vectorimage.rs) pastes it
inside an ``<svg>`` element of its own, and ``Svg::gen_image`` (svg.rs) hands
the result to librsvg. An XML declaration, a DOCTYPE or an undeclared
namespace prefix then makes the whole image fail to parse: Rnote shows an
empty box. In a whole-document export all images end up in one SVG document,
so a ``<style>`` sheet or an id from one image also leaks into the others.

Rnote's own importer avoids this by rewriting files through usvg. This module
does the same simplifications with the standard library only:

- the XML declaration, DOCTYPE, comments, ``<metadata>`` and everything outside
  the SVG namespace are dropped (entities from the DOCTYPE are expanded first),
- CSS (``<style>`` sheets and ``style=`` attributes) becomes presentation
  attributes,
- ``<use>`` references are replaced by copies of what they reference,
- ids get a per-image prefix, so images cannot collide,
- the root element gets its size in px.
"""

from __future__ import annotations

import copy
import hashlib
import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
_S = "{" + SVG_NS + "}"
_XLINK_HREF = "{" + XLINK_NS + "}href"
_HREFS = ("href", _XLINK_HREF)

_UNITS = {"": 1.0, "px": 1.0, "pt": 96 / 72, "pc": 16.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0}
_LEN_RE = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(px|pt|pc|mm|cm|in)?\s*$")
_URL_RE = re.compile(r"url\(\s*(['\"]?)#([^'\")\s]+)\1\s*\)")

# elements that are never rendered (or cannot be rendered inside Rnote)
_DROP = {"metadata", "title", "desc", "script", "foreignObject"}
# CSS properties that are also SVG presentation attributes (librsvg accepts them as attributes)
PRESENTATION = frozenset("""
alignment-baseline baseline-shift clip clip-path clip-rule color color-interpolation color-interpolation-filters
color-rendering cursor direction display dominant-baseline enable-background fill fill-opacity fill-rule filter
flood-color flood-opacity font-family font-size font-size-adjust font-stretch font-style font-variant font-weight
glyph-orientation-horizontal glyph-orientation-vertical image-rendering kerning letter-spacing lighting-color
marker-end marker-mid marker-start mask opacity overflow paint-order pointer-events shape-rendering stop-color
stop-opacity stroke stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit
stroke-opacity stroke-width text-anchor text-decoration text-rendering unicode-bidi vector-effect visibility
word-spacing writing-mode
""".split())


class SvgError(ValueError):
    pass


# ---------------------------------------------------------------------- sizes


def _length(v: str | None) -> float | None:
    if v is None:
        return None
    m = _LEN_RE.match(v)
    if not m:
        return None  # percentages, em, ...: no absolute size
    return float(m.group(1)) * _UNITS[(m.group(2) or "").lower()]


def _viewbox(v: str | None) -> list[float] | None:
    if not v:
        return None
    try:
        parts = [float(x) for x in re.split(r"[\s,]+", v.strip()) if x]
    except ValueError:
        return None
    return parts if len(parts) == 4 and parts[2] > 0 and parts[3] > 0 else None


def _size_from(width: str | None, height: str | None, viewbox: str | None) -> tuple[float | None, float | None]:
    w, h = _length(width), _length(height)
    vb = _viewbox(viewbox)
    if vb:
        if w is None and h is None:
            w, h = vb[2], vb[3]
        elif w is None:
            w = h * vb[2] / vb[3]
        elif h is None:
            h = w * vb[3] / vb[2]
    return w, h


def svg_intrinsic_size(svg: str) -> tuple[float | None, float | None]:
    """Size in px of an SVG document from its root's width/height (px, pt, pc, mm, cm, in) and viewBox."""
    m = re.search(r"<svg\b[^>]*>", svg, re.S)
    if not m:
        return None, None
    tag = m.group(0)

    def attr(name):
        a = re.search(rf'\s{name}\s*=\s*["\']([^"\']*)["\']', tag)
        return a.group(1) if a else None

    return _size_from(attr("width"), attr("height"), attr("viewBox"))


# ---------------------------------------------------------------------- normalization


def normalize_svg(data: str | bytes, id_prefix: str | None = None) -> tuple[str, float, float]:
    """Returns (svg, width, height): a self-contained SVG that renders inside Rnote's wrapper.

    id_prefix defaults to a hash of the content, so equal images get equal ids and
    different images never share one.
    """
    root = _parse(data)
    if root.tag == "svg":  # no xmlns: inside Rnote's wrapper it would be SVG anyway
        for e in root.iter():
            if isinstance(e.tag, str) and not e.tag.startswith("{"):
                e.tag = _S + e.tag
    if root.tag != _S + "svg":
        raise SvgError(f"the root element is <{_local(root.tag)}>, not <svg>")
    w, h = _size_from(root.get("width"), root.get("height"), root.get("viewBox"))
    if w is None or h is None or w <= 0 or h <= 0:
        raise SvgError("the <svg> has no usable width/height or viewBox")
    _drop_foreign(root)
    _apply_css(root)
    _expand_uses(root)
    _prune_defs(root)
    rest = [(k, v) for k, v in root.attrib.items() if k not in ("x", "y", "width", "height", "version", "baseProfile")]
    root.attrib.clear()
    root.set("width", _fmt(w))
    root.set("height", _fmt(h))
    for k, v in rest:
        root.set(k, v)
    if id_prefix is None:
        id_prefix = "r" + hashlib.sha1(_serialize(root).encode("utf-8")).hexdigest()[:8] + "-"
    _prefix_ids(root, id_prefix)
    return _serialize(root), w, h


def _parse(data: str | bytes) -> ET.Element:
    if isinstance(data, str):
        # str input is read as UTF-8 whatever the declaration says, so drop it
        data = re.sub(r"^\s*<\?xml[^>]*\?>", "", data.lstrip("﻿"))
    else:
        data = data.lstrip()
    parser = ET.XMLParser()  # comments and processing instructions are not kept
    try:
        parser.feed(data)
        return parser.close()
    except ET.ParseError as e:
        raise SvgError(f"not valid XML ({e})") from None


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _drop_foreign(el: ET.Element) -> None:
    for child in list(el):
        if not isinstance(child.tag, str) or not child.tag.startswith(_S) or _local(child.tag) in _DROP:
            el.remove(child)
        else:
            _drop_foreign(child)
    for k in list(el.attrib):
        if k.startswith("{") and not k.startswith(("{" + XLINK_NS, "{" + XML_NS)) or k.lower().startswith("on"):
            del el.attrib[k]


# ---------------------------------------------------------------------- CSS


def _split_top(s: str, sep: str) -> list[str]:
    out, depth, quote, cur = [], 0, None, []
    for ch in s:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return out


def _declarations(text: str) -> list[tuple[str, str, bool]]:
    out = []
    for part in _split_top(text, ";"):
        if ":" not in part:
            continue
        prop, val = part.split(":", 1)
        prop, val = prop.strip().lower(), val.strip()
        m = re.search(r"!\s*important\s*$", val, re.I)
        important = m is not None
        if m:
            val = val[: m.start()].strip()
        if prop and val:
            out.append((prop, val, important))
    return out


_COMPOUND_RE = re.compile(r"^(\*|[A-Za-z_][\w-]*)?((?:[.#][\w-]+|\[[^\]]*\])*)$")
_QUAL_RE = re.compile(r"([.#])([\w-]+)|\[\s*([\w:-]+)\s*(?:=\s*(\"[^\"]*\"|'[^']*'|[^\]\s]*))?\s*\]")


def _selector(sel: str):
    """[(combinator, tag, [qualifiers])] left to right, or None for selectors we cannot match statically."""
    parts = re.findall(r">|[^\s>]+", sel.strip())
    seq, comb = [], " "
    for p in parts:
        if p == ">":
            comb = ">"
            continue
        m = _COMPOUND_RE.match(p)
        if not m:
            return None  # pseudo-classes, sibling combinators, ...
        quals = [(q[0] or "[", q[1] or q[2], q[3].strip("\"'") if q[3] else None) for q in _QUAL_RE.findall(m.group(2))]
        seq.append((comb, m.group(1), quals))
        comb = " "
    return seq or None


def _specificity(seq) -> tuple[int, int, int]:
    ids = sum(1 for _, _, qs in seq for q in qs if q[0] == "#")
    classes = sum(1 for _, _, qs in seq for q in qs if q[0] != "#")
    tags = sum(1 for _, tag, _ in seq if tag and tag != "*")
    return ids, classes, tags


def _stylesheet(text: str) -> list[tuple[list, tuple, int, list]]:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S).replace("<![CDATA[", "").replace("]]>", "")
    rules, i, order = [], 0, 0
    while True:
        j = text.find("{", i)
        if j < 0:
            break
        k, depth = j + 1, 1
        while k < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[k], 0)
            k += 1
        prelude, body = text[i:j].strip(), text[j + 1 : k - 1]
        i = k
        while prelude.startswith("@") and ";" in prelude:  # @import ...; @charset ...;
            prelude = prelude.split(";", 1)[1].strip()
        if not prelude or prelude.startswith("@"):
            continue  # @media, @font-face, ...: not applied
        decls = _declarations(body)
        for sel in prelude.split(","):
            seq = _selector(sel)
            if seq:
                rules.append((seq, _specificity(seq), order, decls))
        order += 1
    return rules


def _compound_matches(el: ET.Element, tag: str | None, quals) -> bool:
    if tag and tag != "*" and _local(el.tag) != tag:
        return False
    for kind, name, value in quals:
        if kind == "." and name not in (el.get("class") or "").split():
            return False
        if kind == "#" and el.get("id") != name:
            return False
        if kind == "[" and (el.get(name) is None or (value is not None and el.get(name) != value)):
            return False
    return True


def _matches(seq, i: int, el: ET.Element, parent: dict) -> bool:
    if not _compound_matches(el, seq[i][1], seq[i][2]):
        return False
    if i == 0:
        return True
    p = parent.get(el)
    if seq[i][0] == ">":
        return p is not None and _matches(seq, i - 1, p, parent)
    while p is not None:
        if _matches(seq, i - 1, p, parent):
            return True
        p = parent.get(p)
    return False


def _apply_css(root: ET.Element) -> None:
    parent = {c: p for p in root.iter() for c in p}
    sheets = []
    for st in list(root.iter(_S + "style")):
        if (st.get("type") or "text/css").lower() == "text/css":
            sheets.append("".join(st.itertext()))
        parent[st].remove(st)
    rules = _stylesheet("\n".join(sheets))
    plan = []
    for el in root.iter():
        decls = []
        for seq, spec, order, ds in rules:
            if _matches(seq, len(seq) - 1, el, parent):
                decls += [((imp, 0, spec, order), prop, val) for prop, val, imp in ds]
        inline = el.get("style")
        if inline:
            decls += [((imp, 1, (0, 0, 0), n), prop, val) for n, (prop, val, imp) in enumerate(_declarations(inline))]
        plan.append((el, decls))
    # apply after matching everything: attribute and class selectors look at the original attributes
    for el, decls in plan:
        el.attrib.pop("style", None)
        el.attrib.pop("class", None)
        rest = {}
        for _, prop, val in sorted(decls, key=lambda d: d[0]):
            if prop in PRESENTATION:
                el.set(prop, val)
            else:
                rest[prop] = val
        if rest:
            el.set("style", "; ".join(f"{k}: {v}" for k, v in rest.items()))


# ---------------------------------------------------------------------- <use>


def _num(v: str | None) -> float:
    n = _length(v)
    return n if n is not None else 0.0


def _fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _instance(use: ET.Element, ids: dict) -> ET.Element | None:
    href = use.get("href") or use.get(_XLINK_HREF) or ""
    target = ids.get(href[1:]) if href.startswith("#") else None
    if target is None or any(e is use for e in target.iter()):
        return None  # missing, external or self-referencing: renders nothing
    g = ET.Element(_S + "g")
    for k, v in use.attrib.items():
        if k not in ("x", "y", "width", "height") + _HREFS:
            g.set(k, v)
    x, y = _num(use.get("x")), _num(use.get("y"))
    tr = (use.get("transform") or "").strip()
    if x or y:
        tr = (tr + " " if tr else "") + f"translate({_fmt(x)} {_fmt(y)})"
    if tr:
        g.set("transform", tr)
    inst = copy.deepcopy(target)
    for e in inst.iter():
        e.attrib.pop("id", None)
    inst.tail = None
    kind = _local(inst.tag)
    if kind == "symbol":
        inst.tag = _S + "svg"
        for k in ("x", "y", "width", "height"):
            inst.attrib.pop(k, None)
        inst.set("width", use.get("width") or "100%")
        inst.set("height", use.get("height") or "100%")
    elif kind == "svg":
        for k in ("width", "height"):
            if use.get(k):
                inst.set(k, use.get(k))
    g.append(inst)
    return g


def _expand_uses(root: ET.Element) -> None:
    ids = {e.get("id"): e for e in root.iter() if e.get("id")}
    for _ in range(12):  # nested references; reference cycles stop here
        parent = {c: p for p in root.iter() for c in p}
        uses = [u for u in root.iter(_S + "use") if u in parent]
        if not uses:
            return
        for u in uses:
            p = parent[u]
            idx = list(p).index(u)
            inst = _instance(u, ids)
            p.remove(u)
            if inst is not None:
                inst.tail = u.tail
                p.insert(idx, inst)
        if sum(1 for _ in root.iter()) > 200_000:
            break
    parent = {c: p for p in root.iter() for c in p}
    for u in list(root.iter(_S + "use")):
        parent[u].remove(u)


def _references(root: ET.Element) -> set[str]:
    refs = set()
    for e in root.iter():
        for k, v in e.attrib.items():
            if k in _HREFS and v.startswith("#"):
                refs.add(v[1:])
            elif "url(" in v:
                refs.update(m.group(2) for m in _URL_RE.finditer(v))
    return refs


def _prune_defs(root: ET.Element) -> None:
    """Remove definitions nothing refers to (e.g. glyphs whose <use> copies replaced them)."""
    while True:
        refs = _references(root)
        removed = False
        for p in list(root.iter()):
            for c in list(p):
                if (_local(p.tag) == "defs" or _local(c.tag) == "symbol") and c.get("id") not in refs:
                    p.remove(c)
                    removed = True
        if not removed:
            break
    for p in list(root.iter()):
        for c in list(p):
            if _local(c.tag) == "defs" and len(c) == 0:
                p.remove(c)


def _prefix_ids(root: ET.Element, prefix: str) -> None:
    ids = {e.get("id") for e in root.iter() if e.get("id")}
    if not ids:
        return

    def url(m):
        return f"url(#{prefix}{m.group(2)})" if m.group(2) in ids else m.group(0)

    for e in root.iter():
        for k, v in list(e.attrib.items()):
            if k == "id":
                e.set(k, prefix + v)
            elif k in _HREFS and v.startswith("#") and v[1:] in ids:
                e.set(k, "#" + prefix + v[1:])
            elif "url(" in v:
                e.set(k, _URL_RE.sub(url, v))


# ---------------------------------------------------------------------- output


def _esc(s: str, attr: bool = False) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace('"', "&quot;") if attr else s


def _serialize(root: ET.Element) -> str:
    out: list[str] = []
    xlink = any(k.startswith("{" + XLINK_NS) for e in root.iter() for k in e.attrib)
    head = [("xmlns", SVG_NS)] + ([("xmlns:xlink", XLINK_NS)] if xlink else [])
    _write(root, out, False, head)
    return "".join(out)


def _write(el: ET.Element, out: list[str], keep_space: bool, extra=()) -> None:
    name = _local(el.tag)
    keep_space = keep_space or name in ("text", "tspan", "textPath")
    attrs = list(extra)
    for k, v in el.attrib.items():
        if k.startswith("{"):
            ns, local = k[1:].split("}", 1)
            k = ("xlink:" if ns == XLINK_NS else "xml:") + local
        if k in ("d", "points"):
            v = " ".join(v.split())
        attrs.append((k, v))
    out.append("<" + name + "".join(f' {k}="{_esc(v, True)}"' for k, v in attrs))
    text = el.text or ""
    if not keep_space and not text.strip():
        text = ""
    if not len(el) and not text:
        out.append("/>")
        return
    out.append(">" + _esc(text))
    for c in el:
        _write(c, out, keep_space)
        tail = c.tail or ""
        if keep_space or tail.strip():
            out.append(_esc(tail))
    out.append(f"</{name}>")


# ---------------------------------------------------------------------- Rnote's wrapper


def rnote_image_svg(svg_data: str, x: float, y: float, w: float, h: float, intrinsic: tuple[float, float]) -> str:
    """The SVG that VectorImage::gen_svg makes from a vector image placed at x, y with size w x h."""
    return (f'<g transform="matrix(1 0 0 1 {x + w / 2:.3f} {y + h / 2:.3f})"><svg x="{-w / 2:.3f}" y="{-h / 2:.3f}" '
            f'width="{w:.3f}" height="{h:.3f}" viewBox="0.000 0.000 {intrinsic[0]:.3f} {intrinsic[1]:.3f}" '
            f'preserveAspectRatio="none">{svg_data}</svg></g>')


def rnote_render_document(svg_data: str, x: float, y: float, w: float, h: float, intrinsic: tuple[float, float]) -> str:
    """The document Svg::gen_image hands to librsvg: rnote_image_svg inside wrap_svg_root (rnote-compose)."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
            f'viewBox="{x:.3f} {y:.3f} {w:.3f} {h:.3f}" preserveAspectRatio="none">'
            + rnote_image_svg(svg_data, x, y, w, h, intrinsic) + "</svg>")


def rnote_parse_error(svg_data: str, x: float, y: float, w: float, h: float, intrinsic: tuple[float, float]) -> str | None:
    """Why librsvg would reject this image inside Rnote's wrapper (an XML error), or None."""
    try:
        ET.fromstring(rnote_render_document(svg_data, x, y, w, h, intrinsic))  # namespace-aware, like librsvg
    except ET.ParseError as e:
        return str(e)
    return None
