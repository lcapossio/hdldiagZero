# JSON spec schema

Reference for the JSON the renderer expects. The agent SHOULD validate the
spec with `validate_spec.py` before rendering - it catches structural errors
(missing block ids in edges, duplicate cells, unknown domains) that would
otherwise produce a broken SVG.

## Example

```json
{
  "title": "Diagram title (optional)",
  "top": "soc_top",
  "theme": "light",
  "domains": {
    "axi":   {"freq_mhz": 100, "color": "#42A5F5", "border": "#0D47A1"},
    "video": {"freq_mhz": 148, "color": "#FFA726", "border": "#E65100"},
    "ddr":   {"freq_mhz": 333, "color": "#66BB6A", "border": "#1B5E20"}
  },
  "blocks": [
    {"id": "phy",          "label": "Video Source",   "external": true,                            "row": 0, "col": 0, "sublabel": "[16 lanes x 16b]"},
    {"id": "pix_packer",   "label": "pix_packer",     "domain": "video", "domain_b": "axi",        "row": 0, "col": 1, "sublabel": "video / axi CDC"},
    {"id": "rx_dma",       "label": "RX DMA",         "domain": "axi",                             "row": 0, "col": 2},
    {"id": "interconnect", "label": "AXI Interconnect","domain": "axi",                            "row": 0, "col": 3},
    {"id": "cpu",          "label": "SoftCore MCU",   "domain": "axi",                             "row": 0, "col": 4},
    {"id": "ddr_ctrl",     "label": "DDR Ctrl",       "domain": "ddr",                             "row": 1, "col": 4}
  ],
  "edges": [
    {"from": "phy",          "to": "pix_packer",   "kind": "generic",    "width": "16x16b"},
    {"from": "pix_packer",   "to": "rx_dma",       "kind": "axi-stream", "width": 64},
    {"from": "rx_dma",       "to": "interconnect", "kind": "axi-mm",     "width": 64},
    {"from": "cpu",          "to": "interconnect", "kind": "axi-lite",   "width": 32},
    {"from": "interconnect", "to": "ddr_ctrl",     "kind": "axi-mm",     "width": 128}
  ]
}
```

## Top-level fields

| Field     | Type   | Required  | Description |
|-----------|--------|-----------|-------------|
| `title`   | string | optional  | Title rendered at top-center. |
| `top`     | string | optional  | Informational; identifies the top module. |
| `theme`   | string | optional  | `"light"` (default) or `"dark"`. |
| `grid`    | object | optional  | Grid sizing overrides; see below. |
| `domains` | object | required* | Map of domain key -> `{freq_mhz, color, border}`. *Required unless every block is external. |
| `groups`  | object | optional  | Map of group id -> `{label}`. Used to draw dashed hierarchy containers around member blocks (depth > 1). |
| `lanes`   | object | optional  | Map of domain id -> `{rows: [...]}`. Draws full-width tinted bands per clock domain across the canvas. See *lanes* below. |
| `blocks`  | array  | required  | One or more blocks. |
| `edges`   | array  | required  | May be empty. |

## domains[name]

| Field      | Type   | Required | Description |
|------------|--------|----------|-------------|
| `freq_mhz` | number | optional | Shown in the legend when known. Omit this field when the frequency is unknown; never use placeholders like `?` or `? MHz`. |
| `color`    | string | optional | Block fill (`#RRGGBB`). Auto-assigned from a Material palette if missing. |
| `border`   | string | optional | Block stroke. Auto-paired to the fill if missing. |

## blocks[]

| Field      | Type    | Required  | Description |
|------------|---------|-----------|-------------|
| `id`       | string  | required  | Unique non-empty string. |
| `label`    | string  | optional  | Defaults to `id`. |
| `sublabel` | string  | optional  | Italic line under the label. Keep it terse. |
| `domain`   | string  | required* | Domain key from `domains`. *Optional if `external=true`. |
| `domain_b` | string  | optional  | CDC blocks only. Half fill of each domain's color. Must differ from `domain` and reference a declared domain. |
| `external` | boolean | optional  | True = off-chip / off-die. Neutral grey fill, ignores `domain`. |
| `row`      | int     | required  | 0-indexed grid row. |
| `col`      | int     | required  | 0-indexed grid column. One block per `(row, col)`. |
| `group`    | string  | optional  | Group id from `groups`. Member blocks are wrapped in a dashed rectangle at render time. Use this when expanding a parent block at depth > 1 - the group represents the parent. |

## lanes (optional)

Clock-domain "swim lanes" - tinted bands that visually group all blocks of
one domain. Each entry maps a domain id (must exist in `domains`) to the
grid rows or columns that domain occupies. The renderer draws each lane as
a low-opacity tint of the domain's color with a dashed border in the
domain's accent color, and prints the domain header
(`<name> domain  <freq> MHz`) at the top-left of the lane.

A lane is either **horizontal** (`rows`, spanning full canvas width - pick
this when data flows top-to-bottom through CDCs) or **vertical** (`cols`,
spanning full canvas height - pick this when data flows left-to-right and
each domain owns a column).

| Field  | Type   | Required | Description |
|--------|--------|----------|-------------|
| `rows` | int[]  | one of   | Non-empty list of grid row indices the domain occupies. Spans `min(rows)`..`max(rows)`. |
| `cols` | int[]  | one of   | Non-empty list of grid col indices the domain occupies. Spans `min(cols)`..`max(cols)`. |

