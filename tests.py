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

# The bundled fixture deliberately contains exactly this many violations, one
# of each rule plus the two PORT collisions. If the renderer or the validator
# regresses, this number changes and CI fails.
EXPECTED_FIXTURE_VIOLATIONS = 8

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


def test_renderer_light() -> None:
    with _tmpdir() as tmp:
        out = Path(tmp) / "out.svg"
        run([PY, RENDER, "test_spec.json", str(out)], label="render-light")
        if out.is_file():
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
                "phy":  {"cols": [1]},
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


def test_renderer_soc_sample() -> None:
    """The bundled full-SoC sample renders and validates clean in both themes."""
    with _tmpdir() as tmp:
        out_light = Path(tmp) / "soc.svg"
        out_dark = Path(tmp) / "soc_dark.svg"
        run([PY, VALIDATE_SPEC, "test_spec_soc.json"], label="soc-sample-spec")
        run([PY, RENDER, "test_spec_soc.json", str(out_light)],
            label="soc-sample-light")
        if out_light.is_file():
            run([PY, VALIDATE, str(out_light)], label="soc-sample-light-validate")
        run([PY, RENDER, "--theme", "dark", "test_spec_soc.json", str(out_dark)],
            label="soc-sample-dark")
        if out_dark.is_file():
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
            run([PY, VALIDATE, str(out_light)], label="opentitan-sample-light-validate")
        run([PY, RENDER, "--theme", "dark", "test_spec_opentitan.json", str(out_dark)],
            label="opentitan-sample-dark")
        if out_dark.is_file():
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
            run([PY, VALIDATE, str(out_light)],
                label="opentitan-depth2-sample-light-validate")
        run([PY, RENDER, "--theme", "dark", "test_spec_opentitan_depth2.json", str(out_dark)],
            label="opentitan-depth2-sample-dark")
        if out_dark.is_file():
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
            run([PY, VALIDATE, str(out_light)], label="lanes-sample-light-validate")
        run([PY, RENDER, "--theme", "dark", "test_spec_lanes.json", str(out_dark)],
            label="lanes-sample-dark")
        if out_dark.is_file():
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
            run([PY, VALIDATE, str(out_light)], label="depth2-light-validate")
        run([PY, RENDER, "--theme", "dark", "test_spec_depth2.json", str(out_dark)],
            label="depth2-dark")
        if out_dark.is_file():
            run([PY, VALIDATE, str(out_dark)], label="depth2-dark-validate")


def test_renderer_dark() -> None:
    with _tmpdir() as tmp:
        out = Path(tmp) / "out_dark.svg"
        run(
            [PY, RENDER, "--theme", "dark", "test_spec.json", str(out)],
            label="render-dark",
        )
        if out.is_file():
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
                ],
                "edges": [
                    {"from": "a", "to": "b", "kind": "generic",
                     "route": {"mode": "direct"},
                     "label": {"dx": -20, "dy": 10, "segment": 0, "t": 0.42}},
                    {"from": "a", "to": "b", "kind": "generic",
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
    test_renderer_light()
    test_renderer_dark()
    test_groups_render_and_skip_geometry()
    test_spec_validator_rejects_unknown_group_ref()
    test_lanes_render_and_skip_geometry()
    test_lanes_column_orientation()
    test_spec_validator_rejects_lane_with_both_rows_and_cols()
    test_spec_validator_rejects_unknown_lane_domain()
    test_legend_card_renders_top_right()
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
    test_renderer_direct_mode_is_orthogonal()
    test_spec_validator_rejects_diagonal_route_points()
    test_validator_flags_diagonal_segment_in_svg()
    test_validator_flags_tangential_block_exit()
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
