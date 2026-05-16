"""
Self-tests for the hdldiagZero skill. Runs in CI and locally.

Test conditions: each test runs `python` (the same interpreter that invoked
this script) against `render.py` / `validate.py` / `install.py` from this
directory; no external tools or network. Designed to take well under 10s on
any modern machine.

Author: Leonardo Capossio - bard0 design - hello@bard0.com
Year:   2026
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_DIR = ROOT / "skills" / "hdldiagzero"
RENDER = str(SKILL_DIR / "render.py")
VALIDATE = str(SKILL_DIR / "validate.py")
VALIDATE_SPEC = str(SKILL_DIR / "validate_spec.py")
PY = sys.executable


@contextmanager
def _tmpdir():
    """Make a temp directory we can actually write to.

    System temp is sometimes locked down (corporate Windows, sandboxed
    runners). Honor HDLDIAG_TEST_TMP first, then try normal system temp and
    repo-local fallbacks. Each candidate is probed by creating a child temp
    dir and writing inside it before tests are allowed to use it."""
    candidates = []
    if os.environ.get("HDLDIAG_TEST_TMP"):
        candidates.append(Path(os.environ["HDLDIAG_TEST_TMP"]))
    candidates.extend(
        [
            Path(tempfile.gettempdir()),
            ROOT / "tmp",
            ROOT / ".test_tmp",
        ]
    )

    errors = []
    seen = set()
    for base in candidates:
        try:
            base = base.resolve()
        except OSError:
            base = base.absolute()
        if base in seen:
            continue
        seen.add(base)
        try:
            base.mkdir(parents=True, exist_ok=True)
            tmp = base / f"hdldiag-{uuid.uuid4().hex}"
            tmp.mkdir()
            probe = tmp / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            errors.append(f"{base}: {exc}")
            continue
        try:
            yield str(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return

    raise RuntimeError("no writable temp directory found:\n  " + "\n  ".join(errors))

# The bundled fixture deliberately contains exactly this many violations across
# the validator rules: the original 8 fixture violations, 6 ENDPOINT reports
# on intentionally free-floating arrows, and explicit LOOP / PERPENDICULAR /
# DIAGONAL / TEXT_TEXT coverage. If the renderer or the validator regresses,
# this number changes and CI fails.
EXPECTED_FIXTURE_VIOLATIONS = 20

FAILURES: list[str] = []


def run(cmd: list[str], expect_rc: int = 0, label: str = "") -> subprocess.CompletedProcess:
    label = label or " ".join(cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if proc.returncode != expect_rc:
        FAILURES.append(
            f"[{label}] expected rc={expect_rc} got {proc.returncode}\n"
            f"  stdout: {proc.stdout.strip()}\n"
            f"  stderr: {proc.stderr.strip()}"
        )
    return proc


def test_validator_fixture() -> None:
    run(
        [PY, VALIDATE, "not_sample_broken_validator_fixture.svg"],
        expect_rc=EXPECTED_FIXTURE_VIOLATIONS,
        label="validator-fixture",
    )


def test_spec_validator_passes_on_test_spec() -> None:
    run([PY, VALIDATE_SPEC, "test_spec.json"], label="spec-validator-pass")


def test_spec_validator_catches_bad_spec() -> None:
    """A spec with an unknown block id in an edge must fail validation."""
    with _tmpdir() as tmp:
        bad = Path(tmp) / "bad.json"
        bad.write_text(
            '{\n'
            '  "domains": {"d": {"freq_mhz": 100, "color": "#42A5F5"}},\n'
            '  "blocks": [{"id": "a", "domain": "d", "row": 0, "col": 0}],\n'
            '  "edges":  [{"from": "a", "to": "ghost", "kind": "generic"}]\n'
            '}\n',
            encoding="utf-8",
        )
        run([PY, VALIDATE_SPEC, str(bad)], expect_rc=1, label="spec-validator-fail")


_BASE_SPEC = {
    "domains": {"d": {"freq_mhz": 100, "color": "#42A5F5"}},
    "blocks": [{"id": "a", "domain": "d", "row": 0, "col": 0}],
    "edges": [],
}


def _spec_with(**override_block_fields):
    """Return a deep-copy of the base spec with one block field overridden."""
    import copy
    s = copy.deepcopy(_BASE_SPEC)
    s["blocks"][0].update(override_block_fields)
    return s


def _write_and_check(tmp: Path, name: str, spec_obj, expect_rc: int, label: str) -> None:
    p = tmp / name
    p.write_text(json.dumps(spec_obj), encoding="utf-8")
    run([PY, VALIDATE_SPEC, str(p)], expect_rc=expect_rc, label=label)


def _localname(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _assert_rendered_spec_content(svg_path: Path, spec_path: str, label: str) -> None:
    spec = json.loads((ROOT / spec_path).read_text(encoding="utf-8"))
    root = ET.parse(svg_path).getroot()
    rect_ids = {
        elem.get("id")
        for elem in root.iter()
        if _localname(elem.tag) == "rect"
    }
    path_ids = {
        elem.get("id")
        for elem in root.iter()
        if _localname(elem.tag) == "path"
    }
    missing_blocks = [b["id"] for b in spec["blocks"] if b["id"] not in rect_ids]
    missing_edges = [
        f'edge_{e["from"]}_to_{e["to"]}'
        for e in spec["edges"]
        if f'edge_{e["from"]}_to_{e["to"]}' not in path_ids
    ]
    if missing_blocks:
        FAILURES.append(f"[{label}] missing rendered block ids: {missing_blocks}")
    if missing_edges:
        FAILURES.append(f"[{label}] missing rendered edge ids: {missing_edges}")


def test_spec_validator_strict_types() -> None:
    """Strict-type checks: bool != int, string != bool, etc."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        # external=string must be rejected
        bad = _spec_with(external="false")
        _write_and_check(tmp, "ext_str.json", bad, 1, "spec-strict-external-str")
        # row=true must be rejected (bool subclasses int but isn't a coordinate)
        bad = _spec_with(row=True)
        _write_and_check(tmp, "row_bool.json", bad, 1, "spec-strict-row-bool")
        # row/col support quarter-step placement, but not arbitrary fractions
        good = _spec_with(row=0.25, col=1.5)
        _write_and_check(tmp, "quarter_step.json", good, 0, "spec-strict-quarter-step")
        bad = _spec_with(col=0.3)
        _write_and_check(tmp, "bad_step.json", bad, 1, "spec-strict-bad-step")
        # bad color
        bad = {
            "domains": {"d": {"color": "not-a-color"}},
            "blocks": [{"id": "a", "domain": "d", "row": 0, "col": 0}],
            "edges": [],
        }
        _write_and_check(tmp, "bad_color.json", bad, 1, "spec-strict-color")
        # unknown field in block
        bad = _spec_with(unknown_field=42)
        _write_and_check(tmp, "unknown_block.json", bad, 1, "spec-strict-unknown-block-field")
        # width as list (neither int nor string)
        bad = {
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "a", "domain": "d", "row": 0, "col": 0},
                {"id": "b", "domain": "d", "row": 0, "col": 1},
            ],
            "edges": [{"from": "a", "to": "b", "kind": "generic", "width": [1, 2]}],
        }
        _write_and_check(tmp, "bad_width.json", bad, 1, "spec-strict-width-list")
        bad = {
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "a", "domain": "d", "row": 0, "col": 0},
                {"id": "b", "domain": "d", "row": 0, "col": 1},
            ],
            "edges": [{"from": "a", "to": "b", "kind": "generic", "width": "eviltext ); //"}],
        }
        _write_and_check(tmp, "bad_width_str.json", bad, 1, "spec-strict-width-string")
        bad = {
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "a", "domain": "d", "row": 0, "col": 0},
                {"id": "b", "domain": "d", "row": 0, "col": 1},
            ],
            "edges": [
                {"from": "a", "to": "b", "kind": "generic", "width": "bus"},
                {"from": "a", "to": "b", "kind": "generic", "width": "irq"},
            ],
        }
        _write_and_check(tmp, "duplicate_edge.json", bad, 1, "spec-strict-duplicate-edge")
        # 3-digit color is no longer accepted (schema documents #RRGGBB only)
        bad = {
            "domains": {"d": {"color": "#abc"}},
            "blocks": [{"id": "a", "domain": "d", "row": 0, "col": 0}],
            "edges": [],
        }
        _write_and_check(tmp, "short_color.json", bad, 1, "spec-strict-color-3digit")
        # 8-digit (RRGGBBAA) is no longer accepted
        bad = {
            "domains": {"d": {"color": "#42A5F5FF"}},
            "blocks": [{"id": "a", "domain": "d", "row": 0, "col": 0}],
            "edges": [],
        }
        _write_and_check(tmp, "long_color.json", bad, 1, "spec-strict-color-8digit")
        # grid: float values rejected
        bad = {
            "grid": {"cell_w": 220.5, "cell_h": 90, "gutter_x": 90, "gutter_y": 70, "margin": 36},
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [{"id": "a", "domain": "d", "row": 0, "col": 0}],
            "edges": [],
        }
        _write_and_check(tmp, "grid_float.json", bad, 1, "spec-strict-grid-float")
        # external block with domain set: rejected
        bad = {
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [{"id": "a", "external": True, "domain": "d", "row": 0, "col": 0}],
            "edges": [],
        }
        _write_and_check(tmp, "ext_domain.json", bad, 1, "spec-strict-external-domain")
        # external block with domain_b set: rejected
        bad = {
            "domains": {"d": {"color": "#42A5F5"}, "e": {"color": "#FFA726"}},
            "blocks": [{"id": "a", "external": True, "domain_b": "e", "row": 0, "col": 0}],
            "edges": [],
        }
        _write_and_check(tmp, "ext_domain_b.json", bad, 1, "spec-strict-external-domain-b")
        bad = _spec_with(side="left")
        _write_and_check(tmp, "internal_side.json", bad, 1, "spec-strict-internal-side")


