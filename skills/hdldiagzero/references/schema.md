# JSON spec schema

Reference for the JSON the renderer expects. Validate with `validate_spec.py`
before rendering - it catches structural errors (missing block ids in edges,
duplicate cells, unknown domains) that would otherwise produce a broken SVG.

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
    {"id": "phy",          "label": "Video Source",    "external": true,                     "row": 0, "col": 0, "sublabel": "[16 lanes x 16b]"},
    {"id": "pix_packer",   "label": "pix_packer",      "domain": "video", "domain_b": "axi", "row": 0, "col": 1, "sublabel": "video / axi CDC"},
    {"id": "rx_dma",       "label": "RX DMA",          "domain": "axi",                      "row": 0, "col": 2},
    {"id": "interconnect", "label": "AXI Interconnect", "domain": "axi",                     "row": 0, "col": 3},
    {"id": "cpu",          "label": "SoftCore MCU",    "domain": "axi",                      "row": 1, "col": 3},
    {"id": "ddr_ctrl",     "label": "DDR Ctrl",        "domain": "ddr",                      "row": 0, "col": 4}
  ],
  "edges": [
    {"from": "phy",        "to": "pix_packer",   "kind": "generic",    "width": "16x16b"},
    {"from": "pix_packer", "to": "rx_dma",       "kind": "axi-stream", "width": 64},
    {"from": "rx_dma",     "to": "interconnect", "kind": "axi-mm",     "width": 64},
    {"from": "cpu",        "to": "interconnect", "kind": "axi-lite",   "width": 32},
    {"from": "interconnect","to": "ddr_ctrl",    "kind": "axi-mm",     "width": 128}
  ]
}
```

## Top-level fields

| Field     | Type   | Required  | Description |
|-----------|--------|-----------|-------------|
| `title`   | string | optional  | Title rendered at top-center. |
| `top`     | string | optional  | Informational; identifies the top module. |
| `theme`   | string | optional  | `"light"` (default) or `"dark"`. |
| `legend`  | bool \| string | optional | `true` / `"right"` (default), `false` / `"none"` to hide, or `"compact"` to show only clock-domain colors. |
| `grid`    | object | optional  | Grid sizing overrides; see *grid*. |
| `domains` | object | required* | Map of domain key -> `{freq_mhz, color, border}`. *Required unless every block is external. |
| `groups`  | object | optional  | Map of group id -> `{label}`. Dashed hierarchy containers around member blocks (depth > 1). See *groups*. |
| `lanes`   | object | optional  | Map of domain id -> `{rows \| cols}`. Full-canvas tinted band per clock domain. See *lanes*. |
| `bands`   | object | optional  | Map of band id -> `{label, rows \| cols, color, border}`. Regions that are not pure clock domains. See *bands*. |
| `blocks`  | array  | required  | One or more blocks. |
| `edges`   | array  | required  | May be empty. |

## domains[name]

| Field      | Type   | Required | Description |
|------------|--------|----------|-------------|
| `freq_mhz` | number | optional | Shown in the legend. Omit when unknown; never use placeholders like `?` or `? MHz`. |
| `color`    | string | optional | Block fill (`#RRGGBB`). Auto-assigned from a Material palette if missing. |
| `border`   | string | optional | Block stroke (`#RRGGBB`). Auto-paired to the fill if missing. |

## blocks[]

| Field      | Type    | Required  | Description |
|------------|---------|-----------|-------------|
| `id`       | string  | required  | Unique non-empty string. |
| `label`    | string  | optional  | Defaults to `id`. |
| `sublabel` | string  | optional  | Terse italic line under the label. |
| `lines`    | string[] | optional | Explicit compact label lines. When present, this is the complete block text and `label` / `sublabel` are ignored. Set only one text model per block. |
| `domain`   | string  | required* | Domain key from `domains`. *Optional if `external=true`. |
| `domain_b` | string  | optional  | CDC blocks only; renders a split fill. Must be a declared domain **different** from `domain`. |
| `external` | boolean | optional  | True = off-chip/off-die: neutral grey fill, ignores `domain` (cannot set `domain`/`domain_b`). |
| `side`     | string  | optional  | `external` blocks only: `left`/`right`/`top`/`bottom` canvas edge; the inward-facing side becomes the preferred port. |
| `row`      | number  | required  | 0-indexed grid row, in 0.25 steps. |
| `col`      | number  | required  | 0-indexed grid column, in 0.25 steps. One block per `(row, col)`. |
| `w`        | int     | optional  | Per-block width override, px (positive int). Defaults to `grid.cell_w`. |
| `h`        | int     | optional  | Per-block height override, px (positive int). Defaults to `grid.cell_h`. |
| `group`    | string  | optional  | Group id from `groups` (the parent module at depth > 1). Members are wrapped in a dashed rectangle. |

## lanes (optional)

Clock-domain swim lanes: a low-opacity tint of the domain's color with a
dashed accent border and a `<name> domain  <freq> MHz` header. Each entry keys
a **declared** domain id. Use `rows` (horizontal, data flows top-to-bottom
through CDCs) or `cols` (vertical, data flows left-to-right, one column per
domain) - **exactly one** per entry. Lanes should not overlap; the renderer
doesn't prevent visual collisions, so skip lanes when rows/cols mix domains.

| Field  | Type   | Required | Description |
|--------|--------|----------|-------------|
| `rows` | number[] | one of | Non-empty grid rows in 0.25 steps. Spans `min`..`max`. Horizontal band. |
| `cols` | number[] | one of | Non-empty grid columns in 0.25 steps. Spans `min`..`max`. Vertical band. |

```json
"lanes": {"usb": {"rows": [0]}, "acq": {"rows": [1]}, "ui": {"rows": [2]}}
```
```json
"lanes": {"host": {"cols": [0]}, "sys": {"cols": [1, 2]}, "phy": {"cols": [3]}}
```

