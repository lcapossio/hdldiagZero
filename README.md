# hdldiagZero

An agent skill that turns an HDL / RTL / SoC architecture description into a clean SVG block diagram. Color-codes blocks by clock domain, distinguishes AXI variants, draws CDC blocks with a split fill, omits clock / reset / JTAG / debug clutter by default, and validates the output geometry so lines never pass through blocks.

The skill itself is runtime-neutral — `SKILL.md`, `render.py`, `validate.py`, `validate_spec.py`, and `references/` make no assumption about which agent harness loads them. `install.py` has built-in defaults for Claude Code (`~/.claude/skills/hdldiagzero/`) and Codex (`~/.codex/skills/hdldiagzero/`); pass `--dst` for a custom runtime path.

## Sample Output

Generated from [test_spec.json](test_spec.json):

Light mode:

<a href="sample_output.svg">
  <img src="sample_output.svg" alt="Sample hdldiagZero SVG output">
</a>

Dark mode:

<a href="sample_output_dark.svg">
  <img src="sample_output_dark.svg" alt="Sample hdldiagZero dark-mode SVG output">
</a>

## Index

- [Sample Output](#sample-output)
- [Features](#features)
- [Files](#files)
- [Install](#install)
- [Usage](#usage)
- [Testing](#testing)
- [Author](#author)
- [License](#license)

## Features

- **JSON-spec-driven render**: the agent extracts a small architecture spec; the renderer (`render.py`) produces the SVG. The renderer owns geometry — the agent doesn't pick coordinates.
- **Clock-domain coloring** with a tuned Material-tone palette. Each domain has a separate fill and dark border. CDC blocks (`domain_b: ...`) render with a horizontal-split linear gradient bridging two domains.
- **External / off-chip blocks** (`external: true`) get a neutral grey fill regardless of domain.
- **Edge styles per kind**: `axi-mm`, `axi-lite`, `axi-stream`, `cdc` (purple dashed), `generic`. Distinct strokes and arrowheads, plus a connection-styles legend below the clock-domain legend.
- **Manhattan single-bend routing** with **interval-coloring lane assignment**: parallel edges sharing a gutter that *actually* overlap in y/x get distinct lanes; non-overlapping edges share a lane so labels stay in the gutter midpoint.
- **Row/column gutter detours** for same-row or same-column edges that need to pass around intermediate blocks.
- **WCAG-style text contrast**: block text auto-flips between light and dark by relative-luminance contrast so labels read on every fill, including CDC gradients.
- **Light + dark themes** (`theme: dark` in the JSON or `--theme dark` on the CLI). Dark mode uses pure black canvas with brightened accent colors for arrows, labels, and external blocks.
- **Edge bitwidth labels** at the bend midpoint, with a subtle pill mask so the line doesn't pierce the text.
- **Geometry validator** (`validate.py`) catches line-through-block crossings, parallel-arrow collisions, stub arrows (shaft shorter than arrowhead), labels overlapping foreign blocks, arrows piercing other arrows' labels, multiple endpoints meeting at the same block port, and missing edge labels.

## Files

### Runtime (installed into the skill directory)

| File | Purpose |
| --- | --- |
| [SKILL.md](SKILL.md) | Skill definition consumed by the agent runtime (description, workflow). |
| [LICENSE](LICENSE) | MIT license included with installed runtime files. |
| [agents/openai.yaml](agents/openai.yaml) | Marketplace/UI metadata for skill lists and default prompts. |
| [assets/hdldiagzero-small.svg](assets/hdldiagzero-small.svg) | Small icon used by marketplace/UI metadata. |
| [render.py](render.py) | JSON → SVG renderer. |
| [validate.py](validate.py) | SVG geometry validator (exit code = violation count). |
| [validate_spec.py](validate_spec.py) | JSON spec validator — run before the renderer to catch structural errors. |
| [references/schema.md](references/schema.md) | Full JSON schema, loaded on demand. |
| [references/extraction.md](references/extraction.md) | HDL extraction patterns: top discovery, hierarchy walking, exclusions, AXI classification. |
| [references/validation.md](references/validation.md) | Fix recipes for each validator violation. |

### Repo-only (not copied by `install.py`)

| File | Purpose |
| --- | --- |
| [install.py](install.py) | Copies the runtime files into a skills directory (Claude and Codex defaults, override with `--dst`). |
| [tests.py](tests.py) | Self-tests: validators, renderer light + dark, install dry-run. |
| [test_spec.json](test_spec.json) | Clean renderer smoke-test spec. Render it manually to inspect normal output. |
| [sample_output.svg](sample_output.svg) | Tracked example of normal renderer output generated from `test_spec.json`. |
| [not_sample_broken_validator_fixture.svg](not_sample_broken_validator_fixture.svg) | Intentionally broken validator regression fixture. It is supposed to fail with exactly 8 violations; it is not sample output. |
| [pyproject.toml](pyproject.toml) | Ruff lint config. |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | GitHub Actions: ruff + `python tests.py` on Linux / macOS / Windows × Python 3.10, 3.12. |
| [README.md](README.md) | Repo docs. |

## Install

Requires Python 3.10+. From the repo root:

```
python install.py                                # Claude default: ~/.claude/skills/hdldiagzero
python install.py --runtime codex                # Codex default: ~/.codex/skills/hdldiagzero
python install.py --dst /opt/agent-skills/hdldiagzero
```

Copies `SKILL.md`, `LICENSE`, `agents/openai.yaml`, `assets/`, `render.py`, `validate.py`, `validate_spec.py`, and the `references/*.md` files into the target directory. Restart your agent runtime to pick up the new skill.

## Usage

Once installed, ask the agent something like *"draw the top-level RTL"* in any HDL project and the skill activates. By default it uses the light theme and depth 1 (top + direct children); add "dark mode" or "two levels deep" to override. The agent extracts the architecture, writes a JSON spec next to the SVG, validates the spec, runs the renderer, and validates the SVG geometry.

You can also drive the toolchain manually:

```
python validate_spec.py spec.json
python render.py --theme dark spec.json out.svg
python validate.py out.svg
```

Each validator exits 0 on PASS and a non-zero count of violations otherwise; each violation prints with coordinates so the agent (or a human) can adjust the JSON.

## Testing

```
python tests.py
```

Runs the same checks as CI:

1. **Validator regression** — runs `validate.py` on the intentionally broken `not_sample_broken_validator_fixture.svg` and asserts exactly 8 violations.
2. **Spec validator** — confirms `validate_spec.py` accepts a known-good spec and rejects one with an unknown block id in an edge.
3. **Renderer light + dark** — renders `test_spec.json` in both themes; each output passes geometry validation.
4. **Install dry-run** — copies the runtime files into a throwaway dir, asserts every runtime file is present, and asserts repo-only files (README, tests, fixtures) were *not* copied.

Test conditions: pure Python stdlib, no external tools, runs in well under 10 s on any modern machine. Verified on the OS / Python matrix in [.github/workflows/ci.yml](.github/workflows/ci.yml) (Linux / macOS / Windows × Python 3.10 / 3.12).

If your system temp dir isn't writable (locked-down corporate Windows, sandboxed runner, etc.), tests fall back to `<repo>/tmp/` (gitignored). Override the location with the env var `HDLDIAG_TEST_TMP=/path/of/your/choice`.

## Author

Leonardo Capossio — [bard0 design](https://www.bard0.com) — hello@bard0.com

## License

[MIT](LICENSE).
