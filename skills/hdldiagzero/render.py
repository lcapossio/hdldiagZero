#!/usr/bin/env python3
"""Render an HDL block-diagram SVG from a JSON architecture spec.

Author: Leonardo Capossio - bard0 design - hello@bard0.com
Year:   2026

Usage:
    python render.py [--theme light|dark] <spec.json> <output.svg>

The model emits a small JSON spec; this script handles all the SVG geometry.
That separation is the whole point: the model's intuition is bad at routing
coordinates, so we deny it the chance.

JSON schema:
{
  "title":  "<diagram title>",          # optional
  "top":    "<top module name>",        # optional, just for the title bar
  "grid": {                             # optional - all keys defaulted
    "cell_w":   220,
    "cell_h":   90,
    "gutter_x": 120,
    "gutter_y": 60,
    "margin":   30
  },
  "domains": {                          # required
    "<name>": {"freq_mhz": <n>, "color": "<#rrggbb>"}
  },
  "blocks": [                           # required
    {"id": "<id>", "label": "<text>", "domain": "<domain>",
     "row": <int>, "col": <int>}
  ],
  "edges": [                            # required
    {"from": "<id>", "to": "<id>",
     "kind": "axi-mm" | "axi-stream" | "generic",
     "width": <int> | "<string>"}
  ]
}

Block placement: explicit (row, col). Edge routing: single-bend Manhattan with
the bend in the gutter between source and target columns/rows. Endpoints on
each block side are distributed evenly along the edge so two arrows never meet
at the same point.
"""

import json
import sys

DEFAULT_GRID = dict(cell_w=220, cell_h=90, gutter_x=120, gutter_y=70, margin=36)

FONT_STACK = ("ui-sans-serif, system-ui, -apple-system, 'Segoe UI', "
              "Roboto, 'Helvetica Neue', Arial, sans-serif")

BLOCK_RX    = 8
CDC_DASH    = "6,3"
GROUP_DASH  = "6,4"
GROUP_PAD_X    = 16
GROUP_PAD_TOP  = 28
GROUP_PAD_BOT  = 14

# Clock-domain lanes (full-width tinted bands behind blocks).
LANE_DASH       = "6,4"
LANE_PAD_TOP    = 30
LANE_PAD_BOT    = 14
LANE_SIDE_PAD   = 10
LANE_FILL_OPACITY = 0.13
BAND_FILL_OPACITY = 0.10

# Top-right legend card.
LEGEND_W           = 240
LEGEND_PAD_OUTER   = 16
LEGEND_PAD_INNER   = 14
LEGEND_HEADER_H    = 22
LEGEND_ROW_H       = 22
LEGEND_SECTION_GAP = 8
LEGEND_SWATCH_W    = 22
LEGEND_SWATCH_H    = 14
LEGEND_ARROW_W     = 42

# Two themes. Switch via spec["theme"] = "dark" or --theme dark on the CLI.
# Dark theme uses a deep-navy canvas with brightened accent colors for arrows
# and labels so they read against the background.
THEMES = {
    "light": {
        "ink":          "#1f2937",   # primary text/stroke
        "ink_soft":     "#374151",
        "border":       "#1f2937",   # block border when domain doesn't override
        "bg":           "#f8fafc",   # canvas
        "label_bg":     "#ffffff",   # edge-label pill fill
        "label_bord":   "#e2e8f0",
        "ext_fill":     "#E0E0E0",   # external / off-chip block
        "ext_border":   "#424242",
        "ext_text":     "#1f2937",
        "stream":       "#5C5C00",   # AXI-S accent
        "axil":         "#0D47A1",   # AXI-L accent
        "cdc":          "#6A1B9A",   # CDC accent
        "legend_bg":    "#ffffff",
        "legend_bord":  "#cbd5e1",
    },
    "dark": {
        "ink":          "#e2e8f0",
        "ink_soft":     "#9ca3af",
        "border":       "#4b5563",
        "bg":           "#000000",
        "label_bg":     "#1f2937",
        "label_bord":   "#4b5563",
        "ext_fill":     "#374151",
        "ext_border":   "#9ca3af",
        "ext_text":     "#e2e8f0",
        "stream":       "#d9f99d",
        "axil":         "#93c5fd",
        "cdc":          "#d8b4fe",
        "legend_bg":    "#0f172a",
        "legend_bord":  "#475569",
    },
}

# Material-ish domain palette. Fill = lighter 400-shade; border = darker 900-
# shade for the light theme. In dark mode we override the auto-border to the
# theme's slate variant so edges read against the dark canvas.
DEFAULT_DOMAIN_PALETTE = [
    ("#42A5F5", "#0D47A1"),  # blue
    ("#66BB6A", "#1B5E20"),  # green
    ("#FFA726", "#E65100"),  # orange
    ("#AB47BC", "#4A148C"),  # purple
    ("#EF5350", "#B71C1C"),  # red
    ("#26A69A", "#004D40"),  # teal
    ("#FFCA28", "#F57F17"),  # amber
    ("#EC407A", "#880E4F"),  # pink
]


KIND_PREFIXES = {
    "axi-mm":     "AXI-MM",
    "axi-stream": "AXI-S",
    "axi-lite":   "AXI-L",
    "cdc":        "CDC",
    "generic":    "",
}


