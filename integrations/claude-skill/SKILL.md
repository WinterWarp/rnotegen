---
name: rnote
description: Create or edit Rnote (.rnote) notes, including handwritten-style notes, study notes, worksheets, diagrams, and conversions from Markdown or Mermaid. Use whenever the user wants a .rnote file or wants something added to an existing Rnote note.
---

# Creating Rnote notes with rnotegen

1. Run `rnotegen spec` once and read it. It is the complete reference of the script language.
2. Choose the input:
   - A document (headings, text, lists, tables, a diagram or two): write an `.rns` script in
     flow mode (no coordinates), or plain Markdown (`.md`) with ```mermaid fences.
   - Only a diagram: a Mermaid file (`.mmd`).
   - A drawing with exact positions: an `.rns` script with `at=`/`center=`, ids,
     `below=`/`right_of=` and `connect`; use `let` and `repeat` for repeated structures.
   - Formulas: `math "TeX"` in a script (needs matplotlib), not SVGs made elsewhere.
     Worksheet problems with answer space: `group min_height=...`.
3. Build with a preview:
   `rnotegen build notes.rns -o notes.rnote -p notes.png`
4. Read the report: fix every error (it has a line number) and every warning about overlaps,
   content off the page or large gaps. Look at `notes.png` and fix anything that looks wrong.
   Rebuild until it is clean.
5. To add to an existing note: `rnotegen build addition.rns --into existing.rnote -o existing.rnote`.
   To change an existing note: `rnotegen decompile existing.rnote -o existing.rns`, edit, rebuild.

Tell the user where the `.rnote` file is. It opens in Rnote, and every element is an
editable Rnote stroke.
