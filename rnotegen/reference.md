# rnotegen script reference

rnotegen turns a small line-based script into an Rnote `.rnote` file. Every
stroke it makes is a native, editable Rnote object: text, shapes, arrows and
pen strokes.

Workflow: write a script, then run `rnotegen build notes.rns -o notes.rnote -p preview.png`
(or the `rnote_build` MCP tool). Read the report: errors carry line numbers,
warnings flag overlapping text or shapes, content off the page and large
page gaps, and every `id=` is listed with its position. Look at the preview,
fix, and rebuild. The preview shows all pages stacked in one tall image,
with dashed lines at page boundaries. Gray areas are outside the page.

## 1. Syntax basics

```
# comment (also: // comment)
COMMAND [values...] [key=value ...] [flags ...]
```

- One command per line. A line ending in `\` continues on the next line.
- Strings use double quotes: `"Hello"`. Escapes: `\n` (new line), `\"`, `\\`.
- Points are `x,y`, e.g. `120,80` or `(120, 80)`. Sizes are `WxH`, e.g. `200x80`
  or `50mmx30mm`.
- Units: pixels by default. Also `mm`, `cm`, `in`, `pt`, and `%` of the page
  width (x) or height (y): `at=20mm,30mm`, `at=50%,10%`.
- Numbers can be arithmetic: `at=margin + 3*colw, 80`, `size=(colw - 8)x600`
  (see section 3, "Variables and loops").
- Colors: CSS names (`red`, `steelblue`), `#rgb`, `#rrggbb`, `#rrggbbaa`,
  `rgb(255,0,0)`, `name/alpha` (`red/0.3`), and `none`.
- Flags are bare words: `bold`, `dashed`, `rough`. `bold=on` and `bold=off` also work.
- Unknown options are reported as warnings with a "did you mean" hint.

**Coordinates:** origin (0,0) is the top-left corner of page 1. x grows right, y grows down.
An A4 page is 794 x 1123 px (landscape: 1123 x 794); US letter is 816 x 1056.
The default margin is 56 px. Build reports always give positions in px.

**Block commands** take the following lines as their body, up to a line that
contains only `end`. These are `mermaid`, `markdown`, `table`, `code`, `group`,
`svg`, and `text`/`para`/`quote`/`callout`/`math` when no "string" is given on the
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
| `x=X` / `y=Y` | set one coordinate; the other comes from the flow. With only `x=`, the flow also decides page breaks |
| `below=ID` `above=ID` `right_of=ID` `left_of=ID` | next to another element, `gap=20` apart by default |
| `align=left\|center\|right` | with `below`/`above`: which edges to line up (default center) |
| `align=top\|middle\|bottom\|baseline` | with `right_of`/`left_of` (default middle). `baseline` lines up the first text baselines of text, headings and math |
| `float` | don't push the flow cursor (use for backgrounds and page frames) |

Give one way of placing an element. If several are given, `at=` wins over
`center=`, which wins over `below=`/`above=`/`right_of=`/`left_of=`, which win
over `x=`/`y=`; the build warns that the others are ignored
(`x= is ignored because at= is set`).

**References:** give an element `id=name` (letters, digits, `_`), then use
`name` (its center) or `name.ANCHOR` anywhere a point is expected, optionally
with an offset `+dx,dy` or `-dx,dy` (each number has its own sign): `title.bottomleft+0,20`
(20 px below), `box1.right+30,0` (30 px to the right), `box1.top+0,-10` (10 px above),
`box1.bottom-70,40` (70 px left, 40 px down).
Anchors: `topleft top topright left center right bottomleft bottom bottomright`, and for
text, headings and math `baseline` (the left end of the first line's baseline;
also `baseline_center`, `baseline_right`). `anchor=baseline` works the same way:
`math "x^2" at=label.baseline+10,0 anchor=baseline` sits on the label's baseline.

Explicitly placed elements also push the flow cursor below them, so flow
content that follows does not overlap them (unless `float`).

**Pages:** `page 2` moves to the top of page 2; from then on, explicit
coordinates are relative to page 2's top-left corner. `newpage` starts a new
page in the flow, and coordinates become relative to that page too. Automatic
page breaks do not change the origin. `space 30` adds vertical space; it can
run over a page break. `space 200 keep` keeps the space on one page with the
block above it: if it does not fit, that block (with a heading right above it)
moves to the next page together with the space.

**Headings keep with the next block:** when the block after a heading (a
paragraph, list, table, code, callout, image, group, diagram or math) moves to
the next page, the heading moves with it, together with one short paragraph
between them.

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
  (sans, serif, mono or a family name), `size=` (font size, default 16, also
  used by `math`), `math_font=` (see Math),
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