def kind_attrs_for(theme):
    """Per-kind line/marker attributes, colored against the active theme."""
    return {
        "axi-mm":     dict(stroke_width=2.5, stroke=theme["ink"],
                           marker="ah-solid",  dash=None),
        "axi-stream": dict(stroke_width=2.2, stroke=theme["stream"],
                           marker="ah-stream", dash=None),
        "axi-lite":   dict(stroke_width=2.5, stroke=theme["axil"],
                           marker="ah-axil",   dash=None),
        "cdc":        dict(stroke_width=1.8, stroke=theme["cdc"],
                           marker="ah-cdc",    dash=CDC_DASH),
        "generic":    dict(stroke_width=1.4, stroke=theme["ink_soft"],
                           marker="ah-thin",   dash=None),
    }


def theme_of(spec, override=None):
    name = override or spec.get("theme", "light")
    if name not in THEMES:
        name = "light"
    return name, THEMES[name]


def grid_of(spec):
    g = dict(DEFAULT_GRID)
    g.update(spec.get("grid", {}))
    return g


def legend_mode_of(spec):
    mode = spec.get("legend", "right")
    if mode is False or mode == "none":
        return "none"
    if mode == "compact":
        return "compact"
    return "right"


def block_rect(g, b):
    x = g["margin"] + b["col"] * (g["cell_w"] + g["gutter_x"])
    y = g["margin"] + b["row"] * (g["cell_h"] + g["gutter_y"])
    w = b.get("w", g["cell_w"])
    h = b.get("h", g["cell_h"])
    return x, y, w, h


def side_facing_canvas(block):
    """Return the side of an edge-placed external block that faces inward."""
    side = block.get("side")
    if side == "left":
        return "right"
    if side == "right":
        return "left"
    if side == "top":
        return "bottom"
    if side == "bottom":
        return "top"
    return None


def determine_sides(a, b):
    """Pick which side the edge exits A and enters B based on grid position."""
    a_side = side_facing_canvas(a) if a.get("external") else None
    b_side = side_facing_canvas(b) if b.get("external") else None
    if a_side and b_side:
        return a_side, b_side
    if a_side:
        return a_side, {
            "right": "left",
            "left": "right",
            "top": "bottom",
            "bottom": "top",
        }[a_side]
    if b_side:
        return {
            "right": "left",
            "left": "right",
            "top": "bottom",
            "bottom": "top",
        }[b_side], b_side
    if b["col"] > a["col"]:
        return "right", "left"
    if b["col"] < a["col"]:
        return "left", "right"
    if b["row"] > a["row"]:
        return "bottom", "top"
    if b["row"] < a["row"]:
        return "top", "bottom"
    raise ValueError(f"edge between co-located blocks {a['id']} and {b['id']}")


def side_endpoint(g, block, side, idx, total):
    """Endpoint on `side` of `block`, position `idx` of `total` (1-indexed-fraction)."""
    x, y, w, h = block_rect(g, block)
    frac = (idx + 1) / (total + 1)
    if side == "right":
        return (x + w, y + frac * h)
    if side == "left":
        return (x, y + frac * h)
    if side == "top":
        return (x + frac * w, y)
    if side == "bottom":
        return (x + frac * w, y + h)
    raise ValueError(side)


def manhattan(g, from_pt, to_pt, from_side, to_side, lane_offset=0):
    """Single-bend orthogonal route with the bend placed in a gutter.

    `lane_offset` shifts the bend within the gutter so multiple parallel edges
    sharing the same channel don't sit on top of each other."""
    fx, fy = from_pt
    tx, ty = to_pt
    out = [(fx, fy)]

    def add(pt):
        if out[-1] != pt:
            out.append(pt)

    def row_lane(y):
        """Return the nearest horizontal gutter lane above/below a same-row edge."""
        above = y - g["cell_h"] / 2 - g["gutter_y"] / 2
        below = y + g["cell_h"] / 2 + g["gutter_y"] / 2
        return above if above >= g["margin"] else below

    def col_lane(x):
        """Return the nearest vertical gutter lane left/right of a same-column edge."""
        left = x - g["cell_w"] / 2 - g["gutter_x"] / 2
        right = x + g["cell_w"] / 2 + g["gutter_x"] / 2
        return left if left >= g["margin"] else right

    horiz_pair = {("right", "left"), ("left", "right")}
    vert_pair = {("bottom", "top"), ("top", "bottom")}

    if (from_side, to_side) in horiz_pair:
        # bend in the horizontal gutter between the two columns
        bend_x = (fx + tx) / 2 + lane_offset
        if abs(fy - ty) < 1e-6:
            if from_side == "right":
                src_lane_x = fx + g["gutter_x"] / 2
                dst_lane_x = tx - g["gutter_x"] / 2
            else:
                src_lane_x = fx - g["gutter_x"] / 2
                dst_lane_x = tx + g["gutter_x"] / 2
            if abs(src_lane_x - dst_lane_x) < 1e-6:
                add((bend_x, fy))
            else:
                lane_y = row_lane(fy)
                add((src_lane_x, fy))
                add((src_lane_x, lane_y))
                add((dst_lane_x, lane_y))
                add((dst_lane_x, ty))
        else:
            add((bend_x, fy))
            add((bend_x, ty))
    elif (from_side, to_side) in vert_pair:
        bend_y = (fy + ty) / 2 + lane_offset
        if abs(fx - tx) < 1e-6:
            if from_side == "bottom":
                src_lane_y = fy + g["gutter_y"] / 2
                dst_lane_y = ty - g["gutter_y"] / 2
            else:
                src_lane_y = fy - g["gutter_y"] / 2
                dst_lane_y = ty + g["gutter_y"] / 2
            if abs(src_lane_y - dst_lane_y) < 1e-6:
                add((fx, bend_y))
            else:
                lane_x = col_lane(fx)
                add((fx, src_lane_y))
                add((lane_x, src_lane_y))
                add((lane_x, dst_lane_y))
                add((tx, dst_lane_y))
        else:
            add((fx, bend_y))
            add((tx, bend_y))
    elif from_side in ("right", "left") and to_side in ("top", "bottom"):
        add((tx, fy))
    elif from_side in ("top", "bottom") and to_side in ("right", "left"):
        add((fx, ty))
    else:
        # Same side on both ends -> u-turn around the outside.
        if from_side == "right":
            mx = max(fx, tx) + g["gutter_x"] / 2
            add((mx, fy)); add((mx, ty))
        elif from_side == "left":
            mx = min(fx, tx) - g["gutter_x"] / 2
            add((mx, fy)); add((mx, ty))
        elif from_side == "bottom":
            my = max(fy, ty) + g["gutter_y"] / 2
            add((fx, my)); add((tx, my))
        else:
            my = min(fy, ty) - g["gutter_y"] / 2
            add((fx, my)); add((tx, my))

    add((tx, ty))
    return out