def test_spec_validator_rejects_extraction_metadata() -> None:
    """Extraction policy is guidance, not inert renderer schema."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        bad = {**_BASE_SPEC, "extraction": {"hide_primitives": False}}
        _write_and_check(tmp, "extraction.json", bad, 1, "spec-extraction-rejected")


def test_renderer_light() -> None:
    with _tmpdir() as tmp:
        out = Path(tmp) / "out.svg"
        run([PY, RENDER, "test_spec.json", str(out)], label="render-light")
        if out.is_file():
            _assert_rendered_spec_content(out, "test_spec.json", "render-light-content")
            run([PY, VALIDATE, str(out)], label="validate-light-output")


def test_lanes_render_and_skip_geometry() -> None:
    """A spec with `lanes` must render full-width tinted bands behind blocks
    with a `lane_*` id and pass geometry validation. Lane backgrounds must
    NOT be treated as blocks (otherwise arrows between blocks would all be
    flagged as crossings)."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "title": "lanes test",
            "domains": {
                "a": {"freq_mhz": 100, "color": "#42A5F5", "border": "#0D47A1"},
                "b": {"freq_mhz": 200, "color": "#66BB6A", "border": "#1B5E20"},
            },
            "lanes": {"a": {"rows": [0]}, "b": {"rows": [1]}},
            "blocks": [
                {"id": "x", "domain": "a", "row": 0, "col": 0},
                {"id": "y", "domain": "a", "row": 0, "col": 1},
                {"id": "z", "domain": "b", "row": 1, "col": 0},
                {"id": "w", "domain": "b", "row": 1, "col": 1},
            ],
            "edges": [
                {"from": "x", "to": "y", "kind": "axi-mm", "width": 64},
                {"from": "y", "to": "w", "kind": "cdc", "width": "cdc"},
                {"from": "z", "to": "w", "kind": "axi-mm", "width": 64},
            ],
        }
        spec_path = tmp / "lanes.json"
        out = tmp / "lanes.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="lanes-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="lanes-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            for needle in ('id="lane_a"', 'id="lane_b"', "a domain", "b domain"):
                if needle not in svg:
                    FAILURES.append(f"[lanes-render] missing '{needle}' in SVG")
            run([PY, VALIDATE, str(out)], label="lanes-validate")


