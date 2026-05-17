# Validator fix recipes

Two validators ship with the skill:

- `validate_spec.py spec.json` - structural JSON checks (run **before** rendering).
- `validate.py out.svg` - geometry checks (run **after** rendering).

Both exit 0 on pass, 1 when validation reports violations, and 2 for
usage/parse errors. For SVG validation, stdout carries the exact violation
count; one bad route can legitimately trigger multiple rules.

## validate_spec.py - JSON structural

Strict checks. The validator rejects rather than coerces; fix the JSON
before calling the renderer.

**Structural integrity**
- Top level is an object; `blocks` is a non-empty list; `edges` is a list.
- Unknown fields are rejected at every level (top, `grid`, `domains[*]`,
  `blocks[*]`, `edges[*]`).
- `theme` is `"light"` or `"dark"` if present.
- `title` and `top` are strings if present.

**Domains**
- Each domain entry is an object with only `freq_mhz` / `color` / `border`.
- `freq_mhz` is numeric (int or float) when present. Omit it when unknown;
  do not use placeholders like `?` or `? MHz`.
- `color` and `border` match `^#[0-9a-fA-F]{6}$` (six hex digits, no alpha,
  no 3-digit shorthand). Renderer accepts only this format.
- `domains` is required when at least one block is non-external.

**Blocks**
- `id` is a non-empty string and unique within the spec.
- `row` and `col` are non-negative numbers in 0.25 steps. **Booleans are
  rejected** even though Python's `bool` subclasses `int`.
- One block per `(row, col)`; duplicate cells are flagged with the other
  occupant's id.
- Optional per-block `w` and `h` are positive ints in pixels. Use them for
  local block-size overrides; omit them for the diagram-wide `grid.cell_w` /
  `grid.cell_h` defaults.
- `label` and `sublabel` are strings if present. `lines`, when present, is a
  non-empty list of strings and becomes the complete rendered block text;
  `label` / `sublabel` are ignored.
- `external` must be a real JSON boolean if present (string `"false"` and
  int `0` both fail).
- `side`, when present, is one of `left`, `right`, `top`, `bottom`. Use it on
  edge-placed external blocks so arrows connect to the inward-facing side.
- An `external: true` block cannot also set `domain` or `domain_b` - the
  block isn't in any internal clock domain by definition.
- A non-external block must set `domain`, and that domain must be declared
  in `domains`.
- `domain_b` (if set on a non-external block) must reference a declared
  domain that is **different** from `domain`.
- `group` (if set on a block) must reference a key in the top-level `groups`
  map. Each declared group must be an object whose only allowed field is
  `label` (string).
- `lanes` (if present) is an object keyed on declared domain ids. Each entry
  must specify **exactly one** of `rows` or `cols`, with a non-empty list of
  non-negative numbers in 0.25 steps. `rows` -> horizontal band, `cols` ->
  vertical band.
- `bands` (if present) is an object keyed on functional region ids. Each entry
  must specify **exactly one** of `rows` or `cols`, using non-negative numbers
  in 0.25 steps; optional `color` / `border` values are `#RRGGBB`. `label`
  may be an empty string or null to suppress the band header.
- `legend`, if present, is a boolean or one of `"right"`, `"compact"`,
  `"none"`.

**Edges**
- `from` and `to` must reference existing block ids.
- Self-edges (`from == to`) are rejected.
- Duplicate ordered `(from, to)` pairs are rejected because they would render
  duplicate SVG `id` attributes.
- `kind` must be one of `axi-mm`, `axi-lite`, `axi-stream`, `tilelink`,
  `cdc`, `generic`.
- `width` must be an int or a short safe string when present. String labels
  must match `^\d+b?$|^[\w/+\- ]{1,24}$` so they stay readable and cannot be
  abused as arbitrary diagram text.
- `route.mode` (if set) is `"auto"`, `"direct"`, or `"orthogonal"`.
- `route.points` (if set) is a list of at least two `[x, y]` pairs with numeric
  coords. **Consecutive points must share x or y** - diagonal segments are
  rejected; insert an orthogonal waypoint instead.
- `label.dx` and `label.dy` are numeric; `label.segment` is int; `label.t` is
  in `[0, 1]`.

**Grid**
- All keys are known (`cell_w`, `cell_h`, `gutter_x`, `gutter_y`, `margin`).
- All values are non-negative ints. Floats and bools rejected.

## validate.py - SVG geometry

