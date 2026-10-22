# rnotegen script reference

rnotegen turns a small line-based script into an Rnote `.rnote` file. Every
stroke it makes is a native, editable Rnote object: text, shapes, arrows and
pen strokes.

Workflow: write a script, then run `rnotegen build notes.rns -o notes.rnote -p preview.png`
(or the `rnote_build` MCP tool). Read the report: errors carry line numbers,
warnings flag text overlaps and content off the page, and every `id=` is
listed with its position. Look at the preview, fix, and rebuild.

## 1. Syntax basics

```
# comment (also: // comment)
COMMAND [values...] [key=value ...] [flags ...]
```

- One command per line. A line ending in `\` continues on the next line.
- Strings use double quotes: `"Hello"`. Escapes: `\n` (new line), `\"`.
- Points are `x,y`, e.g. `120,80` or `(120, 80)`. Sizes are `WxH`, e.g. `200x80`.
- Units: pixels by default. Also `mm`, `cm`, `in`, `pt`, and `%` of the page
  width (x) or height (y): `at=20mm,30mm`, `at=50%,10%`.
- Colors: CSS names (`red`, `steelblue`), `#rgb`, `#rrggbb`, `#rrggbbaa`,
  `rgb(255,0,0)`, `name/alpha` (`red/0.3`), and `none`.
- Flags are bare words: `bold`, `dashed`, `rough`. `bold=on` and `bold=off` also work.
- Unknown options are reported as warnings with a "did you mean" hint.

**Coordinates:** origin (0,0) is the top-left corner of page 1. x grows right, y grows down.
An A4 page is 794 x 1123 px; US letter is 816 x 1056. The default margin is 56 px.