def test_lanes_column_orientation() -> None:
    """Lanes can be vertical (`cols`) instead of horizontal (`rows`). The
    rendered SVG must still include the lane rect + header, and geometry
    must validate."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "title": "vertical lanes test",
            "domains": {
                "host": {"freq_mhz": 100, "color": "#42A5F5", "border": "#0D47A1"},
                "phy":  {"freq_mhz": 125, "color": "#FFA726", "border": "#E65100"},
            },
            "lanes": {
                "host": {"cols": [0]},
                "phy":  {"cols": [1, 1.25]},
            },
            "blocks": [
                {"id": "h0", "domain": "host", "row": 0, "col": 0},
                {"id": "h1", "domain": "host", "row": 1, "col": 0},
                {"id": "p0", "domain": "phy",  "row": 0, "col": 1},
                {"id": "p1", "domain": "phy",  "row": 1, "col": 1},
            ],
            "edges": [
                {"from": "h0", "to": "p0", "kind": "axi-mm", "width": 32},
                {"from": "h1", "to": "p1", "kind": "axi-mm", "width": 32},
            ],
        }
        spec_path = tmp / "vlanes.json"
        out = tmp / "vlanes.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="vlanes-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="vlanes-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            for needle in ('id="lane_host"', 'id="lane_phy"', "host domain", "phy domain"):
                if needle not in svg:
                    FAILURES.append(f"[vlanes-render] missing '{needle}' in SVG")
            run([PY, VALIDATE, str(out)], label="vlanes-validate")


def test_spec_validator_rejects_lane_with_both_rows_and_cols() -> None:
    """A lane entry with both rows and cols (or neither) must fail validation."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec_both = {
            "domains": {"a": {"color": "#42A5F5"}},
            "lanes": {"a": {"rows": [0], "cols": [0]}},
            "blocks": [{"id": "x", "domain": "a", "row": 0, "col": 0}],
            "edges": [],
        }
        _write_and_check(tmp, "lane_both.json", spec_both, 1, "spec-lane-both-axes")
        spec_neither = {
            "domains": {"a": {"color": "#42A5F5"}},
            "lanes": {"a": {}},
            "blocks": [{"id": "x", "domain": "a", "row": 0, "col": 0}],
            "edges": [],
        }
        _write_and_check(tmp, "lane_neither.json", spec_neither, 1, "spec-lane-neither-axis")


def test_spec_validator_rejects_unknown_lane_domain() -> None:
    """A lane keyed on a domain not declared in `domains` must fail."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "domains": {"a": {"color": "#42A5F5"}},
            "lanes": {"ghost": {"rows": [0]}},
            "blocks": [{"id": "x", "domain": "a", "row": 0, "col": 0}],
            "edges": [],
        }
        _write_and_check(tmp, "bad_lane.json", spec, 1, "spec-unknown-lane-domain")


def test_legend_card_renders_top_right() -> None:
    """The new top-right legend card must appear in every output and the
    `legend_card` rect must be excluded from the validator's block list."""
    with _tmpdir() as tmp:
        out = Path(tmp) / "smoke.svg"
        run([PY, RENDER, "test_spec.json", str(out)], label="legend-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            for needle in (
                'id="legend_card"',
                "Clock domains",
                "Connection styles",
            ):
                if needle not in svg:
                    FAILURES.append(f"[legend-render] missing '{needle}' in SVG")
            run([PY, VALIDATE, str(out)], label="legend-validate")


def test_renderer_can_hide_legend() -> None:
    """Large diagrams can suppress the legend column so the canvas stays tight."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "legend": False,
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "a", "domain": "d", "row": 0, "col": 0},
                {"id": "b", "domain": "d", "row": 0, "col": 1},
            ],
            "edges": [{"from": "a", "to": "b", "kind": "generic", "width": "bus"}],
        }
        spec_path = tmp / "no_legend.json"
        out = tmp / "no_legend.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="no-legend-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="no-legend-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            if 'id="legend_card"' in svg:
                FAILURES.append("[no-legend-render] legend_card should be omitted")
            if 'width="632"' not in svg:
                FAILURES.append("[no-legend-render] canvas still reserved legend width")
            run([PY, VALIDATE, str(out)], label="no-legend-validate")


def test_bands_render_and_skip_geometry() -> None:
    """Functional bands are decorative backgrounds and must not count as blocks."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "legend": False,
            "domains": {"d": {"color": "#42A5F5"}},
            "bands": {
                "system": {
                    "label": "system band",
                    "rows": [0, 1],
                    "color": "#CBD5E1",
                    "border": "#475569",
                }
            },
            "blocks": [
                {"id": "a", "domain": "d", "row": 0, "col": 0},
                {"id": "b", "domain": "d", "row": 1, "col": 1},
            ],
            "edges": [{"from": "a", "to": "b", "kind": "generic", "width": "bus"}],
        }
        spec_path = tmp / "bands.json"
        out = tmp / "bands.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="bands-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="bands-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            if 'id="band_system"' not in svg:
                FAILURES.append("[bands-render] missing band_system rect")
            if "system band" not in svg:
                FAILURES.append("[bands-render] missing band label")
            run([PY, VALIDATE, str(out)], label="bands-validate")


def test_band_label_can_be_suppressed() -> None:
    """Empty/null band labels suppress text so bands can sit under groups."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "legend": False,
            "domains": {"d": {"color": "#42A5F5"}},
            "bands": {
                "system": {
                    "label": None,
                    "rows": [0],
                    "color": "#CBD5E1",
                    "border": "#475569",
                }
            },
            "blocks": [{"id": "a", "domain": "d", "row": 0, "col": 0}],
            "edges": [],
        }
        spec_path = tmp / "band_no_label.json"
        out = tmp / "band_no_label.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="band-no-label-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="band-no-label-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            if 'id="band_system"' not in svg:
                FAILURES.append("[band-no-label-render] missing band_system rect")
            if ">system<" in svg:
                FAILURES.append("[band-no-label-render] suppressed band label still rendered")
            run([PY, VALIDATE, str(out)], label="band-no-label-validate")


def test_renderer_escapes_attribute_quotes() -> None:
    """User-controlled ids must not break out of SVG attributes."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        evil_id = 'a"><script>alert(1)</script>'
        spec = {
            "legend": False,
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": evil_id, "domain": "d", "row": 0, "col": 0},
                {"id": "b", "domain": "d", "row": 0, "col": 1},
            ],
            "edges": [{"from": evil_id, "to": "b", "kind": "generic", "width": "bus"}],
        }
        spec_path = tmp / "escaped.json"
        out = tmp / "escaped.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="escape-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="escape-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            if evil_id in svg:
                FAILURES.append("[escape-render] raw quoted id appeared in SVG")
            if 'id="a&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' not in svg:
                FAILURES.append("[escape-render] quoted id was not attribute-escaped")


