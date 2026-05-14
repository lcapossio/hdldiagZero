---
name: hdldiagzero
description: Generate clean SVG block diagrams of HDL / RTL top-level designs. Use whenever the user asks to draw, diagram, sketch, render, or visualize an RTL / HDL / FPGA / SoC design - including phrases like "draw the top-level RTL", "diagram of the SoC", "block diagram", "make an SVG of the design", "visualize the architecture". Applies to Verilog, SystemVerilog, VHDL, Vivado BD, and LiteX projects. Color-codes by clock domain, distinguishes AXI-MM / AXI-Lite / AXI-Stream, omits clock/reset/JTAG/debug.
---

# hdldiagZero

Extract the design's architecture from HDL into a small JSON spec, then run the renderer in this skill's directory. A geometry validator runs as a final guardrail. Iterate on the JSON until validation passes. You don't author SVG.

```
HDL  --(your job)-->  spec.json  --(render.py)-->  diagram.svg  --(validate.py)-->  PASS/FAIL
```

## Skill scripts

The renderer, validators, and references all live in this skill's directory - the same directory `SKILL.md` was loaded from. Resolve their absolute paths in this priority order:

1. **Use the runtime-supplied skill path** if your runtime exposes it (the absolute path of this `SKILL.md` is the canonical anchor; the script lives next to it).
2. **Search known skill roots** for a `hdldiagzero/` sub-directory: `~/.claude/skills/`, `~/.codex/skills/`, plus any path the user mentioned as a custom skills root. The first one that contains `render.py` is the right one.
3. **Ask the user once** for the skill directory if both of the above fail.

Once you have the skill directory, all script invocations below use `<skill>/...` as the prefix.

| Script | Purpose |
|---|---|
| `render.py spec.json out.svg` | JSON -> SVG (`--theme dark` for dark mode). |
| `validate_spec.py spec.json` | JSON structural checks. Run **before** rendering. |
| `validate.py out.svg` | SVG geometry checks. Run **after** rendering. |

References (read on demand, not always loaded):
- `references/schema.md` - full JSON schema with examples.
- `references/extraction.md` - concrete patterns for finding the top, walking hierarchy, identifying clock domains, classifying interfaces, and excluding vendor / debug / generated cruft.
- `references/validation.md` - fix recipes for every validator violation.

## Defaults - don't ask, just apply

- **Theme**: `light`. Switch to `dark` only if the user's prompt mentions dark mode.
- **Hierarchy depth**: `1` (top + direct children). Go deeper only if the user asked.
- **Output paths**: `docs/architecture.json` and `docs/architecture.svg` in the project's working directory unless the user named another path.
- **Inclusions**: see "Standing rules" below - domain coloring, AXI variants distinguished, clk/rst/JTAG/debug omitted.

The only legitimate clarifying question is "which file is top?" when there are multiple top-level candidates and no clear indicator. Don't ask about theme, depth, or filenames - apply the defaults and let the user redirect.

## Workflow

1. **Find the top module.** Pick the obvious candidate (`top.v`, `*_top.{sv,vhd}`, `system.bd`, `litex_soc.py`); ask only if multiple candidates exist with no clear winner. Patterns: `references/extraction.md` section 1.

2. **Walk hierarchy to depth N** (default 1). Skip vendor IP, soft processors, BRAM/FIFO leaves, JTAG, debug-only IP, and clock primitives at every depth. Patterns and exclusion lists: `references/extraction.md` section 2 and section 4.

   When `N > 1`, every expanded parent module **must** appear in the top-level `groups` map and every child block must reference it via `group: "<parent_id>"`. The renderer draws a dashed container around each group so the hierarchy is visually obvious; without `groups`, a depth-2 spec just looks like a flat depth-1 diagram with more blocks.

3. **Map each block to a clock domain.** Look at clk_* ports, MMCM/PLL outputs, transceiver-derived clocks, and CDC primitives. Use `domain_b` (a CDC block, rendered with a split fill) **only when CDC is the block's architectural purpose** - async FIFO at a pipeline boundary, a packer that bridges video/TX clocks, an explicit clock-crossing bridge. A block primarily in one domain that happens to contain a single status synchronizer keeps its primary `domain` and routes the crossing as an explicit `cdc` edge. Detail: `references/extraction.md` section 3.

   If a clock frequency is not found in source files or build constraints, omit `freq_mhz` for that domain. Do not invent a value and do not write placeholder text like `? MHz`.

4. **Classify each connection.** Read the actual port declarations:

   | `kind`       | What it is |
   |--------------|----|
   | `axi-mm`     | Full AXI4 / AXI3 memory-mapped (with `awlen` / `awburst` / `arlen` / bursts). |
   | `axi-lite`   | AXI4-Lite control bus. **Distinct from `axi-mm`** - same field names but no burst-related ports. |
   | `axi-stream` | AXI4-Stream (`tdata` / `tvalid` / `tready` ...). |
   | `cdc`        | Signal that crosses domains in flight (re-synchronized at destination). |
   | `generic`    | Anything else (RGMII, SPI, custom buses, discretes). |

   Read widths from port declarations; don't guess. For parametric widths, use the parameter name as a string (`"width": "DATA_W"`). Extraction details: `references/extraction.md` section 5-6.

5. **Emit JSON spec** to `docs/architecture.json` (or the user-named path). Schema: `references/schema.md`.

   Use `grid.cell_w` / `grid.cell_h` for diagram-wide block sizing. Use block
   fields `w` / `h` only for local overrides, such as compact leaf peripherals
   or an intentionally larger interconnect.

   Place blocks on integer `row` / `col` positions by default. When related
   blocks should sit closer together, use quarter steps (`0.25`, `0.5`,
   `0.75`) instead of shrinking the whole grid.

   If you use explicit `route.points`, every segment must be orthogonal, and
   endpoints attached to blocks must leave/enter perpendicular to the touched
   block side. Add a short outward stub before the first turn; never run the
   first or last segment tangentially along the block edge. Do not add dogleg
   loops when two block ports can connect directly; use `route.mode: "direct"`
   or remove the extra waypoints.

6. **Validate the spec** before rendering: `python <skill>/validate_spec.py docs/architecture.json`. Fix any reported issues; do NOT call the renderer with a broken spec.

7. **Render**: `python <skill>/render.py docs/architecture.json docs/architecture.svg`. Add `--theme dark` if requested.

8. **Validate the SVG**: `python <skill>/validate.py docs/architecture.svg`. On violation, edit the JSON and re-run from step 6. Cap iterations at 3. Recipes: `references/validation.md`.

9. **Final report**: one or two sentences - top module, block count, clock-domain count, validator status. Link both files (`[architecture.json](docs/architecture.json)`, `[architecture.svg](docs/architecture.svg)`) so the user can iterate by editing the JSON.

## Standing rules - included / excluded

These are unconditional defaults; don't surface them as questions:

- **Skip clk/rst nets**. Color encodes domain.
- **Skip AXI-Lite control paths to leaf peripherals**. Show AXI-Lite only when structurally important (e.g. one interconnect fan-out edge).
- **Don't expand soft processors** (MicroBlaze, PicoRV, VexRiscv, Ibex). Single block.
- **Drop JTAG / debug headers and paths** entirely.
- **Drop ILA / VIO / system-ila** unless the user explicitly asks.
- **Drop clock primitives** (MMCM, PLL, BUFG, clk_wiz). Domain shows on color.
- **Don't author the SVG yourself**. The renderer owns geometry. Edits go in the JSON, not the SVG.
