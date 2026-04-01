from __future__ import annotations

from typing import Mapping

from .models import IdeModel
from .reference_data import load_streamed_link_table, load_vcs_name_table
from .utils import uppercase_crc32


class NameResolver:
    def __init__(self, ide_catalog: dict[str, IdeModel], game_dat_models: Mapping[int, object] | None = None) -> None:
        self.ide_catalog = ide_catalog
        self.known_hash_names = load_vcs_name_table()
        self.streamed_links = load_streamed_link_table()
        self.model_meta_by_id: dict[int, tuple[str, str, str]] = {}
        if game_dat_models is not None:
            for model_id, model in game_dat_models.items():
                model_name = getattr(model, "model_name", "")
                if not model_name:
                    continue
                txd_name = getattr(model, "txd_name", "") or ""
                self.model_meta_by_id[model_id] = (model_name, txd_name, "GAME.dat")
        for model in ide_catalog.values():
            current = self.model_meta_by_id.get(model.model_id)
            if current is None:
                self.model_meta_by_id[model.model_id] = (model.model_name, model.txd_name, model.source_file)
                continue
            current_name, current_txd_name, _current_source = current
            txd_name = current_txd_name if current_txd_name and current_txd_name.lower() != "null" else model.txd_name
            source_file = model.source_file
            self.model_meta_by_id[model.model_id] = (current_name, txd_name, source_file)

        self.model_id_to_name = {
            model_id: meta[0]
            for model_id, meta in self.model_meta_by_id.items()
        }
        self.hash_to_ide_name = dict(self.known_hash_names)
        for model in ide_catalog.values():
            self.hash_to_ide_name.setdefault(uppercase_crc32(model.model_name), model.model_name)

    def resolve_hash(self, hash_value: int) -> str:
        return self.hash_to_ide_name.get(hash_value, f"hash_{hash_value:08X}")

    def resolve_streamed_model_meta(self, world_id: int) -> tuple[int, int, str | None, str, str] | None:
        link = self.streamed_links.get(world_id)
        if link is None:
            return None
        linked_ipl_id, model_id = link
        meta = self.model_meta_by_id.get(model_id)
        if meta is None:
            return linked_ipl_id, model_id, None, "", "GAME.dat"
        model_name, txd_name, source_file = meta
        return linked_ipl_id, model_id, model_name, txd_name, source_file

    def resolve_streamed_model_name(self, world_id: int) -> str | None:
        meta = self.resolve_streamed_model_meta(world_id)
        if meta is None:
            return None
        _linked_ipl_id, _model_id, model_name, _txd_name, _source_file = meta
        return model_name

    def resolve_streamed_link(self, world_id: int) -> tuple[int, str | None] | None:
        meta = self.resolve_streamed_model_meta(world_id)
        if meta is None:
            return None
        linked_ipl_id, _model_id, model_name, _txd_name, _source_file = meta
        return linked_ipl_id, model_name

    def canonical_model_name(self, model_id: int, fallback_name: str) -> str:
        meta = self.model_meta_by_id.get(model_id)
        if meta is None:
            return fallback_name
        return meta[0] or fallback_name
