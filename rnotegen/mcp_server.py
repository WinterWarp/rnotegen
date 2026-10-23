"""Minimal MCP (Model Context Protocol) server over stdio, no dependencies.

Exposes tools to build .rnote files from scripts, Markdown and Mermaid, to
validate scripts with a rendered preview, and to read existing .rnote files.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import traceback

from . import __version__
from .api import build, read_reference, write_preview

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

CHEATSHEET = """Script cheat sheet (call rnote_reference for everything):
document size=a4 layout=pages pattern=grid
h1 "Title"            para "Text with **bold** and *italic*"      bullet "item" level=2
box "Label" at=100,200 size=160x60 shape=round fill=lightyellow id=a
box "Next" below=a gap=60 id=b        connect a -> b "label"
rect 100,100 300,200 fill=#eef      circle center=400,300 radius=40     arrow 100,400 300,400
text "Note" at=b.right+20,0        (coordinates in px, origin top-left of page 1; A4 = 794 x 1123)
let w = 120                        repeat 5 i ... end   (use i, w in numbers: at=60 + i*w, 300; "Item {i+1}")
math "\\frac{a}{b} + x^2" size=20      (TeX formula; needs matplotlib)
mermaid                            markdown
flowchart TD                       # Heading
  A --> B                          - list item
