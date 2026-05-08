"""
Install the hdldiagZero skill into an agent runtime's skills directory.

By default targets Claude Code's ~/.claude/skills/hdldiagzero. For Codex, use
--runtime codex to target ~/.codex/skills/hdldiagzero. For custom installs,
pass --dst with the exact destination directory.

Runtime installs include SKILL.md, LICENSE, agents/openai.yaml, assets,
renderer/validator scripts, and the references/ tree. Test fixtures, README,
CI config, and the bundled tests stay in the repo and are NOT copied.

Author: Leonardo Capossio - bard0 design - hello@bard0.com
Year:   2026

Usage:
    python install.py                            # Claude default
    python install.py --runtime codex            # Codex default
    python install.py --dst /path/to/skill-dir   # custom destination
"""

import argparse
import shutil
import sys
from pathlib import Path

# Files copied into the runtime skill directory. Listed as paths relative to
# this script, preserving sub-directory structure on the destination side.
SKILL_PATHS = [
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
]

RUNTIME_DEFAULTS = {
    "claude": Path(".claude") / "skills" / "hdldiagzero",
    "codex": Path(".codex") / "skills" / "hdldiagzero",
}


def default_dst(runtime: str) -> Path:
    return Path.home() / RUNTIME_DEFAULTS[runtime]


def main() -> int:
    p = argparse.ArgumentParser(
        description="Install hdldiagZero into a skills directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python install.py\n"
            "  python install.py --runtime codex\n"
            "  python install.py --dst /opt/agent-skills/hdldiagzero"
        ),
    )
    p.add_argument(
        "--runtime",
        choices=sorted(RUNTIME_DEFAULTS),
        default="claude",
        help="default runtime directory to use when --dst is not provided",
    )
    p.add_argument(
        "--dst",
        type=Path,
        default=None,
        help="exact installation directory (overrides --runtime)",
    )
    args = p.parse_args()

    src = Path(__file__).resolve().parent
    dst: Path = (args.dst or default_dst(args.runtime)).expanduser().resolve()

    missing = [rel for rel in SKILL_PATHS if not (src / rel).is_file()]
    if missing:
        print(f"ERROR: missing source files in {src}: {missing}", file=sys.stderr)
        return 2

    dst.mkdir(parents=True, exist_ok=True)
    for rel in SKILL_PATHS:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)

    print(f"Installed hdldiagZero skill -> {dst}")
    print(f"  files: {len(SKILL_PATHS)}")
    print("Restart your agent runtime to pick up changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
