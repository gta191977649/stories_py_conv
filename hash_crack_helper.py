from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import zlib


REPO_ROOT = Path(__file__).resolve().parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vcs_map_extract.game_dat import GameDat
from vcs_map_extract.ide_catalog import parse_ide_directory
from vcs_map_extract.reference_data import load_vcs_name_table
@dataclass(slots=True)
class CandidateName:
    name: str
    source: str


@dataclass(slots=True)
class CandidateMatch:
    name: str
    source: str
    hasher: str


@dataclass(slots=True)
class UnknownModel:
    model_id: int
    hash_key: int
    model_name: str
    txd_name: str
    model_type: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Try to recover unresolved VCS hash_* model names with candidate hash matching.",
        epilog=(
            "Examples:\n"
            "  python3 hash_crack_helper.py --game-root /Users/nurupo/Desktop/ps2/GAME --list-unresolved\n"
            "  python3 hash_crack_helper.py --game-root /Users/nurupo/Desktop/ps2/GAME --auto --hash hash_1AB76BB1\n"
            "  python3 hash_crack_helper.py --game-root /Users/nurupo/Desktop/ps2/GAME --auto --limit 50\n"
            "  python3 hash_crack_helper.py --game-root /Users/nurupo/Desktop/ps2/GAME --hash 0x6D9B9360 --candidate dr_roofblokc06\n"
            "  python3 hash_crack_helper.py --game-root /Users/nurupo/Desktop/ps2/GAME --auto --write-inc /tmp/cracked.inc\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--game-root", required=True, help="Path to the VCS PS2 GAME directory")
    parser.add_argument("--hash", action="append", default=[], help="Target hash in hex, decimal, or hash_XXXXXXXX form")
    parser.add_argument("--model-id", action="append", type=int, default=[], help="Target unresolved model id from GAME.dat")
    parser.add_argument("--candidate", action="append", default=[], help="Manual candidate name to test")
    parser.add_argument("--candidates-file", action="append", default=[], help="Text file with one candidate name per line")
    parser.add_argument("--list-unresolved", action="store_true", help="List unresolved GAME.dat hash_* models and exit")
    parser.add_argument("--auto", action="store_true", help="Build a candidate pool from known names and IDE/TXD data")
    parser.add_argument("--include-zero-hash", action="store_true", help="Include hash_00000000 rows in the target set")
    parser.add_argument("--limit", type=int, default=0, help="Limit target listing or auto scan to the first N unresolved rows")
    parser.add_argument("--write-inc", help="Optional output path for matched entries in .inc format")
    parser.add_argument(
        "--hasher",
        action="append",
        choices=("stories-upper", "crc32-upper", "crc32-raw", "jenkins"),
        default=[],
        help="Hasher to use for candidate matching. Defaults to stories-upper.",
    )
    return parser


def stories_upper_hash(name: str) -> int:
    return (zlib.crc32(name.upper().encode("ascii", "ignore")) ^ 0xFFFFFFFF) & 0xFFFFFFFF


def crc32_upper_hash(name: str) -> int:
    return zlib.crc32(name.upper().encode("ascii", "ignore")) & 0xFFFFFFFF


def crc32_raw_hash(name: str) -> int:
    return zlib.crc32(name.encode("ascii", "ignore")) & 0xFFFFFFFF


def jenkins_hash(name: str) -> int:
    hash_key = 0
    for value in name.encode("ascii", "ignore"):
        hash_key = (hash_key + value) & 0xFFFFFFFF
        hash_key = (hash_key + ((hash_key << 10) & 0xFFFFFFFF)) & 0xFFFFFFFF
        hash_key ^= hash_key >> 6
    hash_key = (hash_key + ((hash_key << 3) & 0xFFFFFFFF)) & 0xFFFFFFFF
    hash_key ^= hash_key >> 11
    hash_key = (hash_key + ((hash_key << 15) & 0xFFFFFFFF)) & 0xFFFFFFFF
    return hash_key & 0xFFFFFFFF


HASHERS = {
    "stories-upper": stories_upper_hash,
    "crc32-upper": crc32_upper_hash,
    "crc32-raw": crc32_raw_hash,
    "jenkins": jenkins_hash,
}