end                                end"""

TOOLS = [
    {
        "name": "rnote_reference",
        "description": "Returns the full reference of the rnotegen script language (commands, options, positioning, "
                       "Mermaid/Markdown support, examples). Read it before writing scripts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rnote_build",
        "description": "Create an Rnote .rnote file from an rnotegen script (or Markdown/Mermaid with format=...). "
                       "Returns a report (errors with line numbers, warnings such as overlapping text or content off the "
                       "page, element ids with their positions) and a preview image.\n\n" + CHEATSHEET,
        "inputSchema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "the script / Markdown / Mermaid source"},
                "output_path": {"type": "string", "description": "where to write the .rnote file"},
                "format": {"type": "string", "enum": ["script", "markdown", "mermaid"], "default": "script"},
                "into_path": {"type": "string", "description": "optional existing .rnote file to append to"},
                "preview": {"type": "boolean", "default": True, "description": "return a PNG preview"},
                "theme": {"type": "string", "description": "diagram theme: default, neutral, forest, blue, ink, sketch"},
            },
            "required": ["script", "output_path"],
        },
    },
    {
        "name": "rnote_check",
        "description": "Validate a script without writing a file. Returns errors/warnings and a preview image, so you "
                       "can fix positions before building.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script": {"type": "string"},
                "format": {"type": "string", "enum": ["script", "markdown", "mermaid"], "default": "script"},
            },
            "required": ["script"],
        },
    },
    {
        "name": "rnote_from_mermaid",
        "description": "Convert a Mermaid diagram (flowchart/graph, sequenceDiagram, stateDiagram-v2, classDiagram, "
                       "erDiagram, mindmap, pie, timeline, gantt) into a .rnote file with native, editable Rnote shapes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mermaid": {"type": "string"},
                "output_path": {"type": "string"},
                "theme": {"type": "string", "description": "default, neutral, forest, blue, ink, sketch"},
                "rough": {"type": "boolean", "description": "hand-drawn look"},
                "preview": {"type": "boolean", "default": True},
            },
            "required": ["mermaid", "output_path"],
        },
    },
    {
        "name": "rnote_from_markdown",
        "description": "Convert a Markdown note (headings, paragraphs with **bold**/*italic*/`code`, lists, task lists, "
                       "quotes, tables, code blocks, ```mermaid fences, images) into a paginated .rnote document.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "markdown": {"type": "string"},
                "output_path": {"type": "string"},
                "page_size": {"type": "string", "description": "a4 (default), a5, letter, ... or WxH in px"},
                "layout": {"type": "string", "enum": ["pages", "continuous", "infinite"]},
                "preview": {"type": "boolean", "default": True},
            },
            "required": ["markdown", "output_path"],
        },
    },
    {
        "name": "rnote_read",
        "description": "Read an existing .rnote file: returns it as an editable rnotegen script (shapes, text, "
                       "simplified pen strokes) plus a preview image. Edit the script and pass it to rnote_build to "
                       "save changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "pen": {"type": "string", "enum": ["simplify", "full", "skip"], "default": "simplify"},
                "preview": {"type": "boolean", "default": True},
            },
            "required": ["path"],
        },
    },
]


def _image(doc) -> dict | None:
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        write_preview(doc, tmp)
        with open(tmp, "rb") as f:
            return {"type": "image", "data": base64.b64encode(f.read()).decode("ascii"), "mimeType": "image/png"}
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _result(text: str, doc=None, preview: bool = True, is_error: bool = False) -> dict:
    content = [{"type": "text", "text": text}]
    if preview and doc is not None:
        img = _image(doc)
        if img:
            content.append(img)
        else:
            content.append({"type": "text", "text": "(preview image unavailable: install rsvg-convert or cairosvg)"})
    return {"content": content, "isError": is_error}


def call_tool(name: str, args: dict) -> dict:
    if name == "rnote_reference":
        return _result(read_reference())
    if name in ("rnote_build", "rnote_check", "rnote_from_mermaid", "rnote_from_markdown"):
        if name == "rnote_from_mermaid":
            src, kind = args.get("mermaid", ""), "mermaid"
        elif name == "rnote_from_markdown":
            src, kind = args.get("markdown", ""), "markdown"
        else:
            src, kind = args.get("script", ""), args.get("format") or "script"
        out = None if name == "rnote_check" else args.get("output_path")
        if name != "rnote_check" and not out:
            return _result("output_path is required", is_error=True)
        if out:
            out = os.path.abspath(os.path.expanduser(out))
        base = os.path.dirname(out) if out else os.getcwd()
        into = args.get("into_path")
        res = build(src, kind, out, None, os.path.abspath(os.path.expanduser(into)) if into else None, base,
                    theme=args.get("theme"), rough=bool(args.get("rough")), page_size=args.get("page_size"),
                    layout=args.get("layout"))
        return _result(res.report(), res.document if res.ok else None, args.get("preview", True), not res.ok)
    if name == "rnote_read":
        from .builder import Builder, BuildError
        from .decompile import decompile

        path = os.path.abspath(os.path.expanduser(args.get("path", "")))
        try:
            script, summary = decompile(path, pen=args.get("pen") or "simplify")
        except Exception as e:
            return _result(f"cannot read {path}: {e}", is_error=True)
        doc = None
        if args.get("preview", True):
            try:
                doc = Builder().run_script(script)
            except BuildError:
                doc = None
        counts = ", ".join(f"{v} {k}" for k, v in summary["strokes"].items()) or "empty"
        text = f"{path}: Rnote format {summary['version']}, {counts}\n\n{script}"
        return _result(text, doc, args.get("preview", True))
    return _result(f"unknown tool {name!r}", is_error=True)


def handle(msg: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if method is None:
        return None
    if mid is None:  # notification
        return None
    try:
        if method == "initialize":
            want = params.get("protocolVersion")
            ver = want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            result = {
                "protocolVersion": ver,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "rnotegen", "version": __version__},
                "instructions": "Create and read Rnote (.rnote) notes. Call rnote_reference once to learn the script "
                                "language, then use rnote_build (check the returned preview and warnings). For Mermaid "
                                "diagrams or Markdown notes use rnote_from_mermaid / rnote_from_markdown.",
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = call_tool(params.get("name", ""), params.get("arguments") or {})
        elif method in ("resources/list", "prompts/list"):
            result = {"resources": []} if method == "resources/list" else {"prompts": []}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue
        msgs = msg if isinstance(msg, list) else [msg]
        for m in msgs:
            resp = handle(m)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
