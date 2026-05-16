# Changelog

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