def test_external_side_hint_controls_endpoint() -> None:
    """`side` lets an edge-placed external block expose its inward-facing port."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "legend": False,
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "core", "domain": "d", "row": 0, "col": 1},
                {"id": "pads", "external": True, "side": "right", "row": 0, "col": 0},
            ],
            "edges": [{"from": "core", "to": "pads", "kind": "generic", "width": "pins"}],
        }
        spec_path = tmp / "side.json"
        out = tmp / "side.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="side-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="side-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            d = _path_d_for(svg, "edge_core_to_pads")
            if d is None or not d.rstrip().endswith("L 36.0,81.0"):
                FAILURES.append(
                    "[side-render] expected edge to terminate on pads left side, "
                    f"got {d!r}"
                )
            run([PY, VALIDATE, str(out)], label="side-validate")


def test_multiline_block_labels_render() -> None:
    """Blocks can supply compact explicit label lines instead of label+sublabel."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "legend": False,
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {
                    "id": "x",
                    "domain": "d",
                    "row": 0,
                    "col": 0,
                    "lines": ["Main TL-UL", "Xbar", "system fabric"],
                }
            ],
            "edges": [],
        }
        spec_path = tmp / "lines.json"
        out = tmp / "lines.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="lines-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="lines-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            for needle in ("Main TL-UL", "Xbar", "system fabric"):
                if needle not in svg:
                    FAILURES.append(f"[lines-render] missing {needle!r}")
            run([PY, VALIDATE, str(out)], label="lines-validate")


def test_renderer_soc_sample() -> None:
    """The bundled full-SoC sample renders and validates clean in both themes."""
    with _tmpdir() as tmp:
        out_light = Path(tmp) / "soc.svg"
        out_dark = Path(tmp) / "soc_dark.svg"
        run([PY, VALIDATE_SPEC, "test_spec_soc.json"], label="soc-sample-spec")
        run([PY, RENDER, "test_spec_soc.json", str(out_light)],
            label="soc-sample-light")
        if out_light.is_file():
            _assert_rendered_spec_content(out_light, "test_spec_soc.json", "soc-sample-light-content")
            run([PY, VALIDATE, str(out_light)], label="soc-sample-light-validate")
        run([PY, RENDER, "--theme", "dark", "test_spec_soc.json", str(out_dark)],
            label="soc-sample-dark")
        if out_dark.is_file():
            _assert_rendered_spec_content(out_dark, "test_spec_soc.json", "soc-sample-dark-content")
            run([PY, VALIDATE, str(out_dark)], label="soc-sample-dark-validate")


def test_renderer_opentitan_sample() -> None:
    """The bundled OpenTitan sample renders and validates clean in both themes."""
    with _tmpdir() as tmp:
        out_light = Path(tmp) / "opentitan.svg"
        out_dark = Path(tmp) / "opentitan_dark.svg"
        run([PY, VALIDATE_SPEC, "test_spec_opentitan.json"], label="opentitan-sample-spec")
        run([PY, RENDER, "test_spec_opentitan.json", str(out_light)],
            label="opentitan-sample-light")
        if out_light.is_file():
            _assert_rendered_spec_content(out_light, "test_spec_opentitan.json", "opentitan-sample-light-content")
            run([PY, VALIDATE, str(out_light)], label="opentitan-sample-light-validate")
        run([PY, RENDER, "--theme", "dark", "test_spec_opentitan.json", str(out_dark)],
            label="opentitan-sample-dark")
        if out_dark.is_file():
            _assert_rendered_spec_content(out_dark, "test_spec_opentitan.json", "opentitan-sample-dark-content")
            run([PY, VALIDATE, str(out_dark)], label="opentitan-sample-dark-validate")


def test_renderer_opentitan_depth2_sample() -> None:
    """The bundled OpenTitan depth-2 sample validates clean in both themes."""
    with _tmpdir() as tmp:
        out_light = Path(tmp) / "opentitan_depth2.svg"
        out_dark = Path(tmp) / "opentitan_depth2_dark.svg"
        run([PY, VALIDATE_SPEC, "test_spec_opentitan_depth2.json"],
            label="opentitan-depth2-sample-spec")
        run([PY, RENDER, "test_spec_opentitan_depth2.json", str(out_light)],
            label="opentitan-depth2-sample-light")
        if out_light.is_file():
            _assert_rendered_spec_content(
                out_light,
                "test_spec_opentitan_depth2.json",
                "opentitan-depth2-sample-light-content",
            )
            run([PY, VALIDATE, str(out_light)],
                label="opentitan-depth2-sample-light-validate")
        run([PY, RENDER, "--theme", "dark", "test_spec_opentitan_depth2.json", str(out_dark)],
            label="opentitan-depth2-sample-dark")
        if out_dark.is_file():
            _assert_rendered_spec_content(
                out_dark,
                "test_spec_opentitan_depth2.json",
                "opentitan-depth2-sample-dark-content",
            )
            run([PY, VALIDATE, str(out_dark)],
                label="opentitan-depth2-sample-dark-validate")


def test_renderer_lanes_sample() -> None:
    """The bundled lanes sample renders and validates clean in both themes."""
    with _tmpdir() as tmp:
        out_light = Path(tmp) / "lanes.svg"
        out_dark = Path(tmp) / "lanes_dark.svg"
        run([PY, VALIDATE_SPEC, "test_spec_lanes.json"], label="lanes-sample-spec")
        run([PY, RENDER, "test_spec_lanes.json", str(out_light)],
            label="lanes-sample-light")
        if out_light.is_file():
            _assert_rendered_spec_content(out_light, "test_spec_lanes.json", "lanes-sample-light-content")
            run([PY, VALIDATE, str(out_light)], label="lanes-sample-light-validate")
        run([PY, RENDER, "--theme", "dark", "test_spec_lanes.json", str(out_dark)],
            label="lanes-sample-dark")
        if out_dark.is_file():
            _assert_rendered_spec_content(out_dark, "test_spec_lanes.json", "lanes-sample-dark-content")
            run([PY, VALIDATE, str(out_dark)], label="lanes-sample-dark-validate")