def path_d(points):
    cmds = [f"M {points[0][0]:.1f},{points[0][1]:.1f}"]
    for x, y in points[1:]:
        cmds.append(f"L {x:.1f},{y:.1f}")
    return " ".join(cmds)


def label_anchor(points, label_cfg=None):
    """Anchor the label at the path's bend region - that's the gutter between
    the two endpoint blocks, where there's clear space.

    `label_cfg` (optional) supports per-edge overrides:
        segment: int  index into the path's segments (0..N-2). Negative wraps.
        t:       0..1 fractional position along that segment.
        dx, dy:  pixel offsets added to the chosen anchor.

    Without `segment`/`t`, prefer the longest available segment so larger
    labels stay clear of adjacent block bodies."""
    cfg = label_cfg or {}
    if "segment" in cfg or "t" in cfg:
        segs = list(zip(points, points[1:]))
        n_segs = len(segs)
        seg_idx = int(cfg.get("segment", 0))
        if seg_idx < 0:
            seg_idx += n_segs
        seg_idx = max(0, min(seg_idx, n_segs - 1))
        a, b = segs[seg_idx]
        t = float(cfg.get("t", 0.5))
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
    else:
        best = (0, points[0], points[-1])
        for a, b in zip(points, points[1:]):
            length = abs(a[0] - b[0]) + abs(a[1] - b[1])
            if length > best[0]:
                best = (length, a, b)
        _, a, b = best
        x = (a[0] + b[0]) / 2
        y = (a[1] + b[1]) / 2
    orient = "h" if abs(a[0] - b[0]) >= abs(a[1] - b[1]) else "v"
    x += float(cfg.get("dx", 0))
    y += float(cfg.get("dy", 0))
    return x, y, orient


def edge_label(e):
    kind = e.get("kind", "generic")
    prefix = KIND_PREFIXES.get(kind, "")
    width = e.get("width", "")
    if isinstance(width, int):
        width = f"{width}b"
    if prefix and width:
        return f"{prefix} {width}"
    return prefix or str(width) or ""


def block_label_lines(b):
    if b.get("lines"):
        return [str(line) for line in b["lines"]]
    label = b.get("label", b["id"])
    sublabel = b.get("sublabel")
    return [label, sublabel] if sublabel else [label]


