"""High-level entry points shared by the CLI and the MCP server."""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field

from .builder import Builder, BuildError
from .model import BBox, Document, PageSetup, union_all
from .rnoteformat import (
    RnoteFormatError, append_elements, build_file_json, encode, page_setup_from_snapshot, read_file_json,
    snapshot_for_append,
)

KINDS = ("script", "markdown", "mermaid")
_MERMAID_STARTS = ("flowchart", "graph", "sequencediagram", "statediagram", "classdiagram", "erdiagram", "mindmap",
                   "pie", "timeline", "gantt", "---", "%%")


def detect_kind(path: str | None, text: str) -> str:
    if path:
        ext = os.path.splitext(path)[1].lower()
        if ext in (".md", ".markdown"):
            return "markdown"
        if ext in (".mmd", ".mermaid"):
            return "mermaid"
        if ext in (".rns", ".rnotegen", ".rnscript"):
            return "script"
    first = next((l.strip() for l in text.splitlines() if l.strip()), "").lower()
    if first.split(" ")[0] in _MERMAID_STARTS or first.startswith("graph ") or first.startswith("flowchart "):
        return "mermaid"
    return "script"


@dataclass
class BuildResult:
    ok: bool
    output: str | None = None
    pages: int = 0
    strokes: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    preview: str | None = None
    notes: list[str] = field(default_factory=list)
    document: Document | None = None

    def report(self) -> str:
        out = []
        if self.ok:
            if self.output:
                out.append(f"OK: wrote {self.output} ({self.pages} page{'s' if self.pages != 1 else ''}, {self.strokes} strokes)")
            else:
                out.append(f"OK: script is valid ({self.pages} page{'s' if self.pages != 1 else ''}, {self.strokes} strokes)")
        else:
            out.append(f"FAILED: {len(self.errors)} error{'s' if len(self.errors) != 1 else ''}, nothing was written")
            for e in self.errors:
                out.append("  " + e.replace("\n", "\n  "))
        if self.preview:
            out.append(f"preview: {self.preview}")
        for n in self.notes:
            out.append(f"note: {n}")
        if self.ids:
            out.append("ids (x, y = top-left):")
            out += ["  " + s for s in self.ids]
        if self.warnings:
            out.append(f"warnings ({len(self.warnings)}):")
            out += ["  " + w for w in self.warnings[:30]]
            if len(self.warnings) > 30:
                out.append(f"  ... {len(self.warnings) - 30} more")
        return "\n".join(out)

    def to_json(self) -> str:
        return json.dumps({k: v for k, v in self.__dict__.items() if k != "document"}, indent=2)


def _page_overrides(page: PageSetup, opts: dict) -> None:
    from .model import LAYOUTS, PAGE_FORMATS_MM, mm_to_px

    size = opts.get("page_size")
    if size:
        key = size.lower()
        if key in PAGE_FORMATS_MM:
            w, h = PAGE_FORMATS_MM[key]
            page.width, page.height = mm_to_px(w), mm_to_px(h)
        elif "x" in key:
            a, b = key.split("x", 1)
            page.width, page.height = float(a), float(b)
    if opts.get("landscape") and page.width < page.height:
        page.width, page.height = page.height, page.width
    if opts.get("layout"):
        page.layout = LAYOUTS.get(opts["layout"].lower(), page.layout)