def test_groups_render_and_skip_geometry() -> None:
    """A spec with `groups` and `group` block fields must validate, render a
    dashed `group_*` rect with a header label, and the SVG geometry validator
    must NOT treat the dashed container as a crossing-eligible block."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "title": "groups test",
            "groups": {"core": {"label": "core pipeline"}},
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "a", "domain": "d", "row": 0, "col": 0, "group": "core"},
                {"id": "b", "domain": "d", "row": 0, "col": 1, "group": "core"},
                {"id": "c", "domain": "d", "row": 0, "col": 2, "group": "core"},
            ],
            "edges": [
                {"from": "a", "to": "b", "kind": "axi-mm", "width": 64},
                {"from": "b", "to": "c", "kind": "axi-mm", "width": 64},
            ],
        }
        spec_path = tmp / "groups.json"
        out = tmp / "groups.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="groups-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="groups-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            if 'id="group_core"' not in svg:
                FAILURES.append("[groups-render] missing group_core rect in SVG")
            if "CORE PIPELINE" not in svg:
                FAILURES.append("[groups-render] missing CORE PIPELINE label in SVG")
            if 'stroke-dasharray="6,4"' not in svg:
                FAILURES.append("[groups-render] group rect not dashed")
            run([PY, VALIDATE, str(out)], label="groups-validate")


def test_spec_validator_rejects_unknown_group_ref() -> None:
    """A block referencing a group that isn't declared must fail spec validation."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "groups": {"a": {}},
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [{"id": "x", "domain": "d", "row": 0, "col": 0, "group": "ghost"}],
            "edges": [],
        }
        _write_and_check(tmp, "bad_group.json", spec, 1, "spec-unknown-group-ref")


def test_renderer_depth2_sample() -> None:
    """The depth-2 sample spec must validate, render, and pass geometry checks
    in both themes. It's wider/taller than the depth-1 sample and exercises
    cross-row routing under tight lane assignment."""
    with _tmpdir() as tmp:
        out_light = Path(tmp) / "depth2.svg"
        out_dark = Path(tmp) / "depth2_dark.svg"
        run([PY, VALIDATE_SPEC, "test_spec_depth2.json"], label="depth2-spec")
        run([PY, RENDER, "test_spec_depth2.json", str(out_light)], label="depth2-light")
        if out_light.is_file():
            _assert_rendered_spec_content(out_light, "test_spec_depth2.json", "depth2-light-content")
            run([PY, VALIDATE, str(out_light)], label="depth2-light-validate")
        run([PY, RENDER, "--theme", "dark", "test_spec_depth2.json", str(out_dark)],
            label="depth2-dark")
        if out_dark.is_file():
            _assert_rendered_spec_content(out_dark, "test_spec_depth2.json", "depth2-dark-content")
            run([PY, VALIDATE, str(out_dark)], label="depth2-dark-validate")


def test_renderer_dark() -> None:
    with _tmpdir() as tmp:
        out = Path(tmp) / "out_dark.svg"
        run(
            [PY, RENDER, "--theme", "dark", "test_spec.json", str(out)],
            label="render-dark",
        )
        if out.is_file():
            _assert_rendered_spec_content(out, "test_spec.json", "render-dark-content")
            run([PY, VALIDATE, str(out)], label="validate-dark-output")


def test_renderer_omits_unknown_clock_frequency() -> None:
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "domains": {"axi": {"color": "#42A5F5"}},
            "blocks": [{"id": "a", "domain": "axi", "row": 0, "col": 0}],
            "edges": [],
        }
        spec_path = tmp / "unknown_freq.json"
        out = tmp / "unknown_freq.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="unknown-freq-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="unknown-freq-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            if "? MHz" in svg:
                FAILURES.append("[unknown-freq-render] rendered placeholder '? MHz'")
            if ">axi<" not in svg:
                FAILURES.append("[unknown-freq-render] missing plain domain label 'axi'")


def test_renderer_routes_same_row_reverse_edges_in_gutter() -> None:
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "domains": {"axi": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "dst", "label": "Dst", "domain": "axi", "row": 0, "col": 0},
                {"id": "mid", "label": "Middle", "domain": "axi", "row": 0, "col": 1},
                {"id": "src", "label": "Src", "domain": "axi", "row": 0, "col": 2},
            ],
            "edges": [{"from": "src", "to": "dst", "kind": "generic", "width": 32}],
        }
        spec_path = tmp / "same_row_reverse.json"
        out = tmp / "same_row_reverse.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="same-row-reverse-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="same-row-reverse-render")
        if out.is_file():
            run([PY, VALIDATE, str(out)], label="same-row-reverse-validate")
            svg = out.read_text(encoding="utf-8")
            if "L 656.0,161.0 L 316.0,161.0" not in svg:
                FAILURES.append("[same-row-reverse-render] missing row-gutter lane segment")


def test_spec_validator_accepts_route_and_label() -> None:
    """Well-formed route/label objects must pass spec validation."""
    with _tmpdir() as tmp:
        good = Path(tmp) / "good_route_label.json"
        good.write_text(
            json.dumps({
                "domains": {"d": {"color": "#42A5F5"}},
                "blocks": [
                    {"id": "a", "domain": "d", "row": 0, "col": 0},
                    {"id": "b", "domain": "d", "row": 0, "col": 1},
                    {"id": "c", "domain": "d", "row": 1, "col": 1},
                ],
                "edges": [
                    {"from": "a", "to": "b", "kind": "generic",
                     "route": {"mode": "direct"},
                     "label": {"dx": -20, "dy": 10, "segment": 0, "t": 0.42}},
                    {"from": "a", "to": "c", "kind": "generic",
                     "route": {"points": [[10, 20], [30, 20], [30, 40]]}},
                ],
            }),
            encoding="utf-8",
        )
        run([PY, VALIDATE_SPEC, str(good)], label="spec-route-label-pass")


def test_spec_validator_rejects_bad_route_label() -> None:
    """Malformed route/label objects must fail spec validation."""
    cases = [
        ({"route": {"mode": "diagonal"}}, "spec-route-bad-mode"),
        ({"route": {"points": [[1, 2]]}}, "spec-route-too-few-points"),
        ({"route": {"points": [[1, 2], [3, "x"]]}}, "spec-route-non-numeric"),
        ({"route": {"unknown": 1}}, "spec-route-unknown-field"),
        ({"label": {"dx": "10"}}, "spec-label-bad-dx"),
        ({"label": {"segment": 1.5}}, "spec-label-bad-segment"),
        ({"label": {"t": 1.5}}, "spec-label-t-out-of-range"),
        ({"label": {"weird": 0}}, "spec-label-unknown-field"),
    ]
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        for extra, label in cases:
            spec = {
                "domains": {"d": {"color": "#42A5F5"}},
                "blocks": [
                    {"id": "a", "domain": "d", "row": 0, "col": 0},
                    {"id": "b", "domain": "d", "row": 0, "col": 1},
                ],
                "edges": [{"from": "a", "to": "b", "kind": "generic", **extra}],
            }
            _write_and_check(tmp, f"{label}.json", spec, 1, label)