def route_all(spec, blocks_by_id, g):
    """Decide endpoints + lane offsets for every edge.

    Distributes endpoints along block sides so two arrows never meet at the
    same point on a block. Then groups edges into routing channels (the gutter
    between two columns or rows) and assigns each edge a lane offset within
    its channel, so parallel verticals/horizontals from different edges don't
    collapse onto the same line."""
    horiz_pair = {("right", "left"), ("left", "right")}
    vert_pair = {("top", "bottom"), ("bottom", "top")}

    side_counts = {}
    edge_sides = []
    for e in spec["edges"]:
        a = blocks_by_id[e["from"]]
        b = blocks_by_id[e["to"]]
        fs, ts = determine_sides(a, b)
        side_counts[(a["id"], fs)] = side_counts.get((a["id"], fs), 0) + 1
        side_counts[(b["id"], ts)] = side_counts.get((b["id"], ts), 0) + 1
        edge_sides.append((fs, ts))

    # Pass 1: assign endpoints (so we know each edge's actual y / x range).
    side_index = {}
    edge_endpoints = []
    for e, (fs, ts) in zip(spec["edges"], edge_sides):
        a = blocks_by_id[e["from"]]
        b = blocks_by_id[e["to"]]
        ia = side_index.get((a["id"], fs), 0)
        ib = side_index.get((b["id"], ts), 0)
        from_pt = side_endpoint(g, a, fs, ia, side_counts[(a["id"], fs)])
        to_pt = side_endpoint(g, b, ts, ib, side_counts[(b["id"], ts)])
        side_index[(a["id"], fs)] = ia + 1
        side_index[(b["id"], ts)] = ib + 1
        edge_endpoints.append((from_pt, to_pt))

    # Pass 2: group edges by routing channel and compute lane offsets via
    # interval-graph coloring. Two edges in the same channel only conflict if
    # their vertical (or horizontal) spans actually come close to each other -
    # edges at row 0 and row 4 in the same column gutter don't overlap and
    # should share a lane (offset 0).
    PADDING = 60                # edges within this many px count as overlapping
    LABEL_MARGIN = 40           # keep bends this far from block edges so labels
                                # never invade adjacent blocks
    h_channel = {}
    v_channel = {}
    for i, (e, (fs, ts)) in enumerate(zip(spec["edges"], edge_sides)):
        a = blocks_by_id[e["from"]]
        b = blocks_by_id[e["to"]]
        fx, fy = edge_endpoints[i][0]
        tx, ty = edge_endpoints[i][1]
        if (fs, ts) in horiz_pair:
            key = tuple(sorted((a["col"], b["col"])))
            h_channel.setdefault(key, []).append((i, min(fy, ty), max(fy, ty)))
        elif (fs, ts) in vert_pair:
            key = tuple(sorted((a["row"], b["row"])))
            v_channel.setdefault(key, []).append((i, min(fx, tx), max(fx, tx)))

    lane_offsets = {}

    def color_intervals(intervals):
        """Greedy interval-graph coloring with a PADDING gap. Returns
        (lane_of_idx, n_lanes_used)."""
        sorted_ivs = sorted(intervals, key=lambda x: x[1])
        lane_end = []
        lane_of = {}
        for idx, lo, hi in sorted_ivs:
            assigned = -1
            for li, end in enumerate(lane_end):
                if end + PADDING <= lo:
                    lane_end[li] = hi
                    assigned = li
                    break
            if assigned == -1:
                assigned = len(lane_end)
                lane_end.append(hi)
            lane_of[idx] = assigned
        return lane_of, len(lane_end)

    def assign_lanes(channel_dict, gutter):
        for _key, intervals in channel_dict.items():
            lane_of, n_lanes = color_intervals(intervals)
            if n_lanes <= 1:
                for idx in lane_of:
                    lane_offsets[idx] = 0
                continue
            max_offset = max(0.0, gutter / 2 - LABEL_MARGIN)
            spacing = (2 * max_offset) / (n_lanes - 1)
            for idx, lane in lane_of.items():
                lane_offsets[idx] = -max_offset + lane * spacing

    assign_lanes(h_channel, g["gutter_x"])
    assign_lanes(v_channel, g["gutter_y"])

    routed = []
    for i, (e, (fs, ts)) in enumerate(zip(spec["edges"], edge_sides)):
        from_pt, to_pt = edge_endpoints[i]
        routed.append((e, from_pt, to_pt, fs, ts, lane_offsets.get(i, 0)))
    return routed


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _relative_luminance(r, g, b):
    """WCAG relative luminance from 0..255 sRGB channels."""
    def chan(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _lum_of(fill_hex):
    h = (fill_hex or "#cccccc").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return _relative_luminance(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return 0.5


def text_color_for(*fill_hexes):
    """Pick black or white text given one or more fill colors. For CDC blocks
    rendered with a horizontal-split gradient, pass both halves; the text color
    chosen will be the one with the best *minimum* contrast across both halves
    (so a light-pastel gradient like Material 200 green / orange picks dark
    text instead of unreadable white). Theme-independent - contrast against the
    block's fill is what matters, not the canvas."""
    L_dark = _relative_luminance(0x1f, 0x29, 0x37)
    candidates = (("#ffffff", 1.0), ("#1f2937", L_dark))
    fill_lums = [_lum_of(c) for c in (fill_hexes or ("#cccccc",))]

    def min_contrast(text_lum):
        return min(
            (max(text_lum, fl) + 0.05) / (min(text_lum, fl) + 0.05)
            for fl in fill_lums
        )

    return max(candidates, key=lambda c: min_contrast(c[1]))[0]


def render(spec_path, out_path, theme_override=None):
    with open(spec_path) as f:
        spec = json.load(f)

    theme_name, theme = theme_of(spec, theme_override)
    kind_attrs = kind_attrs_for(theme)

    g = grid_of(spec)
    blocks = spec["blocks"]
    blocks_by_id = {b["id"]: b for b in blocks}
    legend_mode = legend_mode_of(spec)

    # Fill in domain fill / border from the palette when missing.
    domains = spec.setdefault("domains", {})
    palette_idx = 0
    for name, info in domains.items():
        fill, border = DEFAULT_DOMAIN_PALETTE[palette_idx % len(DEFAULT_DOMAIN_PALETTE)]
        if not info.get("color"):
            info["color"] = fill
            if not info.get("border"):
                # Dark theme uses the slate border; light theme keeps the
                # dark Material 900 paired with the fill.
                info["border"] = theme["border"] if theme_name == "dark" else border
            palette_idx += 1
        elif not info.get("border"):
            info["border"] = theme["border"]

    max_col = max(b["col"] for b in blocks)
    max_row = max(b["row"] for b in blocks)
    title_h = 30 if spec.get("title") else 0
    used_kinds = []
    seen = set()
    for e in spec.get("edges", []):
        k = e.get("kind", "generic")
        if k in kind_attrs and k not in seen:
            seen.add(k)
            used_kinds.append(k)

    # Legend card lives at the top-right of the canvas, so we reserve a column
    # of width LEGEND_W + 2 * LEGEND_PAD_OUTER on the right side of the grid.
    has_legend = legend_mode != "none" and (bool(domains) or bool(used_kinds))
    legend_h = 0
    if has_legend:
        if domains:
            legend_h += LEGEND_HEADER_H + len(domains) * LEGEND_ROW_H
        if used_kinds and legend_mode != "compact":
            if domains:
                legend_h += LEGEND_SECTION_GAP
            legend_h += LEGEND_HEADER_H + len(used_kinds) * LEGEND_ROW_H
        legend_h += LEGEND_PAD_INNER * 2

    grid_w = g["margin"] * 2 + (max_col + 1) * g["cell_w"] + max_col * g["gutter_x"]
    legend_area_w = (LEGEND_W + LEGEND_PAD_OUTER * 2) if has_legend else 0
    canvas_w = grid_w + legend_area_w
    grid_h = (g["margin"] * 2 + (max_row + 1) * g["cell_h"] + max_row * g["gutter_y"]
              + title_h)
    legend_min_h = (title_h + LEGEND_PAD_OUTER + legend_h + g["margin"]) if has_legend else 0
    canvas_h = max(grid_h, legend_min_h)
    content_y0 = title_h

    routed = route_all(spec, blocks_by_id, g)

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    # The `style="background:..."` attribute fills the SVG element's render
    # area even when the viewer letterboxes or pads around the viewBox; the
    # explicit <rect> below covers the viewBox proper.
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w}" '
               f'height="{canvas_h}" viewBox="0 0 {canvas_w} {canvas_h}" '
               f'font-family="{FONT_STACK}" '
               f'style="background:{theme["bg"]}">')

    out.append('  <defs>')
    # Per-kind arrowheads, color-matched to the line stroke so they read as one.
    for marker_id, color in [
        ("ah-solid",  theme["ink"]),
        ("ah-thin",   theme["ink_soft"]),
        ("ah-stream", theme["stream"]),
        ("ah-axil",   theme["axil"]),
        ("ah-cdc",    theme["cdc"]),
    ]:
        out.append(f'    <marker id="{marker_id}" markerWidth="9" '
                   f'markerHeight="9" refX="8" refY="3" orient="auto">'
                   f'<path d="M0,0 L0,6 L8,3 z" fill="{color}"/></marker>')
    out.append(f'    <marker id="ah-open" markerWidth="9" markerHeight="9" '
               f'refX="8" refY="3" orient="auto">'
               f'<path d="M0.5,0.5 L0.5,5.5 L8,3 z" fill="{theme["bg"]}" '
               f'stroke="{theme["ink"]}" stroke-width="1"/></marker>')
    # CDC linear gradients: half source-domain color, half destination-domain.
    for b in blocks:
        if not b.get("domain_b"):
            continue
        a = domains.get(b.get("domain", ""), {}).get("color", "#cccccc")
        c = domains.get(b["domain_b"], {}).get("color", "#cccccc")
        out.append(f'    <linearGradient id="grad-{esc(b["id"])}" '
                   f'x1="0%" y1="0%" x2="100%" y2="0%">'
                   f'<stop offset="50%" stop-color="{a}"/>'
                   f'<stop offset="50%" stop-color="{c}"/>'
                   f'</linearGradient>')
    out.append('  </defs>')

    out.append(f'  <rect width="{canvas_w}" height="{canvas_h}" '
               f'fill="{theme["bg"]}" id="_canvas_bg"/>')

    if spec.get("title"):
        # Title is centered on the GRID portion of the canvas, not the full
        # canvas - otherwise the legend column pulls the title visually off to
        # the right and away from the diagram it describes.
        out.append(f'  <text x="{grid_w/2:.1f}" y="24" text-anchor="middle" '
                   f'font-size="21" font-weight="600" fill="{theme["ink"]}" '
                   f'letter-spacing="0.2">{esc(spec["title"])}</text>')

    # Functional background bands. Unlike `lanes`, bands are not tied to a
    # clock domain; use them for architectural regions like "secure services".
    spec_bands = spec.get("bands") or {}
    if isinstance(spec_bands, dict) and spec_bands:
        for bid, info in spec_bands.items():
            rows = info.get("rows") or []
            cols = info.get("cols") or []
            if rows:
                rmin, rmax = min(rows), max(rows)
                x1 = LANE_SIDE_PAD
                x2 = grid_w - LANE_SIDE_PAD
                y1 = (g["margin"] + rmin * (g["cell_h"] + g["gutter_y"])
                      + content_y0 - LANE_PAD_TOP)
                y2 = (g["margin"] + rmax * (g["cell_h"] + g["gutter_y"])
                      + g["cell_h"] + content_y0 + LANE_PAD_BOT)
            elif cols:
                cmin, cmax = min(cols), max(cols)
                x1 = (g["margin"] + cmin * (g["cell_w"] + g["gutter_x"])
                      - LANE_PAD_TOP)
                x2 = (g["margin"] + cmax * (g["cell_w"] + g["gutter_x"])
                      + g["cell_w"] + LANE_PAD_BOT)
                y1 = content_y0 + LANE_SIDE_PAD
                y2 = canvas_h - LANE_SIDE_PAD
            else:
                continue
            fill = info.get("color", "#64748b")
            border = info.get("border", theme["ink_soft"])
            label = info.get("label") or bid
            out.append(
                f'  <rect id="band_{esc(bid)}" '
                f'x="{x1:.0f}" y="{y1:.0f}" '
                f'width="{x2 - x1:.0f}" height="{y2 - y1:.0f}" '
                f'fill="{fill}" fill-opacity="{BAND_FILL_OPACITY}" '
                f'stroke="{border}" stroke-width="1" '
                f'stroke-dasharray="{LANE_DASH}" rx="4"/>'
            )
            out.append(
                f'  <text x="{x1 + 14:.0f}" y="{y1 + 18:.0f}" '
                f'font-size="13" font-weight="700" '
                f'fill="{border}">{esc(label)}</text>'
            )

    # Clock-domain lanes (tinted bands). Opt-in via top-level
    # `lanes: {<domain>: {rows: [...]}}` for horizontal bands or `{cols: [...]}`
    # for vertical bands. Drawn UNDER everything else; blocks and groups
    # render on top.
    spec_lanes = spec.get("lanes") or {}
    if isinstance(spec_lanes, dict) and spec_lanes:
        for dname, info in spec_lanes.items():
            rows = info.get("rows") or []
            cols = info.get("cols") or []
            if rows:
                rmin, rmax = min(rows), max(rows)
                x1 = LANE_SIDE_PAD
                x2 = canvas_w - LANE_SIDE_PAD
                y1 = (g["margin"] + rmin * (g["cell_h"] + g["gutter_y"])
                      + content_y0 - LANE_PAD_TOP)
                y2 = (g["margin"] + rmax * (g["cell_h"] + g["gutter_y"])
                      + g["cell_h"] + content_y0 + LANE_PAD_BOT)
            elif cols:
                cmin, cmax = min(cols), max(cols)
                # Vertical band: header goes at the top of the column where
                # the lane begins, reading horizontally. Side padding mirrors
                # the row case (LANE_PAD_TOP becomes left padding).
                x1 = (g["margin"] + cmin * (g["cell_w"] + g["gutter_x"])
                      - LANE_PAD_TOP)
                x2 = (g["margin"] + cmax * (g["cell_w"] + g["gutter_x"])
                      + g["cell_w"] + LANE_PAD_BOT)
                y1 = content_y0 + LANE_SIDE_PAD
                y2 = canvas_h - LANE_SIDE_PAD
            else:
                continue
            dinfo = domains.get(dname, {})
            fill = dinfo.get("color", "#cccccc")
            border = dinfo.get("border", theme["border"])
            out.append(
                f'  <rect id="lane_{esc(dname)}" '
                f'x="{x1:.0f}" y="{y1:.0f}" '
                f'width="{x2 - x1:.0f}" height="{y2 - y1:.0f}" '
                f'fill="{fill}" fill-opacity="{LANE_FILL_OPACITY}" '
                f'stroke="{border}" stroke-width="1" '
                f'stroke-dasharray="{LANE_DASH}" rx="4"/>'
            )
            freq = dinfo.get("freq_mhz")
            hdr = (f'{dname} domain  {freq} MHz'
                   if freq is not None else f'{dname} domain')
            out.append(
                f'  <text x="{x1 + 14:.0f}" y="{y1 + 18:.0f}" '
                f'font-size="13" font-weight="700" '
                f'fill="{border}">{esc(hdr)}</text>'
            )

    # Group containers (hierarchy depth > 1). Drawn BEFORE blocks so the dashed
    # outline tucks behind member blocks and only shows through the gutters.
    # IDs prefixed `group_` so the geometry validator can skip them - they're
    # not real blocks and arrows are allowed to cross their borders.
    groups = spec.get("groups", {})
    if isinstance(groups, dict) and groups:
        members_by_group = {gid: [] for gid in groups}
        for b in blocks:
            gid = b.get("group")
            if gid in members_by_group:
                members_by_group[gid].append(b)
        for gid, members in members_by_group.items():
            if not members:
                continue
            xs1, ys1, xs2, ys2 = [], [], [], []
            for b in members:
                bx, by, bw, bh = block_rect(g, b)
                xs1.append(bx)
                ys1.append(by + content_y0)
                xs2.append(bx + bw)
                ys2.append(by + content_y0 + bh)
            gx1 = min(xs1) - GROUP_PAD_X
            gy1 = min(ys1) - GROUP_PAD_TOP
            gx2 = max(xs2) + GROUP_PAD_X
            gy2 = max(ys2) + GROUP_PAD_BOT
            out.append(
                f'  <rect id="group_{esc(gid)}" x="{gx1:.0f}" y="{gy1:.0f}" '
                f'width="{gx2 - gx1:.0f}" height="{gy2 - gy1:.0f}" fill="none" '
                f'stroke="{theme["ink_soft"]}" stroke-width="1.2" '
                f'stroke-dasharray="{GROUP_DASH}" rx="10"/>'
            )
            glabel = groups[gid].get("label") or gid
            out.append(
                f'  <text x="{gx1 + 14:.0f}" y="{gy1 + 18:.0f}" font-size="14" '
                f'font-weight="700" letter-spacing="1.2" '
                f'fill="{theme["ink_soft"]}">{esc(glabel.upper())}</text>'
            )

    for b in blocks:
        x, y, w, h = block_rect(g, b)
        y += content_y0
        if b.get("external"):
            fill = theme["ext_fill"]
            border_color = theme["ext_border"]
            text_fill = theme["ext_text"]
        elif b.get("domain_b"):
            fill = f"url(#grad-{b['id']})"
            border_color = theme["border"]
            color_a = domains.get(b.get("domain", ""), {}).get("color", "#cccccc")
            color_b = domains.get(b["domain_b"], {}).get("color", "#cccccc")
            text_fill = text_color_for(color_a, color_b)
        else:
            d = domains.get(b.get("domain", ""), {})
            fill = d.get("color", "#cccccc")
            border_color = d.get("border", theme["border"])
            text_fill = text_color_for(fill)
        out.append(f'  <rect id="{esc(b["id"])}" x="{x:.0f}" y="{y:.0f}" '
                   f'width="{w}" height="{h}" fill="{fill}" '
                   f'stroke="{border_color}" stroke-width="1.4" '
                   f'rx="{BLOCK_RX}"/>')
        cx = x + w / 2
        explicit_lines = b.get("lines")
        if explicit_lines:
            lines = block_label_lines(b)
            font = 17 if len(lines) == 2 else 14
            step = 17 if len(lines) == 2 else 15
            total_h = step * (len(lines) - 1)
            start_y = y + h / 2 - total_h / 2 + 5
            for i, line in enumerate(lines):
                weight = "600" if i == 0 else "500"
                out.append(f'  <text x="{cx:.0f}" y="{start_y + i * step:.0f}" '
                           f'text-anchor="middle" font-size="{font}" '
                           f'font-weight="{weight}" fill="{text_fill}">'
                           f'{esc(line)}</text>')
        else:
            label = b.get("label", b["id"])
            sublabel = b.get("sublabel")
            if sublabel:
                cy_main = y + h / 2 - 2
                cy_sub = y + h / 2 + 14
                out.append(f'  <text x="{cx:.0f}" y="{cy_main:.0f}" '
                           f'text-anchor="middle" font-size="18" font-weight="600" '
                           f'fill="{text_fill}">{esc(label)}</text>')
                out.append(f'  <text x="{cx:.0f}" y="{cy_sub:.0f}" '
                           f'text-anchor="middle" font-size="15" '
                           f'font-style="italic" fill="{text_fill}" '
                           f'opacity="0.85">{esc(sublabel)}</text>')
            else:
                lines = [label]
                cy = y + h / 2 + 5
                out.append(f'  <text x="{cx:.0f}" y="{cy:.0f}" '
                           f'text-anchor="middle" font-size="18" font-weight="600" '
                           f'fill="{text_fill}">{esc(lines[0])}</text>')

    for e, from_pt, to_pt, fs, ts, lane_offset in routed:
        kind = e.get("kind", "generic")
        attrs = kind_attrs.get(kind, kind_attrs["generic"])
        route_cfg = e.get("route") or {}
        explicit_points = route_cfg.get("points")
        route_mode = route_cfg.get("mode", "auto")
        if explicit_points:
            # Explicit waypoints are taken as final SVG coordinates: the user
            # copied them off a previous render, so the title-bar offset is
            # already baked in. Do NOT re-apply content_y0 here.
            pts = [(float(p[0]), float(p[1])) for p in explicit_points]
        else:
            # `direct` = Manhattan with no lane offset: collinear endpoints
            # produce a single horizontal/vertical segment, non-collinear ones
            # an L. Never a diagonal - that's the whole point.
            eff_lane = 0 if route_mode == "direct" else lane_offset
            pts = manhattan(g, from_pt, to_pt, fs, ts, eff_lane)
            pts = [(p[0], p[1] + content_y0) for p in pts]
        d = path_d(pts)
        eid = f"edge_{e['from']}_to_{e['to']}"
        dash_attr = f' stroke-dasharray="{attrs["dash"]}"' if attrs.get("dash") else ""
        out.append(f'  <path id="{esc(eid)}" d="{d}" '
                   f'stroke="{attrs["stroke"]}" stroke-width="{attrs["stroke_width"]}" '
                   f'fill="none" marker-end="url(#{attrs["marker"]})"{dash_attr}/>')

        label = edge_label(e)
        if not label:
            continue
        mx, my, orient = label_anchor(pts, e.get("label"))
        font = 15
        text_w = max(len(label) * font * 0.55, font * 0.6)
        text_h = font
        if orient == "h":
            ly = my - 4
        else:
            ly = my + 3
        lx = mx
        # Auto-clamp the label into the endpoints' horizontal span so it stays
        # over the wire. Skip when the user has supplied an explicit override
        # (dx/dy/segment/t) - they're deliberately placing it themselves.
        if not e.get("label"):
            clear_x = text_w / 2 + 6
            low_x = min(from_pt[0], to_pt[0])
            high_x = max(from_pt[0], to_pt[0])
            if high_x - low_x > clear_x * 2:
                lx = min(max(lx, low_x + clear_x), high_x - clear_x)
        anchor = "middle"
        box_x = lx - text_w / 2 - 3
        box_y = ly - text_h * 0.85 - 2
        out.append(f'  <rect x="{box_x:.1f}" y="{box_y:.1f}" '
                   f'width="{text_w + 6:.1f}" height="{text_h + 4:.1f}" '
                   f'fill="{theme["label_bg"]}" stroke="{theme["label_bord"]}" '
                   f'stroke-width="0.6" rx="3"/>')
        out.append(f'  <text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
                   f'font-size="{font}" fill="{theme["ink"]}">{esc(label)}</text>')

    if has_legend:
        lx = canvas_w - LEGEND_W - LEGEND_PAD_OUTER
        ly = title_h + LEGEND_PAD_OUTER
        out.append(
            f'  <rect id="legend_card" x="{lx}" y="{ly}" '
            f'width="{LEGEND_W}" height="{legend_h}" '
            f'fill="{theme["legend_bg"]}" stroke="{theme["legend_bord"]}" '
            f'stroke-width="1" rx="6"/>'
        )
        cur_y = ly + LEGEND_PAD_INNER
        if domains:
            out.append(
                f'  <text x="{lx + LEGEND_W / 2:.0f}" '
                f'y="{cur_y + 14:.0f}" text-anchor="middle" font-size="13" '
                f'font-weight="700" fill="{theme["ink"]}" '
                f'letter-spacing="0.4">Clock domains</text>'
            )
            cur_y += LEGEND_HEADER_H
            for name, info in domains.items():
                sw_x = lx + LEGEND_PAD_INNER
                sw_y = cur_y + (LEGEND_ROW_H - LEGEND_SWATCH_H) / 2
                out.append(
                    f'  <rect x="{sw_x}" y="{sw_y:.0f}" '
                    f'width="{LEGEND_SWATCH_W}" height="{LEGEND_SWATCH_H}" '
                    f'fill="{info.get("color", "#cccccc")}" '
                    f'stroke="{info.get("border", theme["border"])}" '
                    f'stroke-width="1.2" rx="2"/>'
                )
                freq = info.get("freq_mhz")
                txt = f'{name}  {freq} MHz' if freq is not None else name
                out.append(
                    f'  <text x="{sw_x + LEGEND_SWATCH_W + 10}" '
                    f'y="{cur_y + LEGEND_ROW_H / 2 + 5:.0f}" font-size="13" '
                    f'fill="{theme["ink"]}">{esc(txt)}</text>'
                )
                cur_y += LEGEND_ROW_H
        if used_kinds and legend_mode != "compact":
            if domains:
                cur_y += LEGEND_SECTION_GAP
            out.append(
                f'  <text x="{lx + LEGEND_W / 2:.0f}" '
                f'y="{cur_y + 14:.0f}" text-anchor="middle" font-size="13" '
                f'font-weight="700" fill="{theme["ink"]}" '
                f'letter-spacing="0.4">Connection styles</text>'
            )
            cur_y += LEGEND_HEADER_H
            descriptions = {
                "axi-mm":     "AXI-MM data bus",
                "axi-lite":   "AXI4-Lite control",
                "axi-stream": "AXI-Stream",
                "cdc":        "CDC traversal (re-clocked)",
                "generic":    "Generic / discrete",
            }
            for k in used_kinds:
                attrs = kind_attrs[k]
                dash = (f' stroke-dasharray="{attrs["dash"]}"'
                        if attrs.get("dash") else "")
                arrow_x = lx + LEGEND_PAD_INNER
                arrow_y = cur_y + LEGEND_ROW_H / 2
                out.append(
                    f'  <path id="legend-arrow-{esc(k)}" '
                    f'd="M {arrow_x},{arrow_y:.1f} '
                    f'L {arrow_x + LEGEND_ARROW_W},{arrow_y:.1f}" '
                    f'stroke="{attrs["stroke"]}" '
                    f'stroke-width="{attrs["stroke_width"]}" fill="none" '
                    f'marker-end="url(#{attrs["marker"]})"{dash}/>'
                )
                out.append(
                    f'  <text x="{arrow_x + LEGEND_ARROW_W + 10}" '
                    f'y="{cur_y + LEGEND_ROW_H / 2 + 5:.0f}" font-size="13" '
                    f'fill="{theme["ink"]}">'
                    f'{esc(descriptions.get(k, k))}</text>'
                )
                cur_y += LEGEND_ROW_H

    out.append('</svg>')

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"Wrote {out_path}: {len(blocks)} blocks, {len(spec['edges'])} edges, "
          f"{len(domains)} domains, theme={theme_name}")


def main():
    args = list(sys.argv[1:])
    theme_override = None
    if "--theme" in args:
        i = args.index("--theme")
        if i + 1 >= len(args):
            print("--theme requires a value (light or dark)", file=sys.stderr)
            sys.exit(2)
        theme_override = args[i + 1]
        del args[i:i + 2]
    if len(args) < 2:
        print("Usage: render.py [--theme light|dark] <spec.json> <output.svg>",
              file=sys.stderr)
        sys.exit(2)
    render(args[0], args[1], theme_override)


if __name__ == "__main__":
    main()