## bands (optional)

Like lanes but not tied to clock domains - use for broad regions ("core + bus",
"secure services", "peripherals + AON") where rows/cols mix domains. Same
`rows` xor `cols` rule as lanes.

| Field    | Type   | Required | Description |
|----------|--------|----------|-------------|
| `label`  | string \| null | optional | Header at top-left. Defaults to the band id; set `""` or `null` to suppress it (e.g. when a group header already labels the region). |
| `rows`   | number[] | one of | Non-empty grid rows in 0.25 steps. Spans `min`..`max`. |
| `cols`   | number[] | one of | Non-empty grid columns in 0.25 steps. Spans `min`..`max`. |
| `color`  | string | optional | Fill (`#RRGGBB`). Defaults to slate. |
| `border` | string | optional | Border/header color (`#RRGGBB`). Defaults to soft ink. |

```json
"bands": {
  "core_bus": {"label": "core + bus fabric", "rows": [0], "color": "#DBEAFE", "border": "#2563EB"},
  "secure": {"label": "secure services", "rows": [1, 2], "color": "#FEE2E2", "border": "#B91C1C"}
}
```

## groups (optional)

Hierarchical containers. At depth > 1, expanded children reference a group
whose id is the parent module name. The renderer draws a dashed rectangle
around the members' bounding box with an uppercase header; arrows cross group
borders freely and the validator excludes `group_*` rects from its block list.

| Field   | Type   | Required | Description |
|---------|--------|----------|-------------|
| `label` | string | optional | Header at top-left of the dashed rect. Defaults to the group id. |

```json
"groups": {"tx_path": {"label": "tx_path"}, "rx_path": {"label": "rx_path"}}
```

## edges[]

| Field   | Type          | Required | Description |
|---------|---------------|----------|-------------|
| `from`  | string        | required | Block id (must exist). |
| `to`    | string        | required | Block id (must exist). |
| `kind`  | string        | required | One of `axi-mm`, `axi-lite`, `axi-stream`, `tilelink`, `cdc`, `generic`. |
| `width` | int \| string | optional | Bit width int (rendered `<n>b`) or short protocol/parameter label matching `^\d+b?$|^[\w/+\- ]{1,24}$`. Long unlabeled arrows fail the BITWIDTH check. |
| `route` | object        | optional | Per-edge routing override. See *edges[].route*. |
| `label` | object        | optional | Per-edge label placement override. See *edges[].label*. |

Each ordered `(from, to)` pair must be unique (duplicates would render
duplicate SVG ids). For two links between the same blocks, merge the labels
(e.g. `"cmd + rsp"`) or route one through a named intermediate block.

### edges[].route

Default: side endpoints + single-bend Manhattan path with lane offsets to keep
parallel edges apart. Prefer `mode` over `points` - absolute coords go stale
when blocks move; `mode: "direct"` survives layout edits. Use `points` only for
an awkward wire where auto-routing collides with a neighbor.

| Field    | Type             | Default  | Description |
|----------|------------------|----------|-------------|
| `mode`   | string           | `"auto"` | `"auto"`/`"orthogonal"`: standard Manhattan with parallel-edge lane offsets. `"direct"`: no lane offset - one straight segment when collinear, one bend otherwise. Never diagonal. |
| `points` | `[[x, y], ...]`  | omitted  | Explicit waypoints in **final SVG coordinates**; when set this *is* the path (no endpoint selection or routing). >=2 points; **consecutive points must share x or y** (diagonals rejected - insert `(x2, y1)` or `(x1, y2)`). A point touching a block must leave/enter perpendicular to that side - add a short outward stub before turning. |

### edges[].label

Auto-placed at the longest path segment. Setting any field below also disables
the auto-clamp that pulls the label back onto the endpoints' span.

| Field     | Type   | Default | Description |
|-----------|--------|---------|-------------|
| `dx`      | number | `0`     | Horizontal px offset from the auto anchor (negative = left). |
| `dy`      | number | `0`     | Vertical px offset from the auto anchor (negative = up). |
| `segment` | int    | omitted | Place on segment index *N* (0 = first, -1 = last), overriding the longest-segment heuristic. |
| `t`       | number | `0.5`   | With `segment` set, fractional position along it (0..1). |

## kind catalog

| Kind          | What it is                                       | Stroke              |
|---------------|--------------------------------------------------|---------------------|
| `axi-mm`      | Full AXI4 / AXI3 memory-mapped (with bursts).    | Thick dark.         |
| `axi-lite`    | AXI4-Lite control bus. **Distinct from axi-mm.** | Slimmer dark blue.  |
| `axi-stream`  | AXI4-Stream data path.                           | Olive, open head.   |
| `tilelink`    | TileLink, including OpenTitan TL-UL fabrics.     | Teal solid.         |
| `cdc`         | Signal/bus crossing clock domains in flight.     | Purple dashed.      |
| `generic`     | Anything else (RGMII, SPI, custom, discretes).   | Thin grey.          |

Full AXI and AXI4-Lite are separate kinds despite shared port names: pick
`axi-lite` only when the bus has **no** burst ports (`awlen`, `awburst`,
`arlen`, `wlast`, `rlast` all absent).

## grid (optional)

| Field      | Default | Description |
|------------|---------|-------------|
| `cell_w`   | 220     | Block width (px). Widen if block names truncate. |
| `cell_h`   | 90      | Block height (px). |
| `gutter_x` | 90      | Horizontal gutter between columns. Widen if edge labels overflow into blocks. |
| `gutter_y` | 70      | Vertical gutter between rows. |
| `margin`   | 36      | Canvas margin around the grid. |
