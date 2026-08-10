#!/usr/bin/env python3
"""
Validate the JSON architecture spec before invoking the renderer. Catches
structural problems that would otherwise produce a broken or visually wrong
SVG: missing block ids in edges, duplicate cells, unknown domains, wrong
field types, malformed colors, etc.

Author: Leonardo Capossio - bard0 design - hello@bard0.com
Year:   2026

Usage:
    python validate_spec.py <spec.json>

Exit code: 0 on PASS (possibly with warnings), 1 on FAIL (with numbered
reasons), 2 on usage error.
"""

import json
import re
import sys
from pathlib import Path

VALID_KINDS = {"axi-mm", "axi-lite", "axi-stream", "tilelink", "cdc", "generic"}
WIDTH_RE = re.compile(r"^(\d+b?|[A-Za-z0-9_/+\- ][A-Za-z0-9_/+\- ]{0,23})$")
VALID_THEMES = {"light", "dark"}
VALID_ROUTE_MODES = {"auto", "direct", "orthogonal"}
VALID_LEGENDS = {"right", "compact", "none"}
VALID_SIDES = {"left", "right", "top", "bottom"}
GRID_FIELDS = {"cell_w", "cell_h", "gutter_x", "gutter_y", "margin"}
DOMAIN_FIELDS = {"freq_mhz", "color", "border"}
GROUP_FIELDS = {"label"}
LANE_FIELDS = {"rows", "cols"}
BAND_FIELDS = {"label", "rows", "cols", "color", "border"}
BLOCK_FIELDS = {
    "id", "label", "sublabel", "domain", "domain_b", "external", "row", "col",
    "group", "w", "h", "side", "lines",
}
EDGE_FIELDS = {"from", "to", "kind", "width", "route", "label"}
ROUTE_FIELDS = {"mode", "points"}
LABEL_FIELDS = {"dx", "dy", "segment", "t"}
TOP_FIELDS = {
    "title", "top", "theme", "legend", "grid", "domains",
    "groups", "lanes", "bands", "blocks", "edges",
}

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _is_int(v) -> bool:
    """True for genuine ints. Rejects bool (Python's bool is int)."""
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v) -> bool:
    return _is_int(v) or isinstance(v, float)


def _is_grid_coord(v) -> bool:
    """True for non-bool ints/floats on the 0.25 placement grid."""
    if not _is_number(v) or isinstance(v, bool):
        return False
    return abs(float(v) * 4 - round(float(v) * 4)) < 1e-9


def _is_color(v) -> bool:
    return isinstance(v, str) and bool(_HEX_COLOR.match(v))


def _check_unknown_keys(d, known, prefix, errors):
    for k in d:
        if k not in known:
            errors.append(f"{prefix}: unknown field '{k}'")


