"""Command line interface: `rnotegen <command> ...` (run `rnotegen --help`)."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .api import KINDS, build, detect_kind, read_reference, write_preview


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as f:
        return f.read()


def _default_output(path: str) -> str:
    if path == "-":
        return "output.rnote"
    return os.path.splitext(path)[0] + ".rnote"


def _build_opts(a) -> dict:
    return {"theme": a.theme, "rough": a.rough, "curved": getattr(a, "curved", False), "direction": getattr(a, "direction", None),
            "page_size": a.page, "layout": a.layout, "landscape": a.landscape, "seed": a.seed}


def cmd_build(a, check_only: bool = False) -> int:
    text = _read_input(a.input)
    kind = a.format or detect_kind(None if a.input == "-" else a.input, text)
    base_dir = os.path.dirname(os.path.abspath(a.input)) if a.input != "-" else os.getcwd()
    out = None if check_only else (a.output or (a.into if a.into else _default_output(a.input)))
    res = build(text, kind, out, getattr(a, "preview", None), getattr(a, "into", None), base_dir, **_build_opts(a))
    if getattr(a, "strict", False) and res.ok and res.warnings:
        res.ok = False
        res.errors = ["--strict: warnings are treated as errors"]
    if a.json:
        print(res.to_json())
    else:
        print(res.report())
    return 0 if res.ok else 1


def cmd_preview(a) -> int:
    if a.input.lower().endswith(".rnote"):
        from .builder import Builder
        from .decompile import decompile

        script, _ = decompile(a.input, pen="full")
        doc = Builder().run_script(script)
        out = write_preview(doc, a.output, a.scale)
        print(f"preview: {out}")
        if a.exact:
            from .preview import rnote_cli_png

            if rnote_cli_png(a.input, a.output):
                print("rendered with rnote-cli (exact)")
        return 0
    text = _read_input(a.input)
    kind = a.format or detect_kind(a.input, text)
    base_dir = os.path.dirname(os.path.abspath(a.input)) if a.input != "-" else os.getcwd()
    res = build(text, kind, None, None, None, base_dir, **_build_opts(a))
    if not res.ok:
        print(res.report())
        return 1
    write_preview(res.document, a.output, a.scale)
    print(f"preview: {a.output}")
    if res.warnings:
        print(f"warnings ({len(res.warnings)}):")
        for w in res.warnings:
            print("  " + w)
    return 0


def cmd_decompile(a) -> int:
    from .decompile import decompile

    image_dir = os.path.dirname(os.path.abspath(a.output)) if a.output else None
    script, summary = decompile(a.input, pen=a.pen, images=a.images, image_dir=image_dir)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(script)
        print(f"wrote {a.output} ({sum(summary['strokes'].values())} strokes)")
    else:
        sys.stdout.write(script)
    return 0


def cmd_info(a) -> int:
    from .decompile import decompile

    _, summary = decompile(a.input, pen="skip", images="skip")
    if a.json:
        print(json.dumps(summary, indent=2))
        return 0
    p = summary["page"]
    print(f"file format: {summary['version']}")
    print(f"page: {p['width']:.0f} x {p['height']:.0f} px, layout {p['layout']}")
    print("strokes: " + ", ".join(f"{v} {k}" for k, v in summary["strokes"].items()))
    if summary["text"]:
        print("text:")
        for t in summary["text"]:
            print("  - " + t.replace("\n", " / ")[:200])
    return 0


def cmd_spec(a) -> int:
    sys.stdout.write(read_reference())
    return 0


def cmd_mcp(a) -> int:
    from .mcp_server import serve

    serve()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rnotegen", description="Create Rnote (.rnote) documents from a simple script, Markdown or Mermaid.")
    ap.add_argument("--version", action="version", version=f"rnotegen {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, with_output=True):
        p.add_argument("input", help="input file (.rns script, .md Markdown, .mmd Mermaid) or - for stdin")
        if with_output:
            p.add_argument("-o", "--output", help="output .rnote path (default: input name with .rnote)")
        p.add_argument("-f", "--format", choices=KINDS, help="input kind (default: from the file extension)")
        p.add_argument("--theme", help="diagram theme: default, neutral, forest, blue, ink, sketch")
        p.add_argument("--rough", action="store_true", help="hand-drawn (rough) style")
        p.add_argument("--curved", action="store_true", help="curved diagram edges")
        p.add_argument("--direction", help="override flowchart direction (TB, LR, ...)")
        p.add_argument("--page", help="page size for Markdown/Mermaid input: a4, a5, letter, ... or WxH")
        p.add_argument("--layout", help="pages | continuous | infinite")
        p.add_argument("--landscape", action="store_true")
        p.add_argument("--seed", type=int, default=0, help="seed for the rough style")
        p.add_argument("--json", action="store_true", help="machine-readable result")

    p = sub.add_parser("build", help="build a .rnote file")
    common(p)
    p.add_argument("-p", "--preview", help="also write a preview image (.png or .svg)")
    p.add_argument("--into", help="append to an existing .rnote file instead of creating a new one")
    p.add_argument("--strict", action="store_true", help="fail on warnings")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("check", help="validate a script without writing anything")
    common(p, with_output=False)
    p.set_defaults(func=lambda a: cmd_build(a, check_only=True), output=None, into=None, preview=None)

    p = sub.add_parser("preview", help="render a preview image (.png/.svg) of a script, Markdown, Mermaid or .rnote file")
    common(p, with_output=False)
    p.add_argument("-o", "--output", required=True, help="image path (.png or .svg)")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--exact", action="store_true", help="for .rnote input: render with rnote-cli if installed")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("decompile", help="convert a .rnote file into an editable script")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("--pen", choices=("simplify", "full", "skip"), default="simplify", help="how to handle freehand strokes")
    p.add_argument("--images", choices=("inline", "skip"), default="inline")
    p.set_defaults(func=cmd_decompile)

    p = sub.add_parser("info", help="summarize a .rnote file (page, stroke counts, text)")
    p.add_argument("input")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("spec", help="print the script language reference (written for LLMs)")
    p.set_defaults(func=cmd_spec)

    p = sub.add_parser("mcp", help="run as an MCP server over stdio")
    p.set_defaults(func=cmd_mcp)

    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except FileNotFoundError as e:
        print(f"error: {e.strerror}: {e.filename}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