def build(source: str, kind: str = "script", output: str | None = None, preview: str | None = None,
          into: str | None = None, base_dir: str = ".", strict: bool = False, **opts) -> BuildResult:
    """Build a .rnote file from a script, Markdown or Mermaid source.

    opts: theme, rough, curved, direction (mermaid); page_size, layout, landscape (markdown / mermaid);
    seed (rough-style randomness).
    """
    existing = None
    page = PageSetup()
    if into:
        try:
            existing = read_file_json(into)
            snap = snapshot_for_append(existing)
        except (OSError, RnoteFormatError) as e:
            return BuildResult(False, errors=[f"cannot open {into}: {e}"])
        page = page_setup_from_snapshot(snap)
    _page_overrides(page, opts)
    b = Builder(base_dir=base_dir, page=page)
    if existing is not None:
        bottom = _content_bottom(existing["data"]["engine_snapshot"])
        if bottom is not None:
            b.start_y = bottom + 24
            b.ctx = b._top_ctx()
    try:
        if kind == "script":
            doc = b.run_script(source)
        elif kind == "markdown":
            if opts.get("theme"):
                b.d.theme = opts["theme"]
            if opts.get("rough"):
                b.d.rough = True
            doc = b.run_markdown(source)
        elif kind == "mermaid":
            mopts = {k: opts.get(k) for k in ("theme", "rough", "curved", "direction", "scale", "font", "font_size")}
            doc = b.run_mermaid(source, **mopts)
        else:
            return BuildResult(False, errors=[f"unknown input kind {kind!r}; use one of {KINDS}"])
    except BuildError as e:
        return BuildResult(False, errors=[str(x) for x in e.errors], warnings=list(e.warnings))
    res = BuildResult(True, warnings=list(b.warnings), ids=b.id_report(), document=doc)
    res.strokes = len(doc.elements)
    res.pages = doc.page_count()
    if strict and res.warnings:
        res.ok = False
        res.errors = ["strict mode: warnings are treated as errors"]
        return res
    seed = int(opts.get("seed") or 0)
    if output:
        if existing is not None:
            snap = existing["data"]["engine_snapshot"]
            append_elements(snap, doc.elements, random.Random(seed))
            # make sure fixed-size documents grow to include the new content
            d = snap["document"]
            pages_needed = doc.page_count()
            if page.layout == "fixed_size":
                d["height"] = max(float(d.get("height", 0)), pages_needed * page.height)
            data = encode(existing)
            res.notes.append(f"appended {len(doc.elements)} strokes to the existing content of {into}")
        else:
            data = encode(build_file_json(doc, seed))
        out_dir = os.path.dirname(os.path.abspath(output))
        os.makedirs(out_dir, exist_ok=True)
        with open(output, "wb") as f:
            f.write(data)
        res.output = output
    if preview:
        try:
            pdoc = doc
            if existing is not None:
                pdoc = _with_existing(into, doc)
            res.preview = write_preview(pdoc, preview)
        except Exception as e:  # preview problems must not fail the build
            res.notes.append(f"preview not written: {e}")
    return res


def write_preview(doc: Document, path: str, scale: float = 1.0) -> str:
    from .preview import document_svg, svg_to_png

    svg = document_svg(doc)
    if path.lower().endswith(".svg"):
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
    else:
        svg_to_png(svg, path, scale)
    return path


def _with_existing(path: str, new: Document) -> Document:
    from .decompile import decompile

    script, _ = decompile(path, pen="simplify", images="inline")
    b = Builder()
    try:
        base = b.run_script(script)
    except BuildError:
        return new
    base.elements += new.elements
    base.page = new.page
    return base


def _content_bottom(snap: dict) -> float | None:
    """Rough lowest y of the existing strokes (to continue flow content below them)."""
    from .rnoteformat import iter_strokes

    ys: list[float] = []
    for _, val, _ in iter_strokes(snap):
        kind = next(iter(val))
        s = val[kind]
        if kind == "textstroke":
            a = s.get("transform", {}).get("affine") or s.get("affine") or [0] * 9
            ty = a[7] if len(a) == 9 else a[5]
            size = s.get("text_style", {}).get("font_size", 32)
            lines = max(1, s.get("text", "").count("\n") + 1 + len(s.get("text", "")) // 60)
            ys.append(ty + lines * size * 1.3)
        elif kind == "shapestroke":
            g = next(iter(s["shape"].values()))
            for key in ("start", "end", "tip", "cp", "cp1", "cp2"):
                if key in g:
                    ys.append(g[key][1])
            for p in g.get("path", []):
                ys.append(p[1])
            if "cuboid" in g or "radii" in g:
                a = g.get("transform", {}).get("affine") or g.get("affine") or [0] * 9
                ty = a[7] if len(a) == 9 else a[5]
                half = g["cuboid"]["half_extents"][1] if "cuboid" in g else g["radii"][1]
                ys.append(ty + half)
        elif kind == "brushstroke":
            p = s.get("path", {})
            if "start" in p:
                ys.append(p["start"]["pos"][1])
            for seg in p.get("segments", []):
                ys.append(next(iter(seg.values()))["end"]["pos"][1])
        elif kind in ("vectorimage", "bitmapimage"):
            r = s.get("rectangle", {})
            a = r.get("transform", {}).get("affine") or r.get("affine") or [0] * 9
            ty = a[7] if len(a) == 9 else a[5]
            ys.append(ty + r.get("cuboid", {}).get("half_extents", [0, 0])[1])
    return max(ys) if ys else None


def read_reference() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "reference.md"), encoding="utf-8") as f:
        return f.read()


__all__ = ["build", "detect_kind", "BuildResult", "write_preview", "read_reference", "BBox", "union_all"]
