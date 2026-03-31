from __future__ import annotations

from .models import IdeModel
from .reference_data import load_streamed_link_table, load_vcs_name_table
from .utils import uppercase_crc32


class NameResolver:
    def __init__(self, ide_catalog: dict[str, IdeModel]) -> None:
        self.ide_catalog = ide_catalog
        self.known_hash_names = load_vcs_name_table()
        self.streamed_links = load_streamed_link_table()
        self.model_id_to_name = {
            model.model_id: model.model_name
            for model in ide_catalog.values()
        }
        self.hash_to_ide_name = {
            uppercase_crc32(model.model_name): model.model_name
            for model in ide_catalog.values()
        }
        for key, value in self.known_hash_names.items():
            self.hash_to_ide_name.setdefault(key, value)

    def resolve_hash(self, hash_value: int) -> str:
        return self.hash_to_ide_name.get(hash_value, f"hash_{hash_value:08X}")

    def resolve_streamed_model_name(self, world_id: int) -> str | None:
        link = self.streamed_links.get(world_id)
        if link is None:
            return None
        _linked_ipl_id, model_id = link
        return self.model_id_to_name.get(model_id)

    def resolve_streamed_link(self, world_id: int) -> tuple[int, str | None] | None:
        link = self.streamed_links.get(world_id)
        if link is None:
            return None
        linked_ipl_id, model_id = link
        return linked_ipl_id, self.model_id_to_name.get(model_id)
