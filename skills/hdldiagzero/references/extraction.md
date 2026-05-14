# HDL extraction patterns

Concrete patterns for finding the top module, walking hierarchy, mapping
blocks to clock domains, and classifying interfaces. Use these with `rg` (or
your search tool of choice) on a real codebase.

## 1. Finding the top module

Try, in order:

1. **Explicit naming** - pick the obvious candidate when present:
   - Verilog: `top.v`, `*_top.v`
   - SystemVerilog: `top.sv`, `*_top.sv`
   - VHDL: `top.vhd`, `*_top.vhd`
   - Vivado block design: a single `*.bd` (or one with `top` in the name)
   - LiteX: `litex_soc.py`, or a `BaseSoC`-derived class in the project.

2. **Build-script anchors**: `Makefile`, `scripts/synth.tcl`, `*.xpr`,
   `vivado_proj/*.tcl`, `targets/*.cfg`. Search for `set top`, `top_module=`,
   `top_entity_name`, `set_property top`.

3. **No-instantiation heuristic**: list all module/entity declarations, then
   eliminate any that appear as instantiations elsewhere. Whatever remains is
   a top candidate.

   ```sh
   rg -n "^\s*module\s+(\w+)" --type=verilog
   rg -n "^\s*entity\s+(\w+)\s+is" --type=vhdl
   ```

If after these checks there are still multiple plausible candidates, ask the
user. If there's an obvious one, just use it without asking.

## 2. Walking hierarchy to depth N

Default depth = 1: the top + its direct children only. Apply the extraction
policy in section 4 at every depth.

