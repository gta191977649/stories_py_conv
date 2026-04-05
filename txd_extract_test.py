from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vcs_map_extract.streamed_backend import LVZArchive, set_log_sink


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="txd_extract_test.py",
        description="Extract one streamed LVZ texture to a PNG for decoder validation.",
    )
    parser.add_argument(
        "input",
        help="Game root directory that contains BEACH/MAINLA/MALL LVZ+IMG files",
    )
    parser.add_argument(
        "archive",
        choices=("BEACH", "MAINLA", "MALL"),
        help="Streamed archive name",
    )
    parser.add_argument(
        "res_id",
        type=int,
        help="Streamed texture resource id",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output PNG path. Defaults to OUTPUT_DEBUG/<archive>_<res_id>.png in the repo",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress streamed archive progress logs",
    )
    return parser


def default_output_path(archive_name: str, res_id: int) -> Path:
    return REPO_ROOT / "OUTPUT_DEBUG" / f"{archive_name.lower()}_{res_id}.png"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - environment-specific import
        parser.error(f"Pillow is required to write PNG output: {exc}")

    input_root = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve() if args.output else default_output_path(args.archive, args.res_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.quiet:
        set_log_sink(lambda _msg: None)

    archive = LVZArchive(args.archive, root=input_root)
    texture = archive.texture_for_res_id(args.res_id)
    if texture is None:
        parser.error(f"No decodable texture found for {args.archive} res_id={args.res_id}")

    image = Image.frombytes("RGBA", (texture.width, texture.height), texture.rgba)
    image.save(output_path)
    print(output_path)
    print(f"name={texture.name} size={texture.width}x{texture.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
