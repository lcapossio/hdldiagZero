---
name: hdldiagzero
description: Generate validated SVG block diagrams for HDL/RTL/FPGA/SoC designs. Use when asked to draw, diagram, sketch, render, or visualize Verilog, SystemVerilog, VHDL, Vivado BD, or LiteX architecture. Produces JSON specs plus SVGs, color-codes clock domains, distinguishes AXI/MM/Lite/Stream and TileLink, and hides clock/reset/JTAG/debug detail by default.
---

# hdldiagZero

Create a small JSON architecture spec from HDL, then use this skill's scripts to render and validate the SVG. Edit JSON only; never hand-edit SVG.

Pipeline: HDL -> `spec.json` -> `render.py` -> `diagram.svg` -> `validate.py`.

## Locate Scripts

Use the directory containing this `SKILL.md`. If unavailable, search `~/.codex/skills/hdldiagzero/`, `~/.claude/skills/hdldiagzero/`, then any user-named skill root. Ask once if still not found.

- `render.py spec.json out.svg`: render JSON to SVG; add `--theme dark` only when requested.
- `validate_spec.py spec.json`: run before rendering.
- `validate.py out.svg`: run after rendering.

Read references only when needed:
- `references/schema.md`: JSON fields, examples, layout options.
- `references/extraction.md`: top detection, hierarchy walking, interface classification, clock domains, exclusions.
- `references/validation.md`: validator errors and fix recipes.

## Defaults

- Theme: `light`.
- Depth: top plus direct children.
- Outputs: `docs/architecture.json` and `docs/architecture.svg` unless the user names paths.
- Hide clock/reset nets, JTAG/debug/ILA/VIO, vendor primitives, FIFO/BRAM leaves, AXI-Lite leaf-control clutter, and soft-processor internals unless explicitly requested.
- Ask only when multiple plausible tops exist and no project hint resolves them.

## Workflow

1. Find the top: prefer `top.v`, `*_top.{sv,vhd}`, `system.bd`, `litex_soc.py`, project files, or build scripts. Use `references/extraction.md` if uncertain.

2. Extract hierarchy to requested depth. For depth > 1, declare expanded parents in top-level `groups` and assign child blocks with `group`.

3. Assign clock domains from ports, constraints, PLL/MMCM outputs, transceivers, or CDC primitives. Omit unknown frequencies; do not invent `? MHz`. Use `domain_b` only for blocks whose architectural purpose is CDC; otherwise use a `cdc` edge.

4. Classify connections from actual ports: `axi-mm`, `axi-lite`, `axi-stream`, `tilelink`, `cdc`, or `generic`. Read widths from declarations; for parametric widths, use the parameter name string.

5. Write JSON. Use integer rows/cols by default; quarter steps are okay for tighter placement. Prefer `grid.cell_w/cell_h` for diagram-wide sizing and block `w/h` only for local exceptions. For larger SoCs, consider compact legends, functional `bands`, edge `external` I/O blocks, and explicit `lines`.

6. Validate and iterate:

```bash
python <skill>/validate_spec.py docs/architecture.json
python <skill>/render.py docs/architecture.json docs/architecture.svg
python <skill>/validate.py docs/architecture.svg
```

Fix JSON issues and rerun. Cap geometry-fix loops at 3; use `references/validation.md` for route and overlap recipes.

7. Final response: one or two sentences with top module, block count, clock-domain count, validator status, and links to both files.
