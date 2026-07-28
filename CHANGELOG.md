# Changelog

## 1.2.9

- Fixed text-width estimation in both `render.py` and `validate.py`: replaced
  the `len(text) * font_size * 0.55` monospace approximation with a per-glyph
  proportional advance-width table (Adobe AFM, Helvetica/Arial-class). Edge
  labels and clearance masks are now sized to their real width, and the text
  geometry checks no longer report phantom overlaps or miss real ones.
- Added an SVG snapshot regression test: every tracked `sample_*.svg` is diffed
  against a fresh render of its spec, so a rendering change that still passes
  geometry validation can no longer ship silently. Refresh the tracked samples
  after an intentional change with `python tests.py --update-samples`.
- Hardened the validator regression fixture test to assert a minimum count per
  violation code instead of one magic total, so it fails on a dropped rule but
  tolerates newly added checks.
- Regenerated the tracked sample SVGs under the corrected text metrics.
- Condensed `references/schema.md` (~15% smaller) by cutting prose that merely
  restated the field tables; every field, constraint, and example is retained.
  This reference loads on nearly every skill invocation.

## 1.2.0

- Added geometry validators for overlapping text, diagonal segments, floating
  endpoints, perpendicular block entry/exit, and unnecessary route loops.
- Added TileLink / TL-UL as a first-class edge kind.
- Added compact legends, functional bands, quarter-step placement, compact
  multi-line block labels, and local block size overrides for dense SoC
  diagrams.
- Tightened sample coverage so tracked sample specs render and validate in
  both light and dark themes.
- Removed inert extraction metadata from the JSON schema; extraction policy is
  now documented as guidance rather than renderer input.
- Tightened SVG output safety by escaping quotes in attribute values.