### Verilog / SystemVerilog
- Within each module, find instantiations: `<module_name> <inst_name> (...)`.
  Match positional or named-port forms.
  ```sh
  rg -n "^\s*(\w+)\s+(\w+)\s*\(" --type-add 'sv:*.{v,sv,svh}' --type=sv
  ```
  Filter the first capture against the set of declared module names.

  **Caveat - this regex over-matches.** It will also flag `task <name> (`,
  `function <type> <name> (`, gate primitives (`and g1 (`, `nand`, `xor`, ...),
  `for`/`if`/`generate` constructs, and macro calls (`` `<name> (... ``).
  The "filter against declared modules" step kills most false positives,
  but be alert in two cases:
  - macros that *expand* to instantiations (your filter never sees the
    real module) - search `\`define` and the macro body separately;
  - `bind`-statements (`bind <target> <module> <inst> (...)`) - they're
    real instantiations but not at the top of a line.
  If a module's declared-name list looks suspiciously short, double-check
  by reading the build-script file lists.

### VHDL
- Within each `architecture ... of <entity> is ... begin ... end`, find:
  ```vhdl
  inst : entity work.<entity> ...
  inst : <entity> ...
  inst : component <entity> ...
  ```

### Vivado block design (`*.bd`)
- Open the `.bd` file (it's Tcl-flavored JSON-like). Each `bd:cell` element
  is an instantiation. Hierarchy is explicit; no inference needed.

### LiteX (`litex_soc.py`)
- Top is usually a `BaseSoC` subclass. Cores are added with
  `self.submodules.<name> = <ClassName>(...)`. Walk from the top class.

## 3. Mapping blocks to clock domains

For each block, determine its primary clock domain:

1. Look at the block's port list for `clk_*`, `*_clk`, `*_aclk`, `*clock`.
2. Trace each clock signal back to its source - MMCM, PLL, external pin, or
   transceiver-derived (`tx_par_clk`, `rx_par_clk`).
3. Group blocks by their primary clock source.

Only include `freq_mhz` for a domain when the frequency is explicit in the
source, constraints, or build metadata. If it is not found, omit `freq_mhz`
from that domain entry. Do not guess and do not write placeholders like
`? MHz`.

### When to use `domain_b` (CDC block) vs a `cdc` edge

A block becomes a CDC block (`domain` + `domain_b`, rendered with a split
gradient) **only when CDC is its architectural purpose** - it exists in
the design specifically to bridge two clock domains. Examples:

- Async FIFO instantiated as the boundary between two pipelines.
- A pixel packer that runs partially on a video clock and partially on the
  TX bus clock.
- A clock-crossing arbiter or handshake bridge.

A block that is *primarily* in one domain but happens to contain a single
two-flop status synchronizer is **not** a CDC block. Examples that should
keep their primary domain:

- A control register block in `axi_clk` that re-synchronizes one status bit
  out of a peripheral.
- A DMA on `axi_clk` that has a small `xpm_cdc_single` for a debug signal
  going elsewhere.

For those cases, keep the block on its primary `domain` and draw the
crossing as an explicit `cdc` edge into / out of the block. The diagram
stays honest: large boxes only get split fills when the CDC is structural.

Common domain names you'll see:

| Pattern                          | Typical meaning                          |
|----------------------------------|------------------------------------------|
| `axi_clk`, `s_axi_aclk`          | AXI bus clock (often 100-250 MHz)        |
| `tx_par_clk`, `rx_par_clk`       | GT transceiver parallel clocks           |
| `video_clk`, `pixel_clk`         | Pixel domain                             |
| `ddr_clk`, `mem_clk`             | Memory controller domain                 |
| `freerun_clk`, `ref_clk`         | Reference / startup clock                |

CDC primitive search:
```sh
rg -n "xpm_cdc_|async_fifo|handshake_cdc|xpm_fifo_async" --type=verilog --type=vhdl
```

## 4. Extraction policy

The clean architecture default is to hide low-level implementation detail. The
user can override that per diagram with top-level JSON metadata:

```json
"extraction": {
  "hide_primitives": true,
  "hide_processor_structure": true,
  "hide_debug": true,
  "hide_clock_reset": true
}
```

If the user asks to show primitives, processor internals, debug, clocks/resets,
or implementation detail, flip the matching field to `false` and include that
structure at the requested hierarchy depth.

Default hidden categories:

- **Vendor / generated trees**: anything under `*.cache/`, `*.gen/`,
  `*.runs/`, `vivado_proj/`, `quartus/db/`, `*.IP_user_files/`, `ip/`,
  `_xil_defaultlib/`. Treat as opaque unless implementation detail was
  explicitly requested.
- **Soft processors**: `microblaze`, `picorv32`, `vexriscv`, `cv32e40p`,
  `ibex`, `neorv32`. Single block, no expansion. No BRAM, no MDM, no debug
  bus unless `hide_processor_structure` is `false`.
- **Memory primitives**: `RAMB18`, `RAMB36`, `BRAM_*`, `xpm_memory_*`,
  `xilinx_simple_dual_port_*`, `*_fifo_*`, `xpm_fifo_*`. Don't expand;
  mention only if the surrounding logic is incoherent without them, or if
  `hide_primitives` is `false`.
- **JTAG**: `BSCANE2`, `JTAG_*`, `MDM`, `DAP_*`. Drop unless
  `hide_debug` is `false`.
- **Debug-only IP**: `ila_*`, `vio_*`, `system_ila`, `*_debug_*`. Drop
  unless `hide_debug` is `false`.
- **Clock primitives**: `MMCME*`, `PLLE*`, `BUFG*`, `clk_wiz*`. Drop -
  the diagram encodes domain by color, not topology. Include only when
  `hide_clock_reset` is `false`.
- **Reset primitives**: `proc_sys_reset`, `xpm_cdc_async_rst`. Drop unless
  `hide_clock_reset` is `false`.

## 5. Classifying each connection

Inspect the port declarations connecting two blocks:

| Signature (typical port subset)                                              | `kind`        |
|------------------------------------------------------------------------------|---------------|
| `awvalid awready awaddr awlen awsize awburst wvalid wready wdata wstrb wlast bvalid bready bresp arvalid arready araddr arlen arsize arburst rvalid rready rdata rresp rlast` | `axi-mm`      |
| Same as `axi-mm` but **without** `awlen / awburst / awsize / arlen / arburst / arsize / wlast / rlast` | `axi-lite`    |
| `tdata tvalid tready` (often `tlast tkeep tstrb tuser tdest tid`)            | `axi-stream`  |
| Anything resynchronized at destination via xpm_cdc / async FIFO / handshake  | `cdc`         |
| RGMII, SPI, I2C, UART, custom buses, discretes                                | `generic`     |

`axi-mm` is for **full AXI** only. AXI4-Lite is `axi-lite`. Don't conflate.

## 6. Width extraction

Always read the actual port declaration; don't guess.

- Verilog/SV: `[N:0] portname` -> width = N + 1.
- VHDL: `std_logic_vector(N downto 0)` -> width = N + 1.
- Parametric: if the width is a parameter (`DATA_W`, `AXI_DATA_WIDTH`),
  use the parameter name as a string in the JSON
  (`"width": "DATA_W"`) instead of guessing the value.

For non-numeric buses (RGMII, SPI), set `"width"` to the protocol name as a
string: `"width": "RGMII"`. The validator's BITWIDTH check treats any
non-empty label as satisfied.