def test_spec_validator_accepts_block_size_overrides() -> None:
    """Blocks may override the diagram-wide cell size with positive w/h ints."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        good = _spec_with(w=160, h=64)
        _write_and_check(tmp, "block_size_good.json", good, 0, "spec-block-size-good")
        bad_w = _spec_with(w=0)
        _write_and_check(tmp, "block_size_bad_w.json", bad_w, 1, "spec-block-size-bad-w")
        bad_h = _spec_with(h=True)
        _write_and_check(tmp, "block_size_bad_h.json", bad_h, 1, "spec-block-size-bad-h")


def test_renderer_honors_explicit_route_points() -> None:
    """When route.points is set, the rendered <path> must use those exact coords."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        pts = [[123, 234], [123, 456], [567, 456]]
        spec = {
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "a", "domain": "d", "row": 0, "col": 0},
                {"id": "b", "domain": "d", "row": 0, "col": 2},
            ],
            "edges": [
                {"from": "a", "to": "b", "kind": "generic", "width": "bus",
                 "route": {"points": pts},
                 "label": {"dx": -20, "dy": 10}},
            ],
        }
        spec_path = tmp / "explicit_route.json"
        out = tmp / "explicit_route.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="explicit-route-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="explicit-route-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            expected = "M 123.0,234.0 L 123.0,456.0 L 567.0,456.0"
            if expected not in svg:
                FAILURES.append(
                    "[explicit-route-render] rendered <path> does not contain "
                    f"the explicit waypoints: missing '{expected}'"
                )


def test_renderer_honors_block_size_overrides() -> None:
    """A block-level w/h override changes only that block's rendered rect."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "small", "label": "Small", "domain": "d",
                 "row": 0, "col": 0, "w": 140, "h": 54},
                {"id": "normal", "label": "Normal", "domain": "d",
                 "row": 0, "col": 1},
            ],
            "edges": [{"from": "small", "to": "normal", "kind": "generic", "width": "bus"}],
        }
        spec_path = tmp / "block_size.json"
        out = tmp / "block_size.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="block-size-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="block-size-render")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            if 'id="small" x="36" y="36" width="140" height="54"' not in svg:
                FAILURES.append("[block-size-render] small rect did not use w=140 h=54")
            if 'id="normal" x="376" y="36" width="220" height="90"' not in svg:
                FAILURES.append("[block-size-render] normal rect did not keep grid size")
            run([PY, VALIDATE, str(out)], label="block-size-validate")


def _path_d_for(svg: str, edge_id: str):
    import re
    m = re.search(rf'id="{re.escape(edge_id)}" d="([^"]+)"', svg)
    return m.group(1) if m else None


def _segments_from_d(d: str):
    """Parse 'M x,y L x,y L x,y ...' into a list of ((x1,y1),(x2,y2)) segments."""
    import re
    nums = [float(n) for n in re.findall(r"-?\d+\.?\d*", d)]
    pts = list(zip(nums[0::2], nums[1::2]))
    return list(zip(pts, pts[1:]))


def test_renderer_lane_assignment_ignores_edge_order() -> None:
    """Reordering edge entries should not move endpoints or lane offsets."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        base = {
            "legend": False,
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "a", "domain": "d", "row": 0, "col": 0},
                {"id": "b", "domain": "d", "row": 1, "col": 0},
                {"id": "c", "domain": "d", "row": 0, "col": 2},
                {"id": "d", "domain": "d", "row": 1, "col": 2},
            ],
            "edges": [
                {"from": "a", "to": "d", "kind": "generic", "width": "bus"},
                {"from": "b", "to": "c", "kind": "generic", "width": "bus"},
            ],
        }
        rev = json.loads(json.dumps(base))
        rev["edges"] = list(reversed(rev["edges"]))
        paths_by_spec = []
        for name, spec in (("base", base), ("rev", rev)):
            spec_path = tmp / f"{name}.json"
            out = tmp / f"{name}.svg"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            run([PY, VALIDATE_SPEC, str(spec_path)], label=f"lane-order-{name}-spec")
            run([PY, RENDER, str(spec_path), str(out)], label=f"lane-order-{name}-render")
            svg = out.read_text(encoding="utf-8")
            paths_by_spec.append({
                f'edge_{e["from"]}_to_{e["to"]}': _path_d_for(svg, f'edge_{e["from"]}_to_{e["to"]}')
                for e in base["edges"]
            })
        if paths_by_spec[0] != paths_by_spec[1]:
            FAILURES.append(
                f"[lane-order] edge order changed routing: {paths_by_spec!r}"
            )


def test_renderer_direct_mode_is_orthogonal() -> None:
    """route.mode='direct' must never emit a diagonal segment, even between
    blocks at different rows and columns."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "a", "domain": "d", "row": 0, "col": 0},
                {"id": "b", "domain": "d", "row": 1, "col": 1},
            ],
            "edges": [
                {"from": "a", "to": "b", "kind": "generic", "width": "x",
                 "route": {"mode": "direct"}},
            ],
        }
        spec_path = tmp / "direct.json"
        out = tmp / "direct.svg"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        run([PY, VALIDATE_SPEC, str(spec_path)], label="direct-route-spec")
        run([PY, RENDER, str(spec_path), str(out)], label="direct-route-render")
        run([PY, VALIDATE, str(out)], label="direct-route-validate")
        if out.is_file():
            svg = out.read_text(encoding="utf-8")
            d = _path_d_for(svg, "edge_a_to_b")
            if d is None:
                FAILURES.append("[direct-route-render] could not find edge path")
            else:
                for (x1, y1), (x2, y2) in _segments_from_d(d):
                    if abs(x1 - x2) > 0.5 and abs(y1 - y2) > 0.5:
                        FAILURES.append(
                            f"[direct-route-render] diagonal segment "
                            f"({x1},{y1})->({x2},{y2}) in path: {d!r}"
                        )
                        break


def test_spec_validator_rejects_diagonal_route_points() -> None:
    """route.points must be axis-aligned pairwise; diagonals are banned."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        spec = {
            "domains": {"d": {"color": "#42A5F5"}},
            "blocks": [
                {"id": "a", "domain": "d", "row": 0, "col": 0},
                {"id": "b", "domain": "d", "row": 0, "col": 1},
            ],
            "edges": [
                {"from": "a", "to": "b", "kind": "generic",
                 "route": {"points": [[10, 20], [30, 40]]}},
            ],
        }
        _write_and_check(tmp, "diag.json", spec, 1, "spec-route-diagonal")


