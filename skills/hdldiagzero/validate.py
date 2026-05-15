#!/usr/bin/env python3
"""Validate an HDL block-diagram SVG for layout violations.

Author: Leonardo Capossio - bard0 design - hello@bard0.com
Year:   2026

Checks:
  1. CROSSING    - arrow paths passing through blocks they don't connect to
  2. SPACING     - parallel arrows closer than MIN_SPACING px (unreadable bundles)
  3. OVERLAP     - two arrow segments sharing the exact same route
  4. STUB        - arrows whose shaft is shorter than ~1.5x the arrowhead
  5. TEXT_BLOCK  - a block overlapping text that is not its own label
  6. TEXT_ARROW  - an arrow passing through text that is not its own edge label
  7. TEXT_TEXT   - significant overlap between two separate text labels
  8. PORT        - two arrows attaching to the same block within MIN_PORT_SEP px
                   (i.e. effectively meeting at the same point)
  9. BITWIDTH    - an arrow longer than BITWIDTH_MIN_ARROW_LEN with no nearby
                   text containing a digit (no bitwidth indicator at midpoint)
 10. DIAGONAL    - an arrow segment that is neither horizontal nor vertical.
                   The layout language is strictly orthogonal; diagonals are
                   always a routing bug.
 11. ENDPOINT    - an arrow start/end point is not attached to any block edge.
 12. PERPENDICULAR - an arrow endpoint touching a block must leave/enter
                   perpendicular to that block side, not run tangentially.
 13. LOOP        - a route loops away even though the connected ports are
                   collinear, facing each other, and have clear space.

Exit code = number of violations (0 = pass). Writes a structured report to stdout
that the calling agent can feed back into the next generation pass.

Caveats:
  - Assumes a flat coordinate space (no nested <g transform="...">).
  - Path parser handles M/L/H/V/Z. Curves (C/Q/A) are sampled at endpoints only.
  - Block detection: any <rect> with width >= 40 and height >= 25 is treated as a
    block. Smaller rects (legend swatches, decorations) are ignored.
  - Text bbox is estimated from font-size and character count (no font metrics);
    width is roughly len(text) * font_size * 0.55.
"""

import sys
import re
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass

MIN_SPACING = 15.0       # px - parallel arrows closer than this are unreadable
MIN_OVERLAP = 8.0        # px - ignore only tiny endpoint/rounding coincidences
EDGE_TOLERANCE = 4.0     # px - endpoint within this distance of rect edge = connected
MIN_BLOCK_W = 40.0
MIN_BLOCK_H = 25.0
STUB_RATIO = 1.5         # shaft length must be >= STUB_RATIO * marker width
MIN_PORT_SEP = 12.0      # px - two arrow endpoints on the same block must be this far apart
DEFAULT_FONT_SIZE = 12.0
TEXT_WIDTH_FACTOR = 0.55 # rough character-width / font-size ratio
TEXT_LABEL_PROXIMITY = 12.0  # text within this distance of an arrow is treated as its label
TEXT_TEXT_MIN_OVERLAP = 150.0 # px^2 - ignore tiny bbox estimation overlaps
BITWIDTH_MIN_ARROW_LEN = 50.0  # px - arrows shorter than this are exempt from bitwidth check
LOOP_EXCESS = 10.0       # px - allow small float/label wiggle before calling a loop

SVG_NS = "http://www.w3.org/2000/svg"
NS = {"svg": SVG_NS}


@dataclass
class Block:
    id: str
    label: str
    x: float
    y: float
    w: float
    h: float

    @property
    def rect(self):
        return (self.x, self.y, self.x + self.w, self.y + self.h)


@dataclass
class Arrow:
    id: str
    points: list
    marker_id: str | None = None
    stroke_width: float = 1.0


@dataclass
class Marker:
    id: str
    width: float
    height: float


@dataclass
class TextBox:
    text: str
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def rect(self):
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def cx(self):
        return (self.x1 + self.x2) / 2

    @property
    def cy(self):
        return (self.y1 + self.y2) / 2


