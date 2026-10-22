# rnotegen

Create [Rnote](https://rnote.flxzt.net) notes (`.rnote` files) from a small, line-based
script language that LLMs find easy to write, or straight from **Mermaid** diagrams
and **Markdown**.

Everything rnotegen produces is native Rnote content: text, shapes, arrows and pen
strokes that you can select, move and edit in Rnote like anything you drew yourself.
It does not paste pictures.

```
h1 "Cellular respiration" rule
para "Cells turn **glucose** into *ATP* in three stages."
bullet "Glycolysis"
bullet "Krebs cycle"
mermaid
flowchart LR
  G[Glucose] --> P(Pyruvate) --> A((ATP))
end
box "Mitochondrion" at=60,600 size=200x80 shape=round id=m
box "Cytoplasm" right_of=m gap=120 shape=ellipse id=c
connect c -> m "pyruvate"
```

## Why a separate language?

LLMs are bad at computing coordinates and good at writing simple, regular text. The
script is designed for that:

- **One command per line**, with self-describing `key=value` options. Unknown
  options produce warnings with "did you mean" hints instead of failing.
- **No coordinates needed for documents.** Flow placement stacks headings,
  paragraphs, lists, tables and diagrams down the page and breaks pages
  automatically.
- **Exact positions when you want them.** Use `at=x,y`, `center=`, `anchor=`, units
  (`mm`, `cm`, `in`, `pt`, `%`), page-relative coordinates (`page 2`), named
  elements with anchors and offsets (`title.bottom+0,20`), and relative placement
  (`below=a`, `right_of=b`). Arithmetic, variables and loops handle repeated
  layouts: `let w = 120`, `repeat 7 i ... at=60 + i*w, 100 ... end`.
- **Feedback for the model.** Errors and warnings carry line numbers: overlapping
  text and shapes, content off the page, large page gaps, tiny diagrams. Each
  build lists element ids with their positions, and an optional preview image
  shows the result.

Run `rnotegen spec` for the full language reference. It is written for an LLM to
read in one go, and is also available from the MCP server.

## Install

```sh
pip install -e .            # or: pipx install .
pip install Pillow          # optional: exact font metrics + embedding PNG/JPEG as Rnote bitmaps
```

rnotegen has no required dependencies (Python 3.10+). PNG previews need
`rsvg-convert` (package `librsvg2-bin` on Debian/Ubuntu, `librsvg` on Fedora/Homebrew)
or `pip install cairosvg`. SVG previews always work.

## Usage

```sh
rnotegen build notes.rns -o notes.rnote -p preview.png   # script
rnotegen build lecture.md                                 # Markdown -> lecture.rnote
rnotegen build diagram.mmd --theme forest --rough         # Mermaid -> diagram.rnote
rnotegen build more.rns --into existing.rnote -o out.rnote # append to an existing note
rnotegen check notes.rns                                  # validate only
rnotegen preview notes.rns -o preview.png                 # just render
rnotegen decompile existing.rnote -o existing.rns         # .rnote -> editable script
rnotegen info existing.rnote                              # page, stroke counts, text
rnotegen spec                                             # language reference
rnotegen mcp                                              # MCP server on stdio
```

The input kind comes from the extension (`.rns` script, `.md` Markdown, `.mmd` Mermaid)
or `--format`. Use `-` to read stdin. `--json` prints a machine-readable result.

### Use with an LLM

**MCP** (Claude Code, Claude Desktop, and other MCP clients):

```sh
claude mcp add rnotegen -- rnotegen mcp
```

or in a client config:

```json
{ "mcpServers": { "rnotegen": { "command": "rnotegen", "args": ["mcp"] } } }
```

Tools: `rnote_reference`, `rnote_build`, `rnote_check`, `rnote_from_mermaid`,
`rnote_from_markdown`, `rnote_read`. Build and check results include a PNG preview, so
a model with vision can look at what it made and fix it.

**Shell-based agents:** tell the model "Run `rnotegen spec`, then write a script and
build it with `rnotegen build file.rns -o file.rnote -p preview.png`." For Claude
Code there is a ready-made skill: copy `integrations/claude-skill/` to
`~/.claude/skills/rnote/`.

## Mermaid support

Diagrams are laid out by rnotegen itself, so no Node.js or browser is needed. The
layered layout handles subgraphs, edge labels and cycles.

| Diagram | Supported |
|---|---|
| `flowchart` / `graph` | all node shapes (incl. `@{ shape: ... }`), subgraphs with their own `direction`, `-->` `---` `-.->` `==>` `--o` `--x` `<-->` `~~~`, long links, labels, chaining with `&`, `classDef` / `class` / `style` / `linkStyle` |
| `sequenceDiagram` | participants and actors, all arrow types, activations, notes, `loop` / `alt` / `else` / `opt` / `par` / `critical` / `break` / `rect`, `box`, `autonumber` |
| `stateDiagram-v2` | `[*]`, composite states, fork/join/choice, notes, descriptions |
| `classDiagram` | members, visibility, static/abstract, generics, annotations, all relations, cardinalities, namespaces, notes |
| `erDiagram` | entities with attributes (PK/FK/UK, comments), crow's foot cardinalities |
| `mindmap` | node shapes, balanced left/right layout |
| `pie`, `timeline`, `gantt` | yes (gantt: dates, durations, `after`, milestones, crit/active/done) |

Themes: `default`, `neutral`, `forest`, `blue`, `ink`, `sketch` (hand-drawn), and
`dark`, which is chosen automatically on dark pages. Front matter titles and
`%%{init: {"theme": ...}}%%` are honored.

## Compatibility

rnotegen writes the file format of **Rnote 0.13 and 0.14** (the current releases).
Newer Rnote versions convert it automatically when the file is opened. Generated
files are tested by loading and rendering them with the real Rnote engine
(`rnote-cli` 0.14.2). `--into` appends to files saved by Rnote 0.9 to 0.14,
including handwritten notes.

Text is laid out by Rnote with your installed fonts. When Pillow and fontconfig are
available, rnotegen measures text with the same fonts, so centering and wrapping
are exact. Otherwise it uses slightly generous estimates.

## Examples

See [`examples/`](examples): a study note in flow mode, a hand-placed architecture
diagram, a two-page worksheet in millimeters, a Markdown lecture, and Mermaid
flowchart, sequence and mindmap files. `examples/out/` holds the generated
`.rnote` files and previews.

## Development

```sh
pip install pytest
python -m pytest tests                # RNOTE_CLI=/path/to/rnote-cli also validates files with Rnote
```