Each lane entry **must specify exactly one** of `rows` or `cols`. Different
lanes in the same spec can choose different orientations, but they should
not overlap - the renderer doesn't prevent visual collisions.

```json
"lanes": {
  "usb": {"rows": [0]},
  "acq": {"rows": [1]},
  "ui":  {"rows": [2]}
}
```

```json
"lanes": {
  "host": {"cols": [0]},
  "sys":  {"cols": [1, 2]},
  "phy":  {"cols": [3]}
}
```

Skip lanes if your rows / columns mix domains; the visual collision will
look worse than the default per-block color.

## groups (optional)

Hierarchical containers. When extracting an HDL design at depth > 1, the
expanded children of a parent block should reference a group whose id is the
parent module name. The renderer computes the bounding box of all member
blocks and draws a dashed rectangle around them with the group label as an
uppercase header. Arrows freely cross group borders; the validator excludes
`group_*` rects from its block list.

| Field   | Type   | Required | Description |
|---------|--------|----------|-------------|
| `label` | string | optional | Header text rendered at the top-left of the dashed rect. Defaults to the group id when omitted. |

```json
"groups": {
  "tx_path": {"label": "tx_path"},
  "rx_path": {"label": "rx_path"}
}
```

## edges[]

| Field   | Type          | Required | Description |
|---------|---------------|----------|-------------|
| `from`  | string        | required | Block id (must exist in `blocks`). |
| `to`    | string        | required | Block id (must exist in `blocks`). |
| `kind`  | string        | required | One of `axi-mm`, `axi-lite`, `axi-stream`, `cdc`, `generic`. |
| `width` | int \| string | optional | Bit width as int (rendered `<n>b`), or protocol / parameter as string. Long arrows without a label fail the BITWIDTH validator check. |
| `route` | object        | optional | Per-edge routing override. See *edges[].route* below. |
| `label` | object        | optional | Per-edge label placement override. See *edges[].label* below. |

### edges[].route

By default the renderer picks side endpoints and a single-bend Manhattan path
with lane offsets to keep parallel edges apart. Use `route` to override that
on one edge without disturbing the rest.

| Field    | Type             | Default  | Description |
|----------|------------------|----------|-------------|
| `mode`   | string           | `"auto"` | `"auto"` keeps the standard Manhattan routing with parallel-edge lane offsets. `"direct"` is Manhattan with **no** lane offset - collinear endpoints produce a single straight segment, non-collinear ones a single orthogonal bend. `"orthogonal"` is a synonym for `"auto"`. Diagonal output is impossible in every mode. |
| `points` | `[[x, y], ...]`  | omitted  | Explicit waypoints in **final SVG coordinates** (i.e. the same numbers you'd read off the rendered file). When provided, this *is* the path - the renderer skips both endpoint selection and Manhattan routing. The list must have at least two points. **Consecutive points must share x or y**; diagonal segments are rejected at spec-validation time. To go from `(x1, y1)` to `(x2, y2)` insert an intermediate `(x2, y1)` or `(x1, y2)`. If the first or last point touches a block, the adjacent segment must leave/enter perpendicular to that block side; add a short outward stub before turning. |

Prefer `mode` over `points` when possible: absolute coords get stale when
blocks move, while `mode: "direct"` survives layout edits. Use `points` only
for an awkward wire where automatic routing collides with a neighbor.

### edges[].label

The renderer auto-places labels at the longest segment of the path. Use
`label` to nudge a single label out of the way of an unrelated wire.

| Field     | Type   | Default | Description |
|-----------|--------|---------|-------------|
| `dx`      | number | `0`     | Horizontal pixel offset added to the auto anchor. Negative moves left. |
| `dy`      | number | `0`     | Vertical pixel offset added to the auto anchor. Negative moves up. |
| `segment` | int    | omitted | When set, place the label on segment index *N* of the path (0 = first segment, -1 = last). Overrides the longest-segment heuristic. |
| `t`       | number | `0.5`   | When `segment` is set, fractional position along that segment (0..1). |

Setting any of `dx`/`dy`/`segment`/`t` also disables the auto-clamp that
normally pulls the label back onto the endpoints' span - the assumption is
that you know where you want the label and don't want it dragged back.

## kind catalog

| Kind          | What it is                                       | Stroke              |
|---------------|--------------------------------------------------|---------------------|
| `axi-mm`      | Full AXI4 / AXI3 memory-mapped (with bursts).    | Thick dark.         |
| `axi-lite`    | AXI4-Lite control bus. **Distinct from axi-mm.** | Slimmer dark blue.  |
| `axi-stream`  | AXI4-Stream data path.                           | Olive, open head.   |
| `cdc`         | Signal/bus crossing clock domains in flight.     | Purple dashed.      |
| `generic`     | Anything else (RGMII, SPI, custom, discretes).   | Thin grey.          |

AXI4 (full) and AXI4-Lite are separate `kind` values even though they share
port-name conventions. Pick `axi-lite` if the bus has *no* burst-related
ports (`awlen`, `awburst`, `arlen`, `wlast`, `rlast` all absent).

## grid (optional)

| Field      | Default | Description |
|------------|---------|-------------|
| `cell_w`   | 220     | Block width (px). |
| `cell_h`   | 90      | Block height. |
| `gutter_x` | 90      | Horizontal gutter between columns. |
| `gutter_y` | 70      | Vertical gutter between rows. |
| `margin`   | 36      | Canvas margin around the grid. |

Widen `gutter_x` if edge labels overflow into adjacent blocks.
Widen `cell_w` if block names truncate.
