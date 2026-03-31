from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path

from .constants import DEFAULT_REFERENCE_ROOTS
from .mathutils_compat import install_mathutils_shim


def _load_module(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def load_bleeds_modules():
    install_mathutils_shim()
    root = DEFAULT_REFERENCE_ROOTS["bleeds"] / "leedsLib"
    mdl = _load_module("vcs_map_extract._bleeds_mdl", root / "mdl.py")
    tex = _load_module("vcs_map_extract._bleeds_tex", root / "tex.py")
    col2 = _load_module("vcs_map_extract._bleeds_col2", root / "col2.py")

    def quiet_log(self, msg: str) -> None:
        self.debug_log.append(str(msg))

    mdl.StoriesMDLContext.log = quiet_log
    return mdl, tex, col2


@lru_cache(maxsize=1)
def load_dragonff_modules():
    dragon_root = DEFAULT_REFERENCE_ROOTS["dragonff"]
    if str(dragon_root) not in sys.path:
        sys.path.insert(0, str(dragon_root))
    from gtaLib import col as dragon_col  # type: ignore
    from gtaLib import dff as dragon_dff  # type: ignore
    from gtaLib import txd as dragon_txd  # type: ignore

    return dragon_dff, dragon_txd, dragon_col