**Centering text exactly:** use `center=X,Y` (the text's middle goes to X,Y), or
`at=X,Y width=W align=center` (the text is centered inside the W-wide box that
starts at X). Without `width=`, `align=` only affects how multiple lines line
up with each other. In the flow, `align=center` centers on the page.
`text_origin=X,Y` sets Rnote's raw text position (used by `rnotegen decompile`).

**Inline markup** inside any text: `**bold**`, `*italic*`, `_italic_`,
`~~strike~~`, `` `code` ``, `<u>underline</u>`, `<color=red>red text</color>`,
`[link text](https://...)`. Use `\*` for a literal asterisk.

### Shapes

```
rect 100,100 200x80                       # top-left + size
rect 100,100 300,180                      # two corners
rect at=100,100 size=200x80 fill=lightyellow stroke=orange radius=10 label="Inside"
ellipse center=300,300 radius=80,40
circle center=300,300 radius=40 fill=gold label="A" text_color=red font_size=24 bold
circle 300,300 40                         # center + radius
line 100,100 300,100                      # 2 points = line
line 100,100 200,150 300,100 dashed       # 3+ points = polyline
arrow 100,200 300,200                     # arrow head at the end
arrow 100,200 200,250 300,200 both        # heads at both ends
line 100,250 300,250 head=triangle tail=circle
polygon 100,300 200,300 150,380 fill=pink
curve 100,400 200,320 300,400             # quadratic: start, control, end (peak at y=360)
bezier 100,450 150,380 250,520 300,450    # cubic: start, c1, c2, end
pen 100,500 120,510 140,505 160,520 smooth color=blue width=3   # freehand stroke
highlight 100,540 300,540                 # translucent highlighter (yellow by default)
```

A quadratic `curve` passes halfway between its control point and the middle
of start/end. For a parabola from A to B with its peak at height P, use the
control point `x_mid, 2*P - (A_y + B_y)/2`.
`label=` on `rect`, `ellipse`, `circle` and `polygon` puts centered text
inside; it takes the text options `text_color=`, `font_size=`, `bold`,
`italic`, `font=`. Line-like commands (`line arrow curve bezier pen`) take
`width=` as the stroke width. Arrow heads (`head=` for the end, `tail=` for the start):
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
| `rough` | hand-drawn look (Rnote's rough style). Works on every shape, box and connector; `defaults rough=on` turns it on for everything, `mermaid rough` for a diagram |
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

- `callout "text"`: a highlighted note box in the flow. Options: `title=`,
  `fill=`, `stroke=`, `width=`, `id=`, and the placement options.
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

Inside `group at=X,Y`, coordinates are relative to X,Y. Without `at=`, write
the children as if the group's top-left corner were 0,0. The whole group is
then placed in the flow (use `align=center` to center it). The group's id
refers to its bounding box.

`min_height=H` makes the group at least H tall: the blank space below its
content belongs to the group, so in the flow the group and its space move to
the next page together. Use it for answer space on worksheets:

```
repeat 10 n from=1
  group min_height=180
    text "Problem {n}" bold
    math "\int_0^{1} x^2\,dx" at=20,28
  end
end
```

### Variables and loops

```
let colw = (page_width - 2*margin) / 7      # define a number (units and expressions allowed)
repeat 7 i                                  # i = 0, 1, ..., 6
  rect at=margin + i*colw, 80 size=(colw - 8)x600 id=col{i}
  text "Day {i+1}" center=margin + i*colw + colw/2, 100 bold
end
repeat 3 n from=1                           # n = 1, 2, 3
  bullet "Point {n}"
end
```

- Numbers anywhere can use `+ - * /`, parentheses, units and variable names.
  Built-in names: `page_width`, `page_height`, `margin`, `content_width`.
- `{expression}` inside strings and option values is replaced by its value,
  e.g. `"Day {i+1}"` or `id=col{i}`. Braces that don't contain a valid
  expression stay as they are.
- `repeat N [name]`: repeats the indented commands up to `end`. Options:
  `from=` (start value, default 0), `step=` (default 1).

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

- SVG files (`image "plot.svg"`, `svg "file.svg"` or an `svg` block) are
  cleaned up for Rnote, which renders them nested inside an `<svg>` of its own
  and would otherwise show an empty box: the XML declaration, DOCTYPE,
  comments, `<metadata>` and editor data (Inkscape, Illustrator) are removed,
  `<use>` copies are expanded, CSS (`<style>` and `style=`) becomes plain
  attributes, and ids get a per-image prefix. Output from matplotlib,
  Inkscape and most other tools works. Sizes in `pt`, `mm`, `cm`, `in`, `pc`
  and `px` are converted to px. An SVG that is not valid XML is an error with
  its line number.

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
  In the flow, a diagram is centered and shrunk to fit the page width. If it
  is too tall for the rest of the page, it shrinks by up to 25% to fit, or
  else moves to the next page. The heading before it moves along, and you
  get a warning if a large gap is left. `height=` controls the size directly.
  Mermaid's own `subgraph ... end` lines are fine inside the block; the block ends at the
  first `end` that does not close a subgraph/loop/alt.
- `markdown` supports headings, paragraphs, `-`/`*`/`1.` lists (nested by
  indentation), `- [ ]` task lists, `>` quotes, tables, fenced code,
  ```` ```mermaid ```` fences (rendered as diagrams), `---` rules, images
  `![alt](file.png)` and `<!-- pagebreak -->`. Options: `at=`, `width=`.
- `table`: Markdown pipe-table rows; `header=off` makes the first row plain.
  Long tables continue on the next page with the header repeated.

### Math

```
math "\int_0^1 x^2\,dx = \frac{1}{3}"           # TeX, as between $...$
math "e^{i\pi} + 1 = 0" align=center size=24
text "a)" at=60,300 id=qa
math "\sqrt{x^2 + y^2}" right_of=qa align=baseline gap=8   # on the label's baseline
math "Area: $A = \pi r^2$"                         # text with $...$ math parts
math font=cm
\lim_{n \to \infty} \left(1 + \frac{1}{n}\right)^n = e
end
```

- Needs matplotlib (`pip install matplotlib`); everything else works without
  it. The formula is drawn by matplotlib's mathtext as glyph outlines in a
  transparent vector image: it looks the same everywhere, but it is an
  image in Rnote, not editable text.
- Backslashes in the string are kept as written (`\nabla` is not a new
  line). Inside `repeat`, `{...}` in the formula stays TeX; `{i}` is only
  replaced in the options (`id=eq{i}`).
- Options: `size=` (font size; default `defaults size=`), `color=`,
  `opacity=`, `font=` `sans` (default; `serif` when the text font is serif),
  `serif`, `cm` (TeX look), `stix`, `stixsans`, all placement options, `id=`.
  `defaults math_font=cm` sets the font for all formulas.
- A formula is at least one text line tall, with its baseline where a line of
  text at the same position has its baseline: `text` and `math` with the
  same y line up. For taller formulas use `align=baseline` or
  `anchor=baseline`. In the flow it is left-aligned (`align=center` centers it).
- mathtext supports most TeX math (fractions, roots, sums, integrals, Greek,
  accents, `\left( \right)`, `\mathrm`, `\mathbf`, ...) but no environments
  (`\begin{...}`), no `\\` line breaks and no `\color`.
- Some TeX renders badly in mathtext; the build fixes it unless you add the
  flag `raw`: after `\int` (with no limits) a thin space is too small, so it
  gets `\!\!\!\quad`; after limits (`\int_0^1`) it gets `\ `. `\sin`, `\cos`,
  `\ln`, ... get a thin space after a letter, digit or closing bracket (`x\sin x`).
  `\left[ ... \right]_a^b` around content one line high becomes `[\, ... \,]_a^b`.
  A decimal point before `\overline{...}` loses its extra space (`0.\overline{3}`).

## 4. Editing existing notes

- To **add** content to an existing note (even a handwritten one), write a
  script with just the new content and build it with
  `rnotegen build new.rns --into existing.rnote -o result.rnote`. Flow
  content starts below the existing content, and nothing existing changes.
- To **change** existing content, use `rnotegen decompile existing.rnote -o
  existing.rns`. You get every stroke as a flat command with absolute
  coordinates: text, shapes, simplified pen strokes. Former headings, lists,
  tables and diagrams appear as their individual texts, lines and rectangles.
  Edit the commands, for example the text of one item, and build again.
  Commands added at the end flow below the existing content.

## 5. Tips for reliable results

1. Prefer the flow for documents: headings, `para`, bullets, `mermaid` and
   `markdown` need no coordinates at all.
2. For drawings, give elements ids and place them relative to each other
   (`below=`, `right_of=`, `connect`) instead of computing coordinates.
3. Keep exact coordinates inside the page: x from 56 to 738 and y from 56 to
   1067 on A4 (inside the margins).
4. Text widths are measured with the fonts installed on this machine when
   Pillow and fontconfig are available; otherwise they are slightly
   generous estimates. Leave some room, or set `width=` so that text wraps.
5. Read the warnings after each build. They tell you which line overlaps or
   overflows.
6. Images are always drawn below strokes and text (Rnote's layer order).
7. For repeated structures (columns, grids, rows of boxes), use `let` and
   `repeat` instead of computing every coordinate by hand.
8. For worksheets, put each problem in a `group min_height=...` so that a
   problem and its answer space are never split across pages.
9. Use `math` for formulas instead of SVG files made elsewhere: it sizes the
   formula to the text and lines it up with the text's baseline.

## 6. Complete example

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
