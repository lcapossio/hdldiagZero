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
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_DIR = ROOT / "skills" / "hdldiagzero"
RENDER = str(SKILL_DIR / "render.py")
VALIDATE = str(SKILL_DIR / "validate.py")
VALIDATE_SPEC = str(SKILL_DIR / "validate_spec.py")
PY = sys.executable


def _tmpdir():
    """Make a temp directory we can actually write to.

    System temp is sometimes locked down (corporate Windows, sandboxed
    runners). Honor HDLDIAG_TEST_TMP if set; otherwise fall back to a
    repo-local <root>/tmp/ which is gitignored. Both paths are guaranteed
    to be created if missing."""
    base = Path(os.environ.get("HDLDIAG_TEST_TMP") or (ROOT / "tmp"))
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=str(base), ignore_cleanup_errors=True)

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
        # The arrow has no nearby text label so BITWIDTH also fires; total = 2.
        proc = run([PY, VALIDATE, str(out)], expect_rc=2, label="validate-diagonal")
        if "DIAGONAL" not in proc.stdout:
            FAILURES.append(
                f"[validate-diagonal] expected DIAGONAL in report, "
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
    test_renderer_omits_unknown_clock_frequency()
    test_renderer_routes_same_row_reverse_edges_in_gutter()
    test_spec_validator_accepts_route_and_label()
    test_spec_validator_rejects_bad_route_label()
    test_renderer_honors_explicit_route_points()
    test_renderer_direct_mode_is_orthogonal()
    test_spec_validator_rejects_diagonal_route_points()
    test_validator_flags_diagonal_segment_in_svg()
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
