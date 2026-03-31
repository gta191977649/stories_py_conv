from __future__ import annotations

import argparse
import sys

from .app import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vcs-map-extract",
        description="Extract GTA VCS PS2 map/static assets to RW outputs.",
        epilog=(
            "Examples:\n"
            "  vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out\n"
            "  vcs-map-extract /Users/nurupo/Desktop/ps2/GAME/MOCAPPS2.DIR /tmp/vcs_out\n"
            "  vcs-map-extract /Users/nurupo/Desktop/ps2/GAME/MOCAPPS2.DIR /tmp/vcs_out --packimg"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Game root directory or IMG DIR file path")
    parser.add_argument("output", help="Output directory")
    parser.add_argument(
        "--packimg",
        action="store_true",
        help="Pack generated dff/txd/col outputs into OUTPUT/vcs_map.img",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args.input, args.output, args.packimg)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