def parse_hash_value(raw: str) -> int:
    value = raw.strip()
    if not value:
        raise ValueError("empty hash value")
    lowered = value.lower()
    if lowered.startswith("hash_"):
        return int(lowered[5:], 16)
    if lowered.startswith("0x"):
        return int(lowered, 16)
    return int(value, 10)


def load_game_dat(game_root: Path) -> GameDat:
    game_dat_path = game_root / "GAME.dat"
    if not game_dat_path.is_file():
        raise FileNotFoundError(f"GAME.dat not found: {game_dat_path}")
    return GameDat(game_dat_path.read_bytes(), load_vcs_name_table())


def collect_unknown_models(game_dat: GameDat, include_zero_hash: bool) -> list[UnknownModel]:
    unknowns: list[UnknownModel] = []
    for model in game_dat.iter_model_infos():
        if not model.model_name.startswith("hash_"):
            continue
        if not include_zero_hash and model.hash_key == 0:
            continue
        unknowns.append(
            UnknownModel(
                model_id=model.model_id,
                hash_key=model.hash_key,
                model_name=model.model_name,
                txd_name=model.txd_name,
                model_type=model.model_type,
            )
        )
    return unknowns


def load_manual_candidates(paths: list[str], names: list[str]) -> list[CandidateName]:
    candidates: list[CandidateName] = []
    seen: set[str] = set()
    for name in names:
        cleaned = name.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            candidates.append(CandidateName(cleaned, "manual"))
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        for raw_line in path.read_text(errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line not in seen:
                seen.add(line)
                candidates.append(CandidateName(line, f"file:{path.name}"))
    return candidates


def auto_candidates(game_root: Path, game_dat: GameDat) -> list[CandidateName]:
    candidates: list[CandidateName] = []
    seen: set[str] = set()

    def add(name: str, source: str) -> None:
        cleaned = name.strip()
        if not cleaned or cleaned.startswith("hash_") or cleaned.lower() == "null":
            return
        if cleaned in seen:
            return
        seen.add(cleaned)
        candidates.append(CandidateName(cleaned, source))

    for name in load_vcs_name_table().values():
        add(name, "reference")

    for model in game_dat.iter_model_infos():
        add(model.model_name, "gamedat-model")
        add(model.txd_name, "gamedat-txd")

    ide_dir = game_root / "ide"
    if ide_dir.is_dir():
        for model in parse_ide_directory(ide_dir).values():
            add(model.model_name, f"ide:{model.source_file}")
            add(model.txd_name, f"ide-txd:{model.source_file}")

    expanded: list[CandidateName] = []
    expanded_seen: set[str] = set()

    def emit(name: str, source: str) -> None:
        cleaned = name.strip()
        if not cleaned or cleaned in expanded_seen or cleaned.startswith("hash_"):
            return
        expanded_seen.add(cleaned)
        expanded.append(CandidateName(cleaned, source))

    for candidate in candidates:
        emit(candidate.name, candidate.source)
        lowered = candidate.name.lower()
        if lowered.endswith("_ext"):
            emit(candidate.name[:-4] + "_int", f"{candidate.source}+ext-int")
        elif lowered.endswith("_int"):
            emit(candidate.name[:-4] + "_ext", f"{candidate.source}+int-ext")
        if not candidate.name.startswith("LOD"):
            emit(f"LOD{candidate.name}", f"{candidate.source}+lod-prefix")
            if len(candidate.name) > 3 and candidate.name[2] == "_":
                emit(f"LOD{candidate.name[3:]}", f"{candidate.source}+lod-trim-prefix")
        else:
            emit(candidate.name[3:], f"{candidate.source}+lod-strip")
    return expanded


def build_hash_index(candidates: list[CandidateName], hashers: list[str]) -> dict[int, list[CandidateMatch]]:
    index: dict[int, list[CandidateMatch]] = defaultdict(list)
    seen: set[tuple[int, str, str]] = set()
    for candidate in candidates:
        for hasher_name in hashers:
            hash_key = HASHERS[hasher_name](candidate.name)
            dedupe_key = (hash_key, candidate.name, hasher_name)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            index[hash_key].append(CandidateMatch(candidate.name, candidate.source, hasher_name))
    return index


def select_targets(
    unknowns: list[UnknownModel],
    hash_args: list[str],
    model_ids: list[int],
    limit: int,
) -> list[UnknownModel]:
    by_hash: dict[int, list[UnknownModel]] = defaultdict(list)
    by_model_id = {model.model_id: model for model in unknowns}
    for model in unknowns:
        by_hash[model.hash_key].append(model)

    if not hash_args and not model_ids:
        targets = list(unknowns)
    else:
        targets = []
        seen_model_ids: set[int] = set()
        for raw_hash in hash_args:
            hash_key = parse_hash_value(raw_hash)
            for model in by_hash.get(hash_key, []):
                if model.model_id not in seen_model_ids:
                    seen_model_ids.add(model.model_id)
                    targets.append(model)
            if hash_key not in by_hash:
                targets.append(
                    UnknownModel(
                        model_id=-1,
                        hash_key=hash_key,
                        model_name=f"hash_{hash_key:08X}",
                        txd_name="",
                        model_type=-1,
                    )
                )
        for model_id in model_ids:
            model = by_model_id.get(model_id)
            if model is not None and model.model_id not in seen_model_ids:
                seen_model_ids.add(model.model_id)
                targets.append(model)

    targets.sort(key=lambda model: model.model_id)
    if limit > 0:
        return targets[:limit]
    return targets


def format_unknown(model: UnknownModel) -> str:
    return (
        f"model_id={model.model_id} "
        f"hash=0x{model.hash_key:08X} "
        f"name={model.model_name} "
        f"txd={model.txd_name} "
        f"type={model.model_type}"
    )


def print_unresolved(models: list[UnknownModel]) -> None:
    print(f"unresolved models: {len(models)}")
    for model in models:
        print(format_unknown(model))


def write_inc(path: Path, matches: dict[int, list[CandidateMatch]]) -> None:
    lines = []
    for hash_key in sorted(matches):
        for candidate in sorted(matches[hash_key], key=lambda item: item.name.lower()):
            lines.append(f'{{ 0x{hash_key:08X}, "{candidate.name}" }},')
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def describe_match(model: UnknownModel, candidates: list[CandidateMatch]) -> str:
    ordered = sorted(candidates, key=lambda item: (item.name.lower(), item.source.lower(), item.hasher.lower()))
    rendered = "; ".join(f"{candidate.name} [{candidate.source}; {candidate.hasher}]" for candidate in ordered)
    return f"{format_unknown(model)} -> {rendered}"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    game_root = Path(args.game_root).expanduser().resolve()
    game_dat = load_game_dat(game_root)
    unknowns = collect_unknown_models(game_dat, include_zero_hash=args.include_zero_hash)

    if args.list_unresolved and not args.auto and not args.candidate and not args.candidates_file and not args.hash and not args.model_id:
        listed = unknowns[: args.limit] if args.limit > 0 else unknowns
        print_unresolved(listed)
        return 0

    targets = select_targets(unknowns, args.hash, args.model_id, args.limit)
    if not targets:
        print("no target unresolved hashes selected")
        return 1

    candidates = load_manual_candidates(args.candidates_file, args.candidate)
    if args.auto:
        candidates.extend(auto_candidates(game_root, game_dat))
    if not candidates:
        print("no candidate names provided; use --auto, --candidate, or --candidates-file")
        return 1

    selected_hashers = args.hasher or ["stories-upper"]
    hash_index = build_hash_index(candidates, selected_hashers)
    matches_by_hash: dict[int, list[CandidateMatch]] = {}
    matched_models = 0
    for model in targets:
        matches = hash_index.get(model.hash_key, [])
        if matches:
            matched_models += 1
            matches_by_hash[model.hash_key] = matches

    print(
        f"targets={len(targets)} matched_models={matched_models} "
        f"candidate_pool={len(candidates)} hashers={','.join(selected_hashers)}"
    )
    for model in targets:
        matches = matches_by_hash.get(model.hash_key)
        if matches:
            print(describe_match(model, matches))
        else:
            print(f"{format_unknown(model)} -> <no match>")

    if args.write_inc:
        output_path = Path(args.write_inc).expanduser().resolve()
        write_inc(output_path, matches_by_hash)
        print(f"wrote {sum(len(v) for v in matches_by_hash.values())} inc rows to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