def localname(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def find_label_for_rect(root, rect_elem, block):
    """Find a <text> element near the rect to use as a human-readable label."""
    cx = block.x + block.w / 2
    cy = block.y + block.h / 2
    best = None
    best_dist = float("inf")
    for t in root.iter():
        if localname(t.tag) != "text":
            continue
        try:
            tx = float(t.get("x", 0))
            ty = float(t.get("y", 0))
        except (TypeError, ValueError):
            continue
        if not (block.x - 5 <= tx <= block.x + block.w + 5):
            continue
        if not (block.y - 5 <= ty <= block.y + block.h + 5):
            continue
        d = math.hypot(tx - cx, ty - cy)
        if d < best_dist:
            best_dist = d
            best = (t.text or "").strip()
    return best or rect_elem.get("id", "")


_PATH_TOKEN = re.compile(r"[MLHVZmlhvzCcQqAaSsTt]|-?\d*\.?\d+(?:[eE][+-]?\d+)?")


def text_bbox(elem):
    """Estimate the bounding box of an SVG <text> element."""
    try:
        x = float(elem.get("x", 0))
        y = float(elem.get("y", 0))
    except (TypeError, ValueError):
        return None
    s = (elem.text or "").strip()
    if not s:
        return None
    fs = elem.get("font-size", "")
    font = DEFAULT_FONT_SIZE
    if fs:
        m = re.search(r"-?\d*\.?\d+", fs)
        if m:
            try:
                font = float(m.group(0))
            except ValueError:
                pass
    anchor = elem.get("text-anchor", "start")
    width = max(len(s) * font * TEXT_WIDTH_FACTOR, font * 0.6)
    height = font
    if anchor == "middle":
        x -= width / 2
    elif anchor == "end":
        x -= width
    # SVG text y is the baseline; bbox spans ~85% above and ~15% below.
    return TextBox(s, x, y - height * 0.85, x + width, y + height * 0.15)


def parse_path(d):
    """Parse SVG path 'd' to a list of (x,y) waypoints. Curves -> endpoints only."""
    tokens = _PATH_TOKEN.findall(d)
    points = []
    cx = cy = 0.0
    sx = sy = 0.0
    cmd = None
    i = 0
    n = len(tokens)
    def take_num():
        nonlocal i
        v = float(tokens[i]); i += 1
        return v
    while i < n:
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                points.append((sx, sy))
                cx, cy = sx, sy
            continue
        if cmd is None:
            i += 1
            continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x = take_num(); y = take_num()
            if rel and points:
                x += cx; y += cy
            cx, cy = x, y
            sx, sy = cx, cy
            points.append((cx, cy))
            cmd = "l" if rel else "L"
        elif c == "L":
            x = take_num(); y = take_num()
            if rel:
                x += cx; y += cy
            cx, cy = x, y
            points.append((cx, cy))
        elif c == "H":
            x = take_num()
            if rel: x += cx
            cx = x
            points.append((cx, cy))
        elif c == "V":
            y = take_num()
            if rel: y += cy
            cy = y
            points.append((cx, cy))
        elif c == "C":
            take_num(); take_num(); take_num(); take_num()
            x = take_num(); y = take_num()
            if rel: x += cx; y += cy
            cx, cy = x, y
            points.append((cx, cy))
        elif c == "S" or c == "Q":
            take_num(); take_num()
            x = take_num(); y = take_num()
            if rel: x += cx; y += cy
            cx, cy = x, y
            points.append((cx, cy))
        elif c == "T":
            x = take_num(); y = take_num()
            if rel: x += cx; y += cy
            cx, cy = x, y
            points.append((cx, cy))
        elif c == "A":
            take_num(); take_num(); take_num(); take_num(); take_num()
            x = take_num(); y = take_num()
            if rel: x += cx; y += cy
            cx, cy = x, y
            points.append((cx, cy))
        else:
            i += 1
    return points


def parse_svg(path):
    tree = ET.parse(path)
    root = tree.getroot()

    # Build the set of elements that live inside <defs> so we can skip them when
    # collecting blocks/arrows/texts. Marker arrowhead paths are NOT real arrows;
    # gradient stops are NOT real text; etc.
    defs_descendants = set()
    for defs in root.iter():
        if localname(defs.tag) != "defs":
            continue
        for child in defs.iter():
            defs_descendants.add(id(child))

    markers = {}
    for m in root.iter():
        if localname(m.tag) != "marker":
            continue
        mid = m.get("id", "")
        try:
            mw = float(m.get("markerWidth", 0))
            mh = float(m.get("markerHeight", 0))
        except (TypeError, ValueError):
            continue
        markers[mid] = Marker(mid, mw, mh)

    # SVG canvas dimensions, used to skip background rects that span the whole
    # drawing.
    try:
        svg_w = float(root.get("width", 0))
        svg_h = float(root.get("height", 0))
    except (TypeError, ValueError):
        svg_w = svg_h = 0.0

    blocks = []
    for r in root.iter():
        if localname(r.tag) != "rect":
            continue
        if id(r) in defs_descendants:
            continue
        try:
            x = float(r.get("x", 0))
            y = float(r.get("y", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
        except (TypeError, ValueError):
            continue
        if w < MIN_BLOCK_W or h < MIN_BLOCK_H:
            continue
        # Skip the canvas background rect (spans the full SVG).
        if svg_w and svg_h and w >= svg_w * 0.95 and h >= svg_h * 0.95:
            continue
        bid = r.get("id", f"rect{len(blocks)}")
        # Skip hierarchical group containers, clock-domain lane backgrounds,
        # and the legend card - they're decorative, not real blocks, and edges
        # are expected to cross their borders / draw over their fills.
        if bid.startswith(("group_", "lane_", "band_", "legend_")):
            continue
        b = Block(bid, bid, x, y, w, h)
        b.label = find_label_for_rect(root, r, b)
        blocks.append(b)

    arrows = []
    for elem in root.iter():
        if id(elem) in defs_descendants:
            continue
        tag = localname(elem.tag)
        marker_attr = elem.get("marker-end") or elem.get("marker-start") or ""
        m = re.search(r"url\(#([^)]+)\)", marker_attr)
        mid = m.group(1) if m else None
        sw = 1.0
        try:
            sw = float(elem.get("stroke-width", 1))
        except (TypeError, ValueError):
            pass
        if tag == "path":
            d = elem.get("d", "")
            pts = parse_path(d)
            if len(pts) < 2:
                continue
            aid = elem.get("id", f"path{len(arrows)}")
            arrows.append(Arrow(aid, pts, mid, sw))
        elif tag == "line":
            try:
                x1 = float(elem.get("x1", 0)); y1 = float(elem.get("y1", 0))
                x2 = float(elem.get("x2", 0)); y2 = float(elem.get("y2", 0))
            except (TypeError, ValueError):
                continue
            aid = elem.get("id", f"line{len(arrows)}")
            arrows.append(Arrow(aid, [(x1, y1), (x2, y2)], mid, sw))
        elif tag == "polyline":
            pts_attr = elem.get("points", "")
            nums = [float(n) for n in re.findall(r"-?\d*\.?\d+", pts_attr)]
            pts = list(zip(nums[0::2], nums[1::2]))
            if len(pts) < 2:
                continue
            aid = elem.get("id", f"polyline{len(arrows)}")
            arrows.append(Arrow(aid, pts, mid, sw))

    texts = []
    for t in root.iter():
        if localname(t.tag) != "text":
            continue
        if id(t) in defs_descendants:
            continue
        bb = text_bbox(t)
        if bb is not None:
            texts.append(bb)

    return blocks, arrows, markers, texts


def segments(arrow):
    return list(zip(arrow.points, arrow.points[1:]))


def seg_rect_clip(seg, rect):
    """Liang-Barsky: returns True if segment crosses rect interior."""
    (px1, py1), (px2, py2) = seg
    x1, y1, x2, y2 = rect
    dx = px2 - px1
    dy = py2 - py1
    t_min, t_max = 0.0, 1.0
    for p, q in [(-dx, px1 - x1), (dx, x2 - px1), (-dy, py1 - y1), (dy, y2 - py1)]:
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                if t > t_max: return False
                if t > t_min: t_min = t
            else:
                if t < t_min: return False
                if t < t_max: t_max = t
    return (t_max - t_min) > 1e-3


def endpoint_on_rect(point, rect, tol=EDGE_TOLERANCE):
    px, py = point
    x1, y1, x2, y2 = rect
    near_left = abs(px - x1) <= tol
    near_right = abs(px - x2) <= tol
    near_top = abs(py - y1) <= tol
    near_bot = abs(py - y2) <= tol
    in_x = (x1 - tol) <= px <= (x2 + tol)
    in_y = (y1 - tol) <= py <= (y2 + tol)
    return ((near_left or near_right) and in_y) or ((near_top or near_bot) and in_x)


def endpoint_side(point, rect, tol=EDGE_TOLERANCE):
    """Return the nearest side name when `point` lies on a rect edge."""
    if not endpoint_on_rect(point, rect, tol):
        return None
    px, py = point
    x1, y1, x2, y2 = rect
    candidates = [
        ("left", abs(px - x1)),
        ("right", abs(px - x2)),
        ("top", abs(py - y1)),
        ("bottom", abs(py - y2)),
    ]
    side, dist = min(candidates, key=lambda item: item[1])
    return side if dist <= tol else None


def side_normal(side):
    return {
        "left": (-1.0, 0.0),
        "right": (1.0, 0.0),
        "top": (0.0, -1.0),
        "bottom": (0.0, 1.0),
    }[side]


def endpoint_block_side(point, blocks):
    for b in blocks:
        side = endpoint_side(point, b.rect)
        if side is not None:
            return b, side
    return None, None


def check_floating_endpoints(blocks, arrows):
    """Every real arrow must begin and end on some block edge."""
    violations = []
    for a in arrows:
        if "legend" in a.id.lower():
            continue
        for which, endpoint in (("start", a.points[0]), ("end", a.points[-1])):
            b, side = endpoint_block_side(endpoint, blocks)
            if b is not None:
                continue
            violations.append(
                f"ENDPOINT: arrow '{a.id}' {which} endpoint "
                f"({endpoint[0]:.0f},{endpoint[1]:.0f}) is not attached to "
                f"any block edge. Move the endpoint onto the source/target "
                f"block boundary."
            )
    return violations


def check_crossings(blocks, arrows):
    violations = []
    for a in arrows:
        connected = set()
        for ep in (a.points[0], a.points[-1]):
            for b in blocks:
                if endpoint_on_rect(ep, b.rect):
                    connected.add(b.id)
        for seg in segments(a):
            for b in blocks:
                if b.id in connected:
                    continue
                if seg_rect_clip(seg, b.rect):
                    mx = (seg[0][0] + seg[1][0]) / 2
                    my = (seg[0][1] + seg[1][1]) / 2
                    violations.append(
                        f"CROSSING: arrow '{a.id}' passes through block "
                        f"'{b.label}' near ({mx:.0f},{my:.0f}). "
                        f"Reroute around block (which occupies "
                        f"x={b.x:.0f}..{b.x+b.w:.0f}, y={b.y:.0f}..{b.y+b.h:.0f})."
                    )
                    break
    return violations


def check_perpendicular_ports(blocks, arrows):
    """Block endpoints must use normal entry/exit stubs.

    Auto-routed edges do this naturally. Explicit `route.points` can bypass the
    router, so validate the final SVG shape: a wire on a left/right side must
    start or end horizontally; a wire on a top/bottom side must start or end
    vertically. The segment must also point away from the block interior.
    """
    violations = []
    for a in arrows:
        if len(a.points) < 2:
            continue
        checks = (
            ("start", a.points[0], a.points[1], "leave"),
            ("end", a.points[-1], a.points[-2], "enter"),
        )
        for which, endpoint, neighbor, verb in checks:
            for b in blocks:
                side = endpoint_side(endpoint, b.rect)
                if side is None:
                    continue
                nx, ny = side_normal(side)
                vx = neighbor[0] - endpoint[0]
                vy = neighbor[1] - endpoint[1]
                normal_dist = vx * nx + vy * ny
                tangent_dist = abs(vy) if nx else abs(vx)
                if tangent_dist > EDGE_TOLERANCE or normal_dist <= EDGE_TOLERANCE:
                    violations.append(
                        f"PERPENDICULAR: arrow '{a.id}' {which} endpoint on "
                        f"block '{b.label}' {side} side must {verb} "
                        f"perpendicular to the block. First/last segment is "
                        f"from ({endpoint[0]:.0f},{endpoint[1]:.0f}) to "
                        f"({neighbor[0]:.0f},{neighbor[1]:.0f})."
                    )
                break
    return violations


def check_unnecessary_loops(blocks, arrows):
    """Flag routes that detour when a clear straight port-to-port path exists.

    This is intentionally narrow: only collinear, face-to-face ports are checked.
    Non-collinear routes, same-side u-turns, and paths blocked by another block
    are left to the normal router/other validators.
    """
    violations = []
    for a in arrows:
        if len(a.points) < 3:
            continue
        start = a.points[0]
        end = a.points[-1]
        sb, ss = endpoint_block_side(start, blocks)
        eb, es = endpoint_block_side(end, blocks)
        if sb is None or eb is None or sb.id == eb.id:
            continue

        horiz = (
            ((ss, es) == ("right", "left") and end[0] >= start[0])
            or ((ss, es) == ("left", "right") and end[0] <= start[0])
        )
        vert = (
            ((ss, es) == ("bottom", "top") and end[1] >= start[1])
            or ((ss, es) == ("top", "bottom") and end[1] <= start[1])
        )
        if horiz and abs(start[1] - end[1]) > EDGE_TOLERANCE:
            continue
        if vert and abs(start[0] - end[0]) > EDGE_TOLERANCE:
            continue
        if not (horiz or vert):
            continue

        direct = (start, end)
        blocked = False
        for b in blocks:
            if b.id in (sb.id, eb.id):
                continue
            if seg_rect_clip(direct, b.rect):
                blocked = True
                break
        if blocked:
            continue

        direct_len = math.hypot(end[0] - start[0], end[1] - start[1])
        actual_len = path_length(a)
        if actual_len > direct_len + LOOP_EXCESS:
            violations.append(
                f"LOOP: arrow '{a.id}' detours {actual_len - direct_len:.0f}px "
                f"even though blocks '{sb.label}' and '{eb.label}' have a clear "
                f"direct {ss}-to-{es} connection. Use a direct route or remove "
                f"unnecessary waypoints."
            )
    return violations


def point_seg_dist(p, seg):
    (ax, ay), (bx, by) = seg
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def seg_distance(s1, s2):
    return min(
        point_seg_dist(s1[0], s2),
        point_seg_dist(s1[1], s2),
        point_seg_dist(s2[0], s1),
        point_seg_dist(s2[1], s1),
    )


def seg_angle(seg):
    (ax, ay), (bx, by) = seg
    return math.degrees(math.atan2(by - ay, bx - ax)) % 180


def projections_overlap(s1, s2):
    """Do the two parallel segments share any along-axis overlap?"""
    a = seg_angle(s1)
    if abs(a - 90) < 5:
        a1, a2 = sorted([s1[0][1], s1[1][1]])
        b1, b2 = sorted([s2[0][1], s2[1][1]])
    else:
        a1, a2 = sorted([s1[0][0], s1[1][0]])
        b1, b2 = sorted([s2[0][0], s2[1][0]])
    return max(a1, b1) < min(a2, b2)


def is_parallel(s1, s2, angle_tol=5):
    a = abs(seg_angle(s1) - seg_angle(s2))
    return a < angle_tol or abs(a - 180) < angle_tol


def check_spacing(arrows):
    violations = []
    seen = set()
    for i, a in enumerate(arrows):
        for j, b in enumerate(arrows):
            if i >= j:
                continue
            for s1 in segments(a):
                for s2 in segments(b):
                    if not is_parallel(s1, s2):
                        continue
                    if not projections_overlap(s1, s2):
                        continue
                    d = seg_distance(s1, s2)
                    if 0 < d < MIN_SPACING:
                        key = (a.id, b.id)
                        if key in seen:
                            continue
                        seen.add(key)
                        mx = (s1[0][0] + s1[1][0]) / 2
                        my = (s1[0][1] + s1[1][1]) / 2
                        violations.append(
                            f"SPACING: arrows '{a.id}' and '{b.id}' run parallel "
                            f"only {d:.0f}px apart (min {MIN_SPACING:.0f}px) "
                            f"near ({mx:.0f},{my:.0f}). Increase separation or "
                            f"merge them into a single bus arrow."
                        )
    return violations


def segment_overlap(s1, s2):
    """Return overlapping length when two orthogonal segments share a line."""
    (x1, y1), (x2, y2) = s1
    (x3, y3), (x4, y4) = s2
    eps = 1e-6
    if abs(y1 - y2) <= eps and abs(y3 - y4) <= eps and abs(y1 - y3) <= eps:
        lo = max(min(x1, x2), min(x3, x4))
        hi = min(max(x1, x2), max(x3, x4))
        if hi - lo >= MIN_OVERLAP:
            return hi - lo, ((lo + hi) / 2, y1)
    if abs(x1 - x2) <= eps and abs(x3 - x4) <= eps and abs(x1 - x3) <= eps:
        lo = max(min(y1, y2), min(y3, y4))
        hi = min(max(y1, y2), max(y3, y4))
        if hi - lo >= MIN_OVERLAP:
            return hi - lo, (x1, (lo + hi) / 2)
    return None


def check_overlapping_segments(arrows):
    violations = []
    seen = set()
    for i, a in enumerate(arrows):
        if a.id.startswith("legend-arrow"):
            continue
        for b in arrows[i + 1:]:
            if b.id.startswith("legend-arrow"):
                continue
            for s1 in segments(a):
                for s2 in segments(b):
                    overlap = segment_overlap(s1, s2)
                    if not overlap:
                        continue
                    key = (a.id, b.id)
                    if key in seen:
                        continue
                    seen.add(key)
                    length, mid = overlap
                    violations.append(
                        f"OVERLAP: arrows '{a.id}' and '{b.id}' share the "
                        f"same route for {length:.0f}px near "
                        f"({mid[0]:.0f},{mid[1]:.0f}). Move one route into "
                        f"a different gutter or add a non-overlapping detour."
                    )
    return violations


def path_length(arrow):
    total = 0.0
    for (x1, y1), (x2, y2) in segments(arrow):
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def rects_overlap(r1, r2):
    x1a, y1a, x2a, y2a = r1
    x1b, y1b, x2b, y2b = r2
    return not (x2a <= x1b or x2b <= x1a or y2a <= y1b or y2b <= y1a)


def rect_overlap_area(r1, r2):
    x1a, y1a, x2a, y2a = r1
    x1b, y1b, x2b, y2b = r2
    w = min(x2a, x2b) - max(x1a, x1b)
    h = min(y2a, y2b) - max(y1a, y1b)
    return max(0.0, w) * max(0.0, h)


def point_in_rect(px, py, rect):
    x1, y1, x2, y2 = rect
    return x1 <= px <= x2 and y1 <= py <= y2


def check_text_overlap(blocks, arrows, texts):
    """A block can overlap its own label; an arrow can pass through its own
    edge label. Anything else is a violation."""
    violations = []
    for tb in texts:
        # Identify the owning block: smallest block whose rect contains the text centroid.
        owner_block = None
        for b in blocks:
            if point_in_rect(tb.cx, tb.cy, b.rect):
                if owner_block is None or (b.w * b.h) < (owner_block.w * owner_block.h):
                    owner_block = b
        # Identify the owning arrow: closest arrow within proximity threshold.
        owner_arrow_id = None
        best = TEXT_LABEL_PROXIMITY
        for a in arrows:
            for seg in segments(a):
                d = point_seg_dist((tb.cx, tb.cy), seg)
                if d < best:
                    best = d
                    owner_arrow_id = a.id

        for b in blocks:
            if owner_block is not None and b.id == owner_block.id:
                continue
            if rects_overlap(tb.rect, b.rect):
                violations.append(
                    f"TEXT_BLOCK: block '{b.label}' (x={b.x:.0f}..{b.x+b.w:.0f}, "
                    f"y={b.y:.0f}..{b.y+b.h:.0f}) overlaps the text '{tb.text}' "
                    f"at ({tb.cx:.0f},{tb.cy:.0f}). Move the block or the text "
                    f"so they don't intersect."
                )
        for a in arrows:
            if a.id == owner_arrow_id:
                continue
            for seg in segments(a):
                if seg_rect_clip(seg, tb.rect):
                    violations.append(
                        f"TEXT_ARROW: arrow '{a.id}' passes through text "
                        f"'{tb.text}' at ({tb.cx:.0f},{tb.cy:.0f}). Reroute the "
                        f"arrow around the text, or move the text label to "
                        f"clear space."
                    )
                    break
    return violations


def check_text_text_overlap(texts):
    """Flag significant label collisions.

    Adjacent multi-line labels can have tiny estimated bbox overlaps because
    we do not have real font metrics. Only report overlaps with enough area to
    be visibly confusing.
    """
    violations = []
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            area = rect_overlap_area(a.rect, b.rect)
            if area < TEXT_TEXT_MIN_OVERLAP:
                continue
            violations.append(
                f"TEXT_TEXT: text '{a.text}' at ({a.cx:.0f},{a.cy:.0f}) "
                f"overlaps text '{b.text}' at ({b.cx:.0f},{b.cy:.0f}) "
                f"(overlap area {area:.0f}px^2). Move or suppress one label."
            )
    return violations


def check_port_separation(blocks, arrows):
    """Two arrow endpoints attached to the same block must be visibly distinct."""
    violations = []
    by_block = {}
    for a in arrows:
        for ep in (a.points[0], a.points[-1]):
            for b in blocks:
                if endpoint_on_rect(ep, b.rect):
                    by_block.setdefault(b.id, []).append((a.id, ep, b))
                    break
    seen = set()
    for bid, eps in by_block.items():
        for i in range(len(eps)):
            for j in range(i + 1, len(eps)):
                aid_i, p_i, bi = eps[i]
                aid_j, p_j, _ = eps[j]
                if aid_i == aid_j:
                    continue
                d = math.hypot(p_i[0] - p_j[0], p_i[1] - p_j[1])
                if d < MIN_PORT_SEP:
                    key = tuple(sorted([aid_i, aid_j])) + (bid,)
                    if key in seen:
                        continue
                    seen.add(key)
                    violations.append(
                        f"PORT: arrows '{aid_i}' and '{aid_j}' both attach to "
                        f"block '{bi.label}' only {d:.1f}px apart at "
                        f"({p_i[0]:.0f},{p_i[1]:.0f}) and "
                        f"({p_j[0]:.0f},{p_j[1]:.0f}) (min {MIN_PORT_SEP:.0f}px). "
                        f"Spread the connection points along the block edge."
                    )
    return violations


def arrow_midpoint(arrow):
    """Return (x,y) at half the path length along the arrow."""
    total = path_length(arrow)
    half = total / 2
    accum = 0.0
    for (x1, y1), (x2, y2) in segments(arrow):
        seg_len = math.hypot(x2 - x1, y2 - y1)
        if seg_len <= 0:
            continue
        if accum + seg_len >= half:
            t = (half - accum) / seg_len
            return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
        accum += seg_len
    return arrow.points[0]


def check_bitwidth_labels(arrows, texts):
    """Each long arrow must have a text label (containing a digit) within
    TEXT_LABEL_PROXIMITY of its path. Short arrows and ones with 'legend' in
    their id are exempt (they're typically swatches in a key)."""
    violations = []
    arrow_owned_texts = {a.id: [] for a in arrows}
    for tb in texts:
        owner_id = None
        best = TEXT_LABEL_PROXIMITY
        for a in arrows:
            for seg in segments(a):
                d = point_seg_dist((tb.cx, tb.cy), seg)
                if d < best:
                    best = d
                    owner_id = a.id
        if owner_id is not None:
            arrow_owned_texts[owner_id].append(tb.text)
    for a in arrows:
        if path_length(a) < BITWIDTH_MIN_ARROW_LEN:
            continue
        if "legend" in a.id.lower():
            continue
        labels = arrow_owned_texts[a.id]
        if not any(t.strip() for t in labels):
            mx, my = arrow_midpoint(a)
            violations.append(
                f"BITWIDTH: arrow '{a.id}' has no label near its midpoint "
                f"(~{mx:.0f},{my:.0f}). Add a text label (bitwidth like '32' "
                f"or '[31:0]', or a protocol name like 'RGMII' / 'AXI-S 64b') "
                f"within {TEXT_LABEL_PROXIMITY:.0f}px of the arrow path."
            )
    return violations


def check_diagonal_arrows(arrows):
    """Every arrow segment must be horizontal or vertical. A segment that moves
    in both x and y is a diagonal - banned outright.

    Tolerance of 0.5px absorbs sub-pixel float drift from upstream coord
    computation without admitting visibly-diagonal segments."""
    violations = []
    seen = set()
    for a in arrows:
        if "legend" in a.id.lower():
            continue
        for (x1, y1), (x2, y2) in segments(a):
            if abs(x1 - x2) > 0.5 and abs(y1 - y2) > 0.5:
                if a.id in seen:
                    break
                seen.add(a.id)
                violations.append(
                    f"DIAGONAL: arrow '{a.id}' has a diagonal segment from "
                    f"({x1:.0f},{y1:.0f}) to ({x2:.0f},{y2:.0f}). All wires "
                    f"must be strictly horizontal or vertical; insert an "
                    f"orthogonal bend."
                )
                break
    return violations


def check_stub_arrows(arrows, markers):
    violations = []
    for a in arrows:
        if "legend" in a.id.lower():
            continue
        if not a.marker_id or a.marker_id not in markers:
            continue
        m = markers[a.marker_id]
        marker_px = m.width * a.stroke_width
        plen = path_length(a)
        if plen < marker_px * STUB_RATIO:
            violations.append(
                f"STUB: arrow '{a.id}' shaft is {plen:.0f}px but arrowhead is "
                f"~{marker_px:.0f}px wide. Move the source/target blocks farther "
                f"apart or reroute so the shaft is at least "
                f"{marker_px * STUB_RATIO:.0f}px."
            )
    return violations


def main():
    if len(sys.argv) < 2:
        print("Usage: validate.py <svg-file>", file=sys.stderr)
        sys.exit(2)
    svg_path = sys.argv[1]
    blocks, arrows, markers, texts = parse_svg(svg_path)

    violations = []
    violations += check_crossings(blocks, arrows)
    violations += check_spacing(arrows)
    violations += check_overlapping_segments(arrows)
    violations += check_stub_arrows(arrows, markers)
    violations += check_text_overlap(blocks, arrows, texts)
    violations += check_text_text_overlap(texts)
    violations += check_port_separation(blocks, arrows)
    violations += check_bitwidth_labels(arrows, texts)
    violations += check_diagonal_arrows(arrows)
    violations += check_floating_endpoints(blocks, arrows)
    violations += check_perpendicular_ports(blocks, arrows)
    violations += check_unnecessary_loops(blocks, arrows)

    summary = (
        f"{len(blocks)} blocks, {len(arrows)} arrows, "
        f"{len(markers)} markers, {len(texts)} texts"
    )
    if not violations:
        print(f"PASS: {summary}, 0 violations")
        sys.exit(0)
    print(f"FAIL: {summary}, {len(violations)} violations")
    for i, v in enumerate(violations, 1):
        print(f"  {i}. {v}")
    sys.exit(min(len(violations), 250))


if __name__ == "__main__":
    main()