| Code         | Means                                                       |
|--------------|-------------------------------------------------------------|
| `CROSSING`   | Arrow path passes through a block it doesn't connect to.    |
| `SPACING`    | Two parallel arrows < 15px apart (look like one line).      |
| `OVERLAP`    | Two arrows share the exact same route segment.              |
| `STUB`       | Arrow shaft shorter than ~1.5x the arrowhead.               |
| `TEXT_BLOCK` | A block overlaps text that isn't its own label.             |
| `TEXT_ARROW` | An arrow passes through text that isn't its own edge label. |
| `TEXT_TEXT`  | Two separate text labels significantly overlap.             |
| `PORT`       | Two arrow endpoints on the same block within 12px.          |
| `BITWIDTH`   | A long arrow has no nearby text label.                      |
| `DIAGONAL`   | An arrow segment is neither horizontal nor vertical.        |
| `ENDPOINT`   | An arrow start/end point is not attached to a block edge.   |
| `PERPENDICULAR` | An arrow endpoint leaves/enters along the block edge.    |
| `LOOP`       | A route detours even though a clear direct connection exists. |

## Fix recipes

### CROSSING
Move one of the involved blocks to a different `(row, col)` so the route
doesn't cross the offender, OR introduce an intermediate block to break the
route, OR widen `gutter_x` / `gutter_y` so the bend has room.

### TEXT_BLOCK on edge labels
The label is too long for its gutter. In order of cheapness:
1. Shorten the label (`64b` instead of `AXI-MM 64b`).
2. Increase `gutter_x` / `gutter_y` in the spec's `grid`.
3. Move the source / target blocks to non-adjacent grid cells.

### TEXT_BLOCK on block labels
A block's `label` overflows its cell. Shorten the label, add a `sublabel`
to split content vertically, or increase `cell_w` in `grid`.

### PORT
Too many edges share one side of a block. Either split the block into
sub-blocks at different `(row, col)`s, or restructure topology so some
edges enter via different sides. Rare with the default renderer.

### OVERLAP
Two edges occupy the same visible horizontal or vertical run. Move one edge
into a different gutter, or reroute it around the shared corridor. If the two
edges represent the same bus, merge them into one higher-level bus edge.

### BITWIDTH
The edge has no `width` in the JSON. Add it. For protocols, a string is
fine: `"width": "RGMII"` / `"width": "SPI"`. For parametric widths use the
parameter name: `"width": "DATA_W"`.

### TEXT_ARROW
Almost always a hand-edited SVG, not the renderer. Re-render from the JSON.

### STUB / SPACING
Almost never with the renderer. If they fire, it's a renderer bug - capture
the JSON and the SVG and report.

### DIAGONAL
Either a hand-edited SVG or explicit `route.points` whose consecutive entries
don't share an x or y coordinate. The renderer itself never emits diagonals.
Re-render from the JSON, or insert an intermediate orthogonal waypoint
(`[x1, y1] -> [x2, y1] -> [x2, y2]` instead of `[x1, y1] -> [x2, y2]`).

### ENDPOINT
Usually explicit `route.points`. The first point must sit exactly on the
source block boundary and the last point must sit exactly on the target block
boundary. Do not start inside the block or in the gutter. Use the rendered
block `x/y/width/height` values, or prefer `route.mode` when a standard route
is enough.

### PERPENDICULAR
Usually explicit `route.points`. A route point on a block's left/right side
must connect to a horizontal segment; a point on a top/bottom side must connect
to a vertical segment. The segment must point outward from the block before it
turns. Add a short stub:

- right side: `[block_right, y] -> [block_right + 40, y] -> ...`
- left side: `[block_left, y] -> [block_left - 40, y] -> ...`
- top side: `[x, block_top] -> [x, block_top - 40] -> ...`
- bottom side: `[x, block_bottom] -> [x, block_bottom + 40] -> ...`

### LOOP
The connected ports are collinear, face each other, and have no intervening
block, but the route still leaves the direct segment and comes back. Remove
the extra waypoints or set `route.mode: "direct"`. If the detour exists to
avoid a text label, prefer moving the label with `label.dx` / `label.dy`;
block-to-block geometry should stay direct.

## When iteration doesn't converge

Cap at 3 retries. If the validator still reports after the 4th render:

- Print the remaining violations verbatim (so the user sees what failed).
- Tell the user the layout isn't converging.
- Suggest a topology change. Examples that usually unstick things:
  - "Place the interconnect in the center of the grid, not on the edge."
  - "Split row 2 into two rows so labels have more horizontal room."
  - "Promote the CDC block to its own column instead of sharing one with the producer."