def test_validator_flags_diagonal_segment_in_svg() -> None:
    """A hand-crafted SVG with a diagonal arrow must trigger DIAGONAL."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200" '
            'viewBox="0 0 400 200">\n'
            '  <rect id="a" x="10"  y="10"  width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <rect id="b" x="300" y="140" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <path id="diag" d="M 90,30 L 300,160" stroke="#000" '
            'stroke-width="2" fill="none"/>\n'
            '</svg>\n'
        )
        out = tmp / "diag.svg"
        out.write_text(svg, encoding="utf-8")
        # BITWIDTH also fires, and the diagonal enters/exits both blocks without
        # perpendicular stubs; total = 4.
        proc = run([PY, VALIDATE, str(out)], expect_rc=4, label="validate-diagonal")
        if "DIAGONAL" not in proc.stdout:
            FAILURES.append(
                f"[validate-diagonal] expected DIAGONAL in report, "
                f"got: {proc.stdout.strip()!r}"
            )


def test_validator_flags_tangential_block_exit() -> None:
    """A block-attached arrow must leave perpendicular to the touched side."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="160" '
            'viewBox="0 0 420 160">\n'
            '  <rect id="a" x="10"  y="10" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <rect id="b" x="300" y="70" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <path id="bad_port" d="M 90,30 L 90,90 L 300,90" stroke="#000" '
            'stroke-width="2" fill="none"/>\n'
            '  <text x="195" y="86" text-anchor="middle" font-size="12">label</text>\n'
            '</svg>\n'
        )
        out = tmp / "bad_port.svg"
        out.write_text(svg, encoding="utf-8")
        proc = run([PY, VALIDATE, str(out)], expect_rc=1, label="validate-perpendicular")
        if "PERPENDICULAR" not in proc.stdout:
            FAILURES.append(
                f"[validate-perpendicular] expected PERPENDICULAR in report, "
                f"got: {proc.stdout.strip()!r}"
            )


def test_validator_reports_perpendicular_on_named_endpoint_block() -> None:
    """Shared endpoints should diagnose the actual edge block, not a neighbor."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="160" '
            'viewBox="0 0 420 160">\n'
            '  <rect id="neighbor" x="90" y="20" width="50" height="80" fill="#66BB6A" '
            'stroke="#1B5E20"/>\n'
            '  <rect id="src" x="10" y="40" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <rect id="dst" x="300" y="80" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <path id="edge_src_to_dst" d="M 90,60 L 90,100 L 300,100" stroke="#000" '
            'stroke-width="2" fill="none"/>\n'
            '  <text x="195" y="96" text-anchor="middle" font-size="12">bus</text>\n'
            '</svg>\n'
        )
        out = tmp / "wrong_block.svg"
        out.write_text(svg, encoding="utf-8")
        proc = run([PY, VALIDATE, str(out)], expect_rc=1, label="validate-perpendicular-named-block")
        if "PERPENDICULAR" not in proc.stdout or "block 'src'" not in proc.stdout:
            FAILURES.append(
                f"[validate-perpendicular-named-block] expected src PERPENDICULAR, "
                f"got: {proc.stdout.strip()!r}"
            )
        if "block 'neighbor'" in proc.stdout:
            FAILURES.append(
                f"[validate-perpendicular-named-block] diagnosed neighbor, "
                f"got: {proc.stdout.strip()!r}"
            )


def test_validator_flags_floating_endpoint() -> None:
    """An arrow endpoint must sit exactly on a block boundary."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="120" '
            'viewBox="0 0 420 120">\n'
            '  <rect id="a" x="10" y="40" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <rect id="b" x="300" y="40" width="80" height="40" fill="#66BB6A" '
            'stroke="#1B5E20"/>\n'
            '  <path id="floating" d="M 90,60 L 250,60" stroke="#000" '
            'stroke-width="2" fill="none"/>\n'
            '  <text x="170" y="56" text-anchor="middle" font-size="12">bus</text>\n'
            '</svg>\n'
        )
        out = tmp / "floating.svg"
        out.write_text(svg, encoding="utf-8")
        proc = run([PY, VALIDATE, str(out)], expect_rc=1, label="validate-floating-endpoint")
        if "ENDPOINT" not in proc.stdout:
            FAILURES.append(
                f"[validate-floating-endpoint] expected ENDPOINT in report, "
                f"got: {proc.stdout.strip()!r}"
            )


def test_validator_flags_overlapping_arrow_segments() -> None:
    """Two arrows must not share the exact same visible route segment."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="120" '
            'viewBox="0 0 420 120">\n'
            '  <rect id="a" x="10" y="40" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <rect id="b" x="300" y="40" width="80" height="40" fill="#66BB6A" '
            'stroke="#1B5E20"/>\n'
            '  <path id="first" d="M 90,60 L 300,60" stroke="#000" '
            'stroke-width="2" fill="none"/>\n'
            '  <path id="second" d="M 90,60 L 300,60" stroke="#00796B" '
            'stroke-width="2" fill="none"/>\n'
            '  <text x="195" y="56" text-anchor="middle" font-size="12">32b</text>\n'
            '</svg>\n'
        )
        out = tmp / "arrow_overlap.svg"
        out.write_text(svg, encoding="utf-8")
        proc = run([PY, VALIDATE, str(out)], expect_rc=4, label="validate-arrow-overlap")
        if "OVERLAP" not in proc.stdout:
            FAILURES.append(
                f"[validate-arrow-overlap] expected OVERLAP in report, "
                f"got: {proc.stdout.strip()!r}"
            )


def test_validator_flags_unnecessary_loop() -> None:
    """A U-shaped route is a loop when a clear direct segment exists."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="140" '
            'viewBox="0 0 420 140">\n'
            '  <rect id="a" x="10"  y="40" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <rect id="b" x="300" y="40" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <path id="loop" d="M 90,60 L 120,60 L 120,100 L 270,100 L 270,60 L 300,60" '
            'stroke="#000" stroke-width="2" fill="none"/>\n'
            '  <text x="195" y="96" text-anchor="middle" font-size="12">bus</text>\n'
            '</svg>\n'
        )
        out = tmp / "loop.svg"
        out.write_text(svg, encoding="utf-8")
        proc = run([PY, VALIDATE, str(out)], expect_rc=1, label="validate-loop")
        if "LOOP" not in proc.stdout:
            FAILURES.append(
                f"[validate-loop] expected LOOP in report, "
                f"got: {proc.stdout.strip()!r}"
            )