**Block commands** take the following lines as their body, up to a line that
contains only `end`. These are `mermaid`, `markdown`, `table`, `code`, `group`,
`svg`, and `text`/`para`/`quote`/`callout` when no "string" is given on the
command line. The body may also be wrapped in a ``` fence.

## 2. Placement: flow or exact

**Flow (default):** an element without a position goes below the previous
one, at the left margin, wrapping text to the page width. Pages break
automatically. This is the easiest way to write notes: just list headings,
paragraphs, lists and diagrams.

**Exact:** give any of the following.

| option | meaning |
|---|---|
| `at=X,Y` | top-left corner at X,Y (change which corner with `anchor=`) |
| `anchor=NAME` | which point of the element `at=` refers to: `topleft` `top` `topright` `left` `center` `right` `bottomleft` `bottom` `bottomright` |
| `center=X,Y` | center of the element at X,Y |
| `x=X` / `y=Y` | set one coordinate; the other comes from the flow |
| `below=ID` `above=ID` `right_of=ID` `left_of=ID` | next to another element, `gap=20` apart by default |
| `align=left\|center\|right` | with `below`/`above`: which edges to line up (default center) |
| `align=top\|middle\|bottom` | with `right_of`/`left_of` (default middle) |
| `float` | don't push the flow cursor (use for backgrounds and page frames) |

**References:** give an element `id=name` (letters, digits, `_`), then use
`name` (its center) or `name.ANCHOR` anywhere a point is expected, optionally
with an offset: `title.bottomleft+0,20`, `box1.right+30,0`, `box1.top-0,10`.
Anchors: `topleft top topright left center right bottomleft bottom bottomright`.

Explicitly placed elements also push the flow cursor below them, so flow
content that follows does not overlap them (unless `float`).

**Pages:** `page 2` moves to the top of page 2; from then on, explicit
coordinates are relative to page 2's top-left corner. `newpage` starts a new
page in the flow. `space 30` adds vertical space.

## 3. Commands

### Setup

```
document size=a4 orientation=portrait layout=pages pattern=grid margin=56
defaults font=sans size=16 color=#1e1e1e stroke_width=2 rough=off theme=default
```

- `document`: `size=` a6 a5 a4 a3 a2 letter legal or `WxH`; `orientation=`
  portrait|landscape; `layout=` pages | continuous (default) | infinite;
  `background=COLOR`; `pattern=` none lines grid dots isometric_grid
  isometric_dots; `pattern_color=`; `pattern_size=32`; `margin=56`;
  `borders=on|off`; `pages=N` (minimum page count). It applies to the whole
  file, wherever it appears.
- `defaults` changes the defaults for the commands that follow: `font=`
  (sans, serif, mono or a family name), `size=` (font size, default 16),
  `color=` (text and lines), `text_color=`, `stroke=`, `fill=`,
  `stroke_width=`, `dash=`, `rough=on|off`, `fill_style=`, `gap=` (flow
  spacing, default 12), `theme=` (diagram theme), `heading_color=`,
  `box_fill=`, `box_stroke=`, `link_color=`.

### Text

```
h1 "Title"                     # also h2, h3, h4, or: heading "Title" level=2
h1 "Chapter 1" rule            # 'rule' adds a line under the heading
para "A paragraph. Wraps to the page width automatically."
text "Exact label" at=100,200 size=24 color=navy bold
text "Centered title" center=397,60 size=28
text "Wrapped" at=60,300 width=250 align=center
bullet "First point"
bullet "Sub point" level=2
bullet "Step one" marker="1."
todo "Buy milk"
todo "Done thing" done
quote "A quotation, shown indented with a bar."
callout "Remember to cite sources." title="Note" fill=#fff4c2
hr
text at=60,500 width=300
A block of text
spanning several lines.
end
```

Text options: `size=` `font=` `color=` `bold` `italic` `underline` `strike`
`weight=` (100-900) `align=left|center|right|justify` `width=` (wrap width)
`rotate=DEG` `raw` (turns off inline markup) `opacity=`.

**Inline markup** inside any text: `**bold**`, `*italic*`, `_italic_`,
`~~strike~~`, `` `code` ``, `<u>underline</u>`, `<color=red>red text</color>`,
`[link text](https://...)`. Use `\*` for a literal asterisk.

### Shapes

```
rect 100,100 200x80                       # top-left + size
rect 100,100 300,180                      # two corners
rect at=100,100 size=200x80 fill=lightyellow stroke=orange radius=10 label="Inside"
ellipse center=300,300 radius=80,40
circle center=300,300 radius=40 fill=gold label="A"
circle 300,300 40                         # center + radius
line 100,100 300,100                      # 2 points = line
line 100,100 200,150 300,100 dashed       # 3+ points = polyline
arrow 100,200 300,200                     # arrow head at the end
arrow 100,200 200,250 300,200 both        # heads at both ends
line 100,250 300,250 head=triangle tail=circle
polygon 100,300 200,300 150,380 fill=pink
curve 100,400 200,320 300,400             # quadratic: start, control, end
bezier 100,450 150,380 250,520 300,450    # cubic: start, c1, c2, end
pen 100,500 120,510 140,505 160,520 smooth color=blue width=3   # freehand stroke
highlight 100,540 300,540                 # translucent highlighter (yellow by default)
```

Line-like commands (`line arrow curve bezier pen`) take `width=` as the
stroke width. Arrow heads (`head=` for the end, `tail=` for the start):
`arrow` (Rnote's native arrow), `triangle`, `triangle_open`, `diamond`,
`diamond_open`, `circle`, `circle_open`, `cross`, `bar`, `none`.
`arrow=start|end|both|none` also works on `line`.

### Style options (all shapes, boxes, connectors)

| option | values |
|---|---|
| `stroke=` (or `color=`) | line color, `none` for no outline |
| `fill=` | fill color (default none) |
| `stroke_width=` | line width in px (default 2) |
| `dash=` | `solid` `dashed` `dotted` `dashed_narrow` `dashed_wide` (flags `dashed`, `dotted` too) |
| `rough` | hand-drawn look (Rnote's rough style) |
| `fill_style=` | `hachure` `crosshatch` `zigzag` `zigzag_line` `dots` `dashed` (turns on rough) |
| `opacity=` | 0..1, applied to stroke and fill |
| `rotate=` | degrees (rect, ellipse, text) |
| `radius=` | rounded corners (rect) |

### Boxes and connectors (hand-made diagrams)

```
box "Start" at=100,100 id=start shape=stadium
box "Is it valid?" below=start gap=50 id=check shape=diamond fill=#fff3c4
box "Save" below=check gap=50 id=save
box "Show error" right_of=check gap=80 id=err fill=#ffd6d6
connect start -> check
connect check -> save "yes"
connect check -> err "no"
connect err -> start route=elbow dashed
```

- `box "Label"`: a shape with centered text that sizes itself to fit unless
  you give `size=WxH`, `width=` or `height=`. `shape=` rect (default) round stadium
  ellipse circle doublecircle diamond hexagon parallelogram parallelogram_alt
  trapezoid trapezoid_alt cylinder subroutine note document asymmetric
  text (no border). Options: `fill= stroke= color=` (text color) `font_size=`
  `bold` `text_align=left` `padding=` `wrap=` (max text width when auto-sizing, default 220).
- `connect A -> B "label"`: A and B are ids (the line stops at the shape's
  outline) or points/anchors (`a.bottom`, `300,200`). Operators: `->`
  `<-` `<->` `--` (no heads), `-->` and `..>` (dashed), `=>` (thick).
  Options: `route=straight|elbow|curve` (or flags `elbow`, `curved`),
  `from_side=` / `to_side=` (top bottom left right), `head=` `tail=`
  `label_bg=none`, plus the style options above.

### Groups

```
group at=400,600 id=legend
  rect 0,0 160x70 stroke=gray
  text "Legend" at=10,8 bold
  circle center=20,48 radius=6 fill=red
  text "error" at=34,38
end
```

Inside `group at=X,Y`, coordinates are relative to X,Y. Without `at=`, the
group is placed in the flow (use `align=center` to center it). The group's id
refers to its bounding box.

### Diagrams, Markdown, tables, code, images

````
mermaid width=500 theme=forest
flowchart LR
  A[Idea] --> B{Good?}
  B -->|yes| C[Do it]
  B -->|no| A
end

markdown
## Shopping
- [x] eggs
- [ ] flour
| Item | Qty |
|------|----:|
| eggs | 6   |
end

table
| Name | Role |
|------|------|
| Ada  | Math |
end

code lang=python
print("hello")
end

image "photo.png" width=300            # png, jpg, gif, webp, svg (path relative to the script)
svg at=500,100 width=120
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>
end
````

- `mermaid` supports: `flowchart`/`graph` (all node shapes, subgraphs,
  `classDef`/`class`/`style`/`linkStyle`, labeled, dotted, thick and long
  edges), `sequenceDiagram` (actors, notes, loops, alt/else, opt, par,
  activations, autonumber, boxes), `stateDiagram-v2` (composite states,
  `[*]`, fork/join/choice, notes), `classDiagram` (members, relations,
  cardinalities, annotations, namespaces), `erDiagram` (attributes, crow's
  foot cardinalities), `mindmap`, `pie`, `timeline`, `gantt`.
  Options: `width=` / `height=` / `scale=` / `max_width=`, `theme=`
  default|neutral|forest|blue|ink|sketch, `rough`, `curved`,
  `direction=LR` (override), `font=`, `font_size=`, `wrap=` (label width).
  In the flow, a diagram is centered and shrunk to fit the page width.
  Mermaid's own `subgraph ... end` lines are fine inside the block; the block ends at the
  first `end` that does not close a subgraph/loop/alt.
- `markdown` supports headings, paragraphs, `-`/`*`/`1.` lists (nested by
  indentation), `- [ ]` task lists, `>` quotes, tables, fenced code,
  ```` ```mermaid ```` fences (rendered as diagrams), `---` rules, images
  `![alt](file.png)` and `<!-- pagebreak -->`. Options: `at=`, `width=`.
- `table`: Markdown pipe-table rows; `header=off` makes the first row plain.
  Long tables continue on the next page with the header repeated.

## 4. Tips for reliable results

1. Prefer the flow for documents: headings, `para`, bullets, `mermaid` and
   `markdown` need no coordinates at all.
2. For drawings, give elements ids and place them relative to each other
   (`below=`, `right_of=`, `connect`) instead of computing coordinates.
3. Keep exact coordinates inside the page: x from 56 to 738 and y from 56 to
   1067 on A4 (inside the margins).
4. Text widths are estimates (fonts vary between systems). Leave some room,
   or set `width=` so that text wraps.
5. Read the warnings after each build. They tell you which line overlaps or
   overflows.
6. Images are always drawn below strokes and text (Rnote's layer order).

## 5. Complete example

```
document size=a4 layout=pages pattern=dots
defaults size=16

h1 "Photosynthesis" rule
para "Plants turn **light**, water and CO₂ into glucose and oxygen."
bullet "Light reactions: in the *thylakoid membranes*"
bullet "Calvin cycle: in the *stroma*"

mermaid
flowchart LR
  L[Light] --> T(Thylakoid)
  W[H2O] --> T
  T -->|ATP, NADPH| C(Calvin cycle)
  CO2 --> C
  C --> G[(Glucose)]
end

h2 "Sketch"
box "Chloroplast" id=chl shape=round fill=#d8f5d0 width=200
box "Leaf cell" right_of=chl gap=100 id=cell shape=ellipse
connect cell -> chl "contains" dashed
callout "Formula: 6 CO₂ + 6 H₂O → C₆H₁₂O₆ + 6 O₂" title="Remember"
```