def validate(spec):
    errors = []

    if not isinstance(spec, dict):
        return ["spec: top level must be an object"]

    _check_unknown_keys(spec, TOP_FIELDS, "spec", errors)

    # title / top
    for f in ("title", "top"):
        if f in spec and not isinstance(spec[f], str):
            errors.append(f"{f}: must be a string")

    # theme
    theme = spec.get("theme")
    if theme is not None and theme not in VALID_THEMES:
        errors.append(f"theme: '{theme}' is not one of {sorted(VALID_THEMES)}")

    # legend
    legend = spec.get("legend")
    if legend is not None:
        if isinstance(legend, bool):
            pass
        elif not isinstance(legend, str) or legend not in VALID_LEGENDS:
            errors.append(
                f"legend: must be a boolean or one of {sorted(VALID_LEGENDS)} "
                f"(got {legend!r})"
            )

    # grid
    grid = spec.get("grid")
    if grid is not None:
        if not isinstance(grid, dict):
            errors.append("grid: must be an object")
        else:
            _check_unknown_keys(grid, GRID_FIELDS, "grid", errors)
            for k, v in grid.items():
                if k not in GRID_FIELDS:
                    continue
                # Pixel-like dimensions: positive ints. Reject floats (NaN/inf
                # would produce broken SVG) and bools (subclass of int).
                if not _is_int(v):
                    errors.append(
                        f"grid.{k}: must be a non-negative int "
                        f"(got {type(v).__name__} = {v!r})"
                    )
                elif v < 0:
                    errors.append(f"grid.{k}: must be non-negative")

    # domains
    domains = spec.get("domains", {})
    if not isinstance(domains, dict):
        errors.append("domains: must be an object (key -> {color, freq_mhz, ...})")
        domains = {}
    else:
        for name, info in domains.items():
            if not isinstance(name, str) or not name:
                errors.append(f"domains: key '{name!r}' must be a non-empty string")
                continue
            if not isinstance(info, dict):
                errors.append(f"domain '{name}': must be an object")
                continue
            _check_unknown_keys(info, DOMAIN_FIELDS, f"domain '{name}'", errors)
            if "freq_mhz" in info and not _is_number(info["freq_mhz"]):
                errors.append(f"domain '{name}': freq_mhz must be a number")
            if "color" in info and not _is_color(info["color"]):
                errors.append(
                    f"domain '{name}': color must be a hex color string "
                    f"like '#42A5F5' (got {info['color']!r})"
                )
            if "border" in info and not _is_color(info["border"]):
                errors.append(
                    f"domain '{name}': border must be a hex color string "
                    f"(got {info['border']!r})"
                )

    # groups (optional). Hierarchical containers drawn as dashed rectangles
    # around member blocks at depth > 1.
    groups = spec.get("groups")
    if groups is not None and not isinstance(groups, dict):
        errors.append("groups: must be an object (key -> {label})")
        groups = {}
    elif isinstance(groups, dict):
        for name, info in groups.items():
            if not isinstance(name, str) or not name:
                errors.append(f"groups: key '{name!r}' must be a non-empty string")
                continue
            if not isinstance(info, dict):
                errors.append(f"group '{name}': must be an object")
                continue
            _check_unknown_keys(info, GROUP_FIELDS, f"group '{name}'", errors)
            if "label" in info and not isinstance(info["label"], str):
                errors.append(f"group '{name}': label must be a string")
    else:
        groups = {}

    # bands (optional). Functional background bands independent of domains.
    bands = spec.get("bands")
    if bands is not None and not isinstance(bands, dict):
        errors.append("bands: must be an object (band key -> {label, rows/cols})")
    elif isinstance(bands, dict):
        for name, info in bands.items():
            prefix_b = f"band '{name}'"
            if not isinstance(name, str) or not name:
                errors.append(f"bands: key '{name!r}' must be a non-empty string")
                continue
            if not isinstance(info, dict):
                errors.append(f"{prefix_b}: must be an object")
                continue
            _check_unknown_keys(info, BAND_FIELDS, prefix_b, errors)
            if (
                "label" in info
                and info["label"] is not None
                and not isinstance(info["label"], str)
            ):
                errors.append(f"{prefix_b}: label must be a string or null")
            for f in ("color", "border"):
                if f in info and not _is_color(info[f]):
                    errors.append(
                        f"{prefix_b}: {f} must be a hex color string "
                        f"(got {info[f]!r})"
                    )
            rows = info.get("rows")
            cols = info.get("cols")
            if (rows is None) == (cols is None):
                errors.append(
                    f"{prefix_b}: must specify exactly one of 'rows' or 'cols'"
                )
            for axis, vals in (("rows", rows), ("cols", cols)):
                if vals is None:
                    continue
                if not isinstance(vals, list) or not vals:
                    errors.append(
                        f"{prefix_b}: '{axis}' must be a non-empty list of "
                        f"non-negative numbers in 0.25 steps"
                    )
                else:
                    for v in vals:
                        if not _is_grid_coord(v) or v < 0:
                            errors.append(
                                f"{prefix_b}: '{axis}' entries must be "
                                f"non-negative numbers in 0.25 steps "
                                f"(got {v!r}; booleans not accepted)"
                            )

    # lanes (optional). Full-width tinted backgrounds per clock domain. Each
    # entry maps a declared domain key to the rows that domain's blocks occupy.
    lanes = spec.get("lanes")
    if lanes is not None and not isinstance(lanes, dict):
        errors.append("lanes: must be an object (domain key -> {rows: [...]})")
        lanes = {}
    elif isinstance(lanes, dict):
        for dname, info in lanes.items():
            prefix_l = f"lane '{dname}'"
            if not isinstance(dname, str) or not dname:
                errors.append(f"lanes: key '{dname!r}' must be a non-empty string")
                continue
            if not isinstance(info, dict):
                errors.append(f"{prefix_l}: must be an object")
                continue
            _check_unknown_keys(info, LANE_FIELDS, prefix_l, errors)
            if isinstance(domains, dict) and dname not in domains:
                errors.append(
                    f"{prefix_l}: domain '{dname}' is not declared in 'domains'"
                )
            rows = info.get("rows")
            cols = info.get("cols")
            if (rows is None) == (cols is None):
                errors.append(
                    f"{prefix_l}: must specify exactly one of 'rows' or 'cols' "
                    f"(rows -> horizontal band, cols -> vertical band)"
                )
            for axis, vals in (("rows", rows), ("cols", cols)):
                if vals is None:
                    continue
                if not isinstance(vals, list) or not vals:
                    errors.append(
                        f"{prefix_l}: '{axis}' must be a non-empty list of "
                        f"non-negative numbers in 0.25 steps"
                    )
                else:
                    for v in vals:
                        if not _is_grid_coord(v) or v < 0:
                            errors.append(
                                f"{prefix_l}: '{axis}' entries must be "
                                f"non-negative numbers in 0.25 steps "
                                f"(got {v!r}; booleans not accepted)"
                            )

    # blocks
    blocks = spec.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        errors.append("blocks: must be a non-empty list")
        return errors

    block_ids = set()
    cells = {}
    any_domain_block = False
    for i, b in enumerate(blocks):
        prefix = f"blocks[{i}]"
        if not isinstance(b, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        _check_unknown_keys(b, BLOCK_FIELDS, prefix, errors)
        bid = b.get("id")
        if not isinstance(bid, str) or not bid:
            errors.append(f"{prefix}: 'id' must be a non-empty string")
            continue
        if bid in block_ids:
            errors.append(f"{prefix}: duplicate id '{bid}'")
        block_ids.add(bid)
        prefix = f"block '{bid}'"

        for f in ("label", "sublabel"):
            if f in b and not isinstance(b[f], str):
                errors.append(f"{prefix}: '{f}' must be a string")

        lines = b.get("lines")
        if lines is not None:
            if not isinstance(lines, list) or not lines:
                errors.append(f"{prefix}: 'lines' must be a non-empty list of strings")
            else:
                for j, line in enumerate(lines):
                    if not isinstance(line, str):
                        errors.append(
                            f"{prefix}: lines[{j}] must be a string "
                            f"(got {type(line).__name__})"
                        )

        side = b.get("side")
        if side is not None and side not in VALID_SIDES:
            errors.append(f"{prefix}: side '{side}' is not one of {sorted(VALID_SIDES)}")
        if side is not None and not b.get("external", False):
            errors.append(f"{prefix}: side is only valid when external=true")

        for f in ("w", "h"):
            if f in b:
                v = b[f]
                if not _is_int(v):
                    errors.append(
                        f"{prefix}: '{f}' must be a positive int "
                        f"(got {type(v).__name__} = {v!r})"
                    )
                elif v <= 0:
                    errors.append(f"{prefix}: '{f}' must be positive")

        row = b.get("row")
        col = b.get("col")
        if not _is_grid_coord(row) or row < 0:
            errors.append(
                f"{prefix}: 'row' must be a non-negative number in 0.25 steps "
                f"(got {row!r}; booleans not accepted)"
            )
        if not _is_grid_coord(col) or col < 0:
            errors.append(
                f"{prefix}: 'col' must be a non-negative number in 0.25 steps "
                f"(got {col!r}; booleans not accepted)"
            )
        if _is_grid_coord(row) and _is_grid_coord(col):
            cell = (row, col)
            if cell in cells:
                errors.append(
                    f"{prefix}: cell ({row},{col}) is also occupied by "
                    f"'{cells[cell]}'"
                )
            else:
                cells[cell] = bid

        group_ref = b.get("group")
        if group_ref is not None:
            if not isinstance(group_ref, str):
                errors.append(f"{prefix}: 'group' must be a string")
            elif not groups or group_ref not in groups:
                errors.append(
                    f"{prefix}: group '{group_ref}' is not declared in 'groups'"
                )

        ext_raw = b.get("external")
        external = False
        if ext_raw is not None:
            if not isinstance(ext_raw, bool):
                errors.append(
                    f"{prefix}: 'external' must be a JSON boolean "
                    f"(got {type(ext_raw).__name__} = {ext_raw!r})"
                )
            external = bool(ext_raw)

        domain = b.get("domain")
        domain_b = b.get("domain_b")

        if external:
            # A clocked off-chip block may share a rendered domain with an
            # on-chip interface. `external` remains a placement/style signal;
            # the dashed border distinguishes it from internal blocks.
            if domain is not None:
                any_domain_block = True
                if not isinstance(domain, str):
                    errors.append(f"{prefix}: 'domain' must be a string")
                elif domain not in domains:
                    errors.append(
                        f"{prefix}: domain '{domain}' is not declared in 'domains'"
                    )
            if domain_b is not None:
                errors.append(
                    f"{prefix}: 'external': true blocks cannot set 'domain_b' "
                    f"(got '{domain_b}'). CDC concept doesn't apply to off-chip blocks."
                )
        else:
            any_domain_block = True
            if domain is None:
                errors.append(f"{prefix}: must set 'domain' or 'external': true")
            elif not isinstance(domain, str):
                errors.append(f"{prefix}: 'domain' must be a string")
            elif domain not in domains:
                errors.append(
                    f"{prefix}: domain '{domain}' is not declared in 'domains'"
                )
            if domain_b is not None:
                if not isinstance(domain_b, str):
                    errors.append(f"{prefix}: 'domain_b' must be a string")
                elif domain_b not in domains:
                    errors.append(
                        f"{prefix}: domain_b '{domain_b}' is not declared in 'domains'"
                    )
                elif domain_b == domain:
                    errors.append(
                        f"{prefix}: 'domain_b' must differ from 'domain' "
                        f"(both = '{domain}')"
                    )

    if any_domain_block and not domains:
        errors.append(
            "domains: required when any block declares or requires a domain "
            "(cannot color blocks without declared domains)"
        )

    # edges
    edges = spec.get("edges")
    if not isinstance(edges, list):
        errors.append("edges: must be a list (may be empty)")
        return errors

    edge_pairs = {}
    for i, e in enumerate(edges):
        prefix = f"edges[{i}]"
        if not isinstance(e, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        _check_unknown_keys(e, EDGE_FIELDS, prefix, errors)
        for endpoint in ("from", "to"):
            v = e.get(endpoint)
            if not isinstance(v, str) or not v:
                errors.append(f"{prefix}: '{endpoint}' must be a non-empty string")
            elif v not in block_ids:
                errors.append(
                    f"{prefix}: '{endpoint}' = '{v}' does not match any block id"
                )
        if (
            isinstance(e.get("from"), str)
            and e.get("from") == e.get("to")
        ):
            errors.append(
                f"{prefix}: self-edge ('{e['from']}' to itself) not supported"
            )
        pair = (e.get("from"), e.get("to"))
        if pair[0] in block_ids and pair[1] in block_ids:
            if pair in edge_pairs:
                errors.append(
                    f"{prefix}: duplicate edge from '{pair[0]}' to '{pair[1]}' "
                    f"would create a duplicate SVG id; first seen at "
                    f"edges[{edge_pairs[pair]}]"
                )
            else:
                edge_pairs[pair] = i
        kind = e.get("kind", "generic")
        if not isinstance(kind, str) or kind not in VALID_KINDS:
            errors.append(
                f"{prefix}: kind '{kind}' is not one of {sorted(VALID_KINDS)}"
            )
        width = e.get("width")
        if width is not None and not (_is_int(width) or isinstance(width, str)):
            errors.append(
                f"{prefix}: 'width' must be int or string "
                f"(got {type(width).__name__} = {width!r})"
            )
        elif isinstance(width, str) and not WIDTH_RE.match(width):
            errors.append(
                f"{prefix}: width string must match "
                r"^\d+b?$|^[\w/+\- ]{1,24}$ "
                f"(got {width!r})"
            )

        route = e.get("route")
        if route is not None:
            if not isinstance(route, dict):
                errors.append(f"{prefix}: 'route' must be an object")
            else:
                _check_unknown_keys(route, ROUTE_FIELDS, f"{prefix}.route", errors)
                mode = route.get("mode")
                if mode is not None and mode not in VALID_ROUTE_MODES:
                    errors.append(
                        f"{prefix}.route: mode '{mode}' is not one of "
                        f"{sorted(VALID_ROUTE_MODES)}"
                    )
                pts = route.get("points")
                if pts is not None:
                    if not isinstance(pts, list) or len(pts) < 2:
                        errors.append(
                            f"{prefix}.route.points: must be a list of at least 2 "
                            f"[x, y] pairs (got {pts!r})"
                        )
                    else:
                        well_formed = True
                        for j, p in enumerate(pts):
                            if not (
                                isinstance(p, list)
                                and len(p) == 2
                                and _is_number(p[0])
                                and _is_number(p[1])
                            ):
                                errors.append(
                                    f"{prefix}.route.points[{j}]: must be "
                                    f"[x, y] with numeric coords (got {p!r})"
                                )
                                well_formed = False
                        # Every consecutive pair must share x or y. Diagonal
                        # wires are banned outright - the whole layout language
                        # is orthogonal.
                        if well_formed:
                            for j in range(len(pts) - 1):
                                x1, y1 = pts[j]
                                x2, y2 = pts[j + 1]
                                if (
                                    abs(float(x1) - float(x2)) > 1e-6
                                    and abs(float(y1) - float(y2)) > 1e-6
                                ):
                                    errors.append(
                                        f"{prefix}.route.points[{j}..{j+1}]: "
                                        f"diagonal segment from ({x1},{y1}) to "
                                        f"({x2},{y2}). Consecutive points must "
                                        f"share x or y; insert an intermediate "
                                        f"orthogonal waypoint."
                                    )

        label = e.get("label")
        if label is not None:
            if not isinstance(label, dict):
                errors.append(f"{prefix}: 'label' must be an object")
            else:
                _check_unknown_keys(label, LABEL_FIELDS, f"{prefix}.label", errors)
                for f in ("dx", "dy"):
                    if f in label and not _is_number(label[f]):
                        errors.append(
                            f"{prefix}.label.{f}: must be a number "
                            f"(got {type(label[f]).__name__} = {label[f]!r})"
                        )
                if "segment" in label and not _is_int(label["segment"]):
                    errors.append(
                        f"{prefix}.label.segment: must be an int "
                        f"(got {type(label['segment']).__name__} = {label['segment']!r})"
                    )
                if "t" in label:
                    tv = label["t"]
                    if not _is_number(tv) or not (0.0 <= float(tv) <= 1.0):
                        errors.append(
                            f"{prefix}.label.t: must be a number in [0, 1] "
                            f"(got {tv!r})"
                        )

    return errors


def lint(spec):
    """Return non-fatal authoring hints for a structurally valid spec."""
    warnings = []
    if not isinstance(spec, dict):
        return warnings

    domains = spec.get("domains")
    blocks = spec.get("blocks")
    edges = spec.get("edges")
    if not isinstance(domains, dict) or not isinstance(blocks, list):
        return warnings

    # Every declared domain appears in the legend, but only domains used by a
    # block or lane paint diagram pixels. Domain-colored external blocks count:
    # they now deliberately retain their off-chip identity via a dashed border.
    legend = spec.get("legend")
    legend_visible = legend is not False and legend != "none"
    if legend_visible:
        used_domains = set()
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for field in ("domain", "domain_b"):
                value = block.get(field)
                if isinstance(value, str):
                    used_domains.add(value)
        lanes = spec.get("lanes")
        if isinstance(lanes, dict):
            used_domains.update(name for name in lanes if isinstance(name, str))
        for name in domains:
            if name not in used_domains:
                warnings.append(
                    f'LEGEND_UNUSED: domain "{name}" is declared but no rendered '
                    "block or lane uses it. Remove it from 'domains', or attach "
                    "it to a block or lane that runs on this clock."
                )

    # The default CDC gradient is domain=left and domain_b=right. Look only at
    # connected neighbors with a clear horizontal relationship; vertical
    # layouts do not provide enough evidence for a useful orientation hint.
    if not isinstance(edges, list):
        return warnings
    blocks_by_id = {
        block.get("id"): block
        for block in blocks
        if isinstance(block, dict) and isinstance(block.get("id"), str)
    }
    neighbors = {bid: [] for bid in blocks_by_id}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        src = edge.get("from")
        dst = edge.get("to")
        if src in neighbors and dst in blocks_by_id:
            neighbors[src].append(blocks_by_id[dst])
        if dst in neighbors and src in blocks_by_id:
            neighbors[dst].append(blocks_by_id[src])

    for block in blocks:
        if not isinstance(block, dict):
            continue
        domain = block.get("domain")
        domain_b = block.get("domain_b")
        bid = block.get("id")
        col = block.get("col")
        if not all(isinstance(value, str) for value in (bid, domain, domain_b)):
            continue
        if not _is_number(col):
            continue
        for neighbor in neighbors.get(bid, []):
            neighbor_col = neighbor.get("col")
            neighbor_domain = neighbor.get("domain")
            neighbor_id = neighbor.get("id")
            if not _is_number(neighbor_col) or not isinstance(neighbor_id, str):
                continue
            side = None
            expected = None
            rendered_half = None
            if neighbor_col < col and neighbor_domain == domain_b:
                side, expected, rendered_half = "left", domain_b, domain
            elif neighbor_col > col and neighbor_domain == domain:
                side, expected, rendered_half = "right", domain, domain_b
            if side:
                warnings.append(
                    f'CDC_ORIENTATION: block "{bid}" splits {domain}|{domain_b}, '
                    f'but its {expected} neighbor "{neighbor_id}" sits on the '
                    f'{side}, where the {rendered_half} half renders. Consider '
                    "swapping 'domain' and 'domain_b'."
                )

    return warnings


def main():
    if len(sys.argv) != 2:
        print("Usage: validate_spec.py <spec.json>", file=sys.stderr)
        return 2
    spec_path = Path(sys.argv[1])
    if not spec_path.is_file():
        print(f"ERROR: not a file: {spec_path}", file=sys.stderr)
        return 2
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {spec_path}: {e}", file=sys.stderr)
        return 1
    errors = validate(spec)
    if errors:
        print(f"FAIL: {len(errors)} issue(s) in {spec_path}")
        for n, err in enumerate(errors, 1):
            print(f"  {n}. {err}")
        return 1
    warnings = lint(spec)
    if warnings:
        print(f"WARN: {len(warnings)} authoring hint(s) in {spec_path}")
        for n, warning in enumerate(warnings, 1):
            print(f"  {n}. {warning}")
    print(f"PASS: {spec_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
