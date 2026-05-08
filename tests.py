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
        [PY, "validate.py", "not_sample_broken_validator_fixture.svg"],
        expect_rc=EXPECTED_FIXTURE_VIOLATIONS,
        label="validator-fixture",
    )


def test_spec_validator_passes_on_test_spec() -> None:
    run([PY, "validate_spec.py", "test_spec.json"], label="spec-validator-pass")


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
        run([PY, "validate_spec.py", str(bad)], expect_rc=1, label="spec-validator-fail")


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
    run([PY, "validate_spec.py", str(p)], expect_rc=expect_rc, label=label)


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
        run([PY, "render.py", "test_spec.json", str(out)], label="render-light")
        if out.is_file():
            run([PY, "validate.py", str(out)], label="validate-light-output")


def test_renderer_dark() -> None:
    with _tmpdir() as tmp:
        out = Path(tmp) / "out_dark.svg"
        run(
            [PY, "render.py", "--theme", "dark", "test_spec.json", str(out)],
            label="render-dark",
        )
        if out.is_file():
            run([PY, "validate.py", str(out)], label="validate-dark-output")


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
        run([PY, "validate_spec.py", str(spec_path)], label="unknown-freq-spec")
        run([PY, "render.py", str(spec_path), str(out)], label="unknown-freq-render")
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
        run([PY, "validate_spec.py", str(spec_path)], label="same-row-reverse-spec")
        run([PY, "render.py", str(spec_path), str(out)], label="same-row-reverse-render")
        if out.is_file():
            run([PY, "validate.py", str(out)], label="same-row-reverse-validate")
            svg = out.read_text(encoding="utf-8")
            if "L 611.0,161.0 L 301.0,161.0" not in svg:
                FAILURES.append("[same-row-reverse-render] missing row-gutter lane segment")


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
