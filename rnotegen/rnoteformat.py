"""Reading and writing Rnote's `.rnote` save files.

A `.rnote` file is gzip-compressed JSON:

    {"version": "<semver>", "data": {"engine_snapshot": {...}}}

We write the format used by Rnote 0.13 / 0.14 (the current stable releases).
Newer Rnote versions convert this format on load.
"""

from __future__ import annotations

import gzip
import json
import random
from typing import Any

from .colors import Color
from .model import Document, Element, PageSetup

FORMAT_VERSION = "0.14.2"

_LAYER_JSON = {"user": {"user_layer": 0}, "highlighter": "highlighter", "image": "image", "document": "document"}


class RnoteFormatError(ValueError):
    pass


def document_json(page: PageSetup, n_pages: int) -> dict:
    w, h = page.width, page.height
    return {
        "config": {
            "format": {
                "width": round(w, 3),
                "height": round(h, 3),
                "dpi": 96.0,
                "orientation": "landscape" if w > h else "portrait",
                "border_color": page.border_color.to_json(),
                "show_borders": page.show_borders,
                "show_origin_indicator": page.show_origin,
            },
            "background": {
                "color": page.background.to_json(),
                "pattern": page.pattern,
                "pattern_size": [round(page.pattern_size, 3), round(page.pattern_size, 3)],
                "pattern_color": page.pattern_color.to_json(),
            },
            "layout": page.layout,
        },
        "x": 0.0,
        "y": 0.0,
        "width": round(w, 3),
        "height": round(h * max(1, n_pages), 3),
    }


def build_file_json(doc: Document, seed: int = 0) -> dict:
    rng = random.Random(seed)
    snapshot = {
        "document": document_json(doc.page, doc.page_count()),
        "camera": {"offset": [-40.0, -40.0], "size": [1200.0, 900.0], "zoom": 1.0},
        "stroke_components": [{"value": None, "version": 0}],
        "chrono_components": [{"value": None, "version": 0}],
        "chrono_counter": 0,
    }
    append_elements(snapshot, doc.elements, rng)
    return {"version": FORMAT_VERSION, "data": {"engine_snapshot": snapshot}}


def append_elements(snapshot: dict, elements: list[Element], rng: random.Random) -> None:
    """Append strokes to an engine snapshot (also used to add to an existing file)."""
    strokes = snapshot["stroke_components"]
    chrono = snapshot["chrono_components"]
    # the secondary map may be shorter than the slot map; pad with vacant slots
    while len(chrono) < len(strokes):
        chrono.append({"value": None, "version": 0})
    t = int(snapshot.get("chrono_counter", 0))
    for el in elements:
        t += 1
        strokes.append({"value": el.to_stroke(rng), "version": 1})
        chrono.append({"value": {"t": t, "layer": _LAYER_JSON[el.layer]}, "version": 1})
    snapshot["chrono_counter"] = t


def encode(file_json: dict) -> bytes:
    raw = json.dumps(file_json, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0)


def write_document(doc: Document, path: str, seed: int = 0) -> None:
    with open(path, "wb") as f:
        f.write(encode(build_file_json(doc, seed)))


def read_file_json(path: str) -> dict:
    with open(path, "rb") as f:
        data = f.read()
    try:
        raw = gzip.decompress(data)
    except OSError as e:
        raise RnoteFormatError(f"{path}: not a gzip-compressed .rnote file ({e})") from e
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RnoteFormatError(f"{path}: invalid JSON inside .rnote file ({e})") from e
    if not isinstance(obj, dict) or "version" not in obj or "data" not in obj:
        raise RnoteFormatError(f"{path}: missing 'version'/'data' wrapper")
    return obj


def parse_semver(v: str) -> tuple[int, int, int]:
    core = v.split("-")[0].split("+")[0]
    parts = (core.split(".") + ["0", "0", "0"])[:3]
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return (0, 0, 0)


def snapshot_for_append(file_json: dict) -> dict:
    """Return the engine snapshot of an existing file, upgraded to the 0.13/0.14 layout.

    Raises for formats we cannot safely append to.
    """
    ver = parse_semver(str(file_json["version"]))
    snap = file_json["data"].get("engine_snapshot")
    if not isinstance(snap, dict):
        raise RnoteFormatError("file has no engine snapshot")
    if ver >= (0, 15, 0):
        raise RnoteFormatError(
            f"file was saved by Rnote {file_json['version']} (development format); "
            "appending is supported for files saved by Rnote 0.9 - 0.14")
    if ver < (0, 9, 0):
        raise RnoteFormatError(f"file version {file_json['version']} is too old; open and re-save it in a newer Rnote first")
    if ver < (0, 13, 0):
        # 0.9 - 0.12: document.{format,background,layout} -> document.config.{...}
        d = snap["document"]
        cfg = {k: d.pop(k) for k in ("format", "background", "layout") if k in d}
        d.pop("snap_positions", None)
        d["config"] = cfg
        file_json["version"] = FORMAT_VERSION
    return snap


def page_setup_from_snapshot(snap: dict) -> PageSetup:
    d = snap.get("document", {})
    cfg = d.get("config", d)
    fmt = cfg.get("format", {})
    bg = cfg.get("background", {})
    ps = PageSetup()
    ps.width = float(fmt.get("width", ps.width))
    ps.height = float(fmt.get("height", ps.height))
    ps.layout = cfg.get("layout", ps.layout)
    ps.background = Color.from_json(bg.get("color")) or ps.background
    ps.pattern = bg.get("pattern", ps.pattern)
    ps.pattern_color = Color.from_json(bg.get("pattern_color")) or ps.pattern_color
    size = bg.get("pattern_size", [ps.pattern_size])
    ps.pattern_size = float(size[0] if isinstance(size, list) else size)
    ps.show_borders = bool(fmt.get("show_borders", True))
    return ps


def iter_strokes(snap: dict) -> list[tuple[int, dict, Any]]:
    """Returns [(t, stroke_value, layer)] for occupied slots, in draw order."""
    strokes = snap.get("stroke_components", [])
    chrono = snap.get("chrono_components", [])
    out = []
    for i, slot in enumerate(strokes):
        if slot.get("value") is None:
            continue
        c = chrono[i]["value"] if i < len(chrono) and chrono[i].get("value") else {"t": 0, "layer": {"user_layer": 0}}
        out.append((c.get("t", 0), slot["value"], c.get("layer")))
    order = {"document": 0, "image": 1, "highlighter": 2}

    def key(item):
        layer = item[2]
        lk = 3 + layer.get("user_layer", 0) if isinstance(layer, dict) else order.get(str(layer).lower(), 3)
        return (lk, item[0])

    out.sort(key=key)
    return out
