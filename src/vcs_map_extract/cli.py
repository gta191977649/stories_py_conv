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
            "  vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --clean\n"
            "  vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --export\n"
            "  vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --clean --export\n"
            "  vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --export --buildimg\n"
            "  vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --decode-dat --export\n"
            "  vcs-map-extract /Users/nurupo/Desktop/ps2/GAME/MOCAPPS2.DIR /tmp/vcs_out --export\n"
            "  vcs-map-extract /Users/nurupo/Desktop/ps2/GAME /tmp/vcs_out --decode-dat\n"
            "\n"
            "Use --export to extract models. Use --buildimg together with --export to pack OUTPUT/vcs_map.img."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Game root directory or IMG DIR file path")
    parser.add_argument("output", help="Output directory")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove all files under the output directory before doing anything else",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export models, textures, and collisions into the output directory",
    )
    parser.add_argument(
        "--buildimg",
        action="store_true",
        help="Pack generated dff/txd/col outputs into OUTPUT/vcs_map.img",
    )
    parser.add_argument(
        "--decode-dat",
        action="store_true",
        help="Decode GAME.dat into OUTPUT/data/ide and OUTPUT/data/ipl, then exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.buildimg and not args.export:
        parser.error("--buildimg requires --export. Use: vcs-map-extract INPUT OUTPUT --export --buildimg")
    if not args.clean and not args.decode_dat and not args.export:
        parser.error("No action selected. Use --clean, --export, or --decode-dat.")
    return run(args.input, args.output, args.clean, args.export, args.buildimg, args.decode_dat)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