def test_validator_flags_overlapping_text() -> None:
    """Two visible labels drawn on top of each other must trigger TEXT_TEXT."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="120" '
            'viewBox="0 0 320 120">\n'
            '  <text x="20" y="40" font-size="16">secure services</text>\n'
            '  <text x="22" y="42" font-size="16">SECURE SERVICES</text>\n'
            '</svg>\n'
        )
        out = tmp / "text_overlap.svg"
        out.write_text(svg, encoding="utf-8")
        proc = run([PY, VALIDATE, str(out)], expect_rc=1, label="validate-text-text")
        if "TEXT_TEXT" not in proc.stdout:
            FAILURES.append(
                f"[validate-text-text] expected TEXT_TEXT in report, "
                f"got: {proc.stdout.strip()!r}"
            )


def test_validator_allows_detour_around_block() -> None:
    """A U-shaped route is allowed when a block blocks the direct segment."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="460" height="160" '
            'viewBox="0 0 460 160">\n'
            '  <rect id="a" x="10"  y="40" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <rect id="mid" x="180" y="35" width="80" height="50" fill="#66BB6A" '
            'stroke="#1B5E20"/>\n'
            '  <rect id="b" x="340" y="40" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <path id="detour" d="M 90,60 L 120,60 L 120,120 L 310,120 L 310,60 L 340,60" '
            'stroke="#000" stroke-width="2" fill="none"/>\n'
            '  <text x="215" y="116" text-anchor="middle" font-size="12">bus</text>\n'
            '</svg>\n'
        )
        out = tmp / "detour.svg"
        out.write_text(svg, encoding="utf-8")
        proc = run([PY, VALIDATE, str(out)], expect_rc=0, label="validate-detour")
        if "LOOP" in proc.stdout:
            FAILURES.append(
                f"[validate-detour] did not expect LOOP in report, "
                f"got: {proc.stdout.strip()!r}"
            )


def test_validator_allows_detour_around_text() -> None:
    """A dogleg is allowed when the direct route would pierce a label."""
    with _tmpdir() as tmp:
        tmp = Path(tmp)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="140" '
            'viewBox="0 0 420 140">\n'
            '  <rect id="a" x="10"  y="40" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <rect id="b" x="300" y="40" width="80" height="40" fill="#42A5F5" '
            'stroke="#0D47A1"/>\n'
            '  <text x="195" y="64" text-anchor="middle" font-size="12">reserved label</text>\n'
            '  <path id="detour" d="M 90,60 L 120,60 L 120,100 L 270,100 L 270,60 L 300,60" '
            'stroke="#000" stroke-width="2" fill="none"/>\n'
            '  <text x="195" y="96" text-anchor="middle" font-size="12">bus</text>\n'
            '</svg>\n'
        )
        out = tmp / "detour_text.svg"
        out.write_text(svg, encoding="utf-8")
        proc = run([PY, VALIDATE, str(out)], expect_rc=0, label="validate-detour-text")
        if "LOOP" in proc.stdout:
            FAILURES.append(
                f"[validate-detour-text] did not expect LOOP in report, "
                f"got: {proc.stdout.strip()!r}"
            )


def test_install() -> None:
    with _tmpdir() as tmp:
        dst = Path(tmp) / "skill-install"
        run([PY, "install.py", "--dst", str(dst)], label="install")
        runtime_paths = (
            "SKILL.md",
            "LICENSE",
            "agents/openai.yaml",
            "assets/hdldiagzero-small.svg",
            "render.py",
            "validate.py",
            "validate_spec.py",
            "references/schema.md",
            "references/extraction.md",
            "references/validation.md",
        )
        for f in runtime_paths:
            if not (dst / f).is_file():
                FAILURES.append(f"[install] missing {f} in {dst}")
        # README / tests / fixtures should NOT have been installed.
        for f in (
            "README.md",
            "tests.py",
            "not_sample_broken_validator_fixture.svg",
            "test_spec.json",
        ):
            if (dst / f).exists():
                FAILURES.append(f"[install] unexpectedly copied {f} into runtime dir")


def main() -> int:
    test_validator_fixture()
    test_spec_validator_passes_on_test_spec()
    test_spec_validator_catches_bad_spec()
    test_spec_validator_strict_types()
    test_spec_validator_rejects_extraction_metadata()
    test_renderer_light()
    test_renderer_dark()
    test_groups_render_and_skip_geometry()
    test_spec_validator_rejects_unknown_group_ref()
    test_lanes_render_and_skip_geometry()
    test_lanes_column_orientation()
    test_spec_validator_rejects_lane_with_both_rows_and_cols()
    test_spec_validator_rejects_unknown_lane_domain()
    test_legend_card_renders_top_right()
    test_renderer_can_hide_legend()
    test_bands_render_and_skip_geometry()
    test_band_label_can_be_suppressed()
    test_renderer_escapes_attribute_quotes()
    test_external_side_hint_controls_endpoint()
    test_multiline_block_labels_render()
    test_renderer_lanes_sample()
    test_renderer_soc_sample()
    test_renderer_opentitan_sample()
    test_renderer_opentitan_depth2_sample()
    test_renderer_depth2_sample()
    test_renderer_omits_unknown_clock_frequency()
    test_renderer_routes_same_row_reverse_edges_in_gutter()
    test_spec_validator_accepts_route_and_label()
    test_spec_validator_rejects_bad_route_label()
    test_renderer_honors_explicit_route_points()
    test_spec_validator_accepts_block_size_overrides()
    test_renderer_honors_block_size_overrides()
    test_renderer_lane_assignment_ignores_edge_order()
    test_renderer_direct_mode_is_orthogonal()
    test_spec_validator_rejects_diagonal_route_points()
    test_validator_flags_diagonal_segment_in_svg()
    test_validator_flags_tangential_block_exit()
    test_validator_reports_perpendicular_on_named_endpoint_block()
    test_validator_flags_floating_endpoint()
    test_validator_flags_overlapping_arrow_segments()
    test_validator_flags_unnecessary_loop()
    test_validator_flags_overlapping_text()
    test_validator_allows_detour_around_block()
    test_validator_allows_detour_around_text()
    test_install()

    if FAILURES:
        print("FAIL")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
