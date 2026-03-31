from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
import sys


class Vector(tuple):
    def __new__(cls, values: tuple[float, float, float] | list[float]) -> "Vector":
        return super().__new__(cls, tuple(float(value) for value in values))

    @property
    def x(self) -> float:
        return self[0]

    @property
    def y(self) -> float:
        return self[1]

    @property
    def z(self) -> float:
        return self[2]


@dataclass(frozen=True)
class Matrix:
    rows: tuple[tuple[float, float, float, float], ...]

    def __init__(self, rows: tuple[tuple[float, float, float, float], ...] | list[tuple[float, float, float, float]]) -> None:
        object.__setattr__(
            self,
            "rows",
            tuple(tuple(float(value) for value in row) for row in rows),
        )

    def __iter__(self):
        return iter(self.rows)

    def __getitem__(self, index: int) -> tuple[float, float, float, float]:
        return self.rows[index]

    def __matmul__(self, other: "Matrix") -> "Matrix":
        result = []
        for row_index in range(4):
            row = []
            for col_index in range(4):
                row.append(
                    sum(self.rows[row_index][inner] * other.rows[inner][col_index] for inner in range(4))
                )
            result.append(tuple(row))
        return Matrix(tuple(result))

    @staticmethod
    def Identity(size: int) -> "Matrix":
        if size != 4:
            raise ValueError("Only 4x4 identity matrices are supported")
        return Matrix(
            (
                (1.0, 0.0, 0.0, 0.0),
                (0.0, 1.0, 0.0, 0.0),
                (0.0, 0.0, 1.0, 0.0),
                (0.0, 0.0, 0.0, 1.0),
            )
        )


def install_mathutils_shim() -> None:
    existing = sys.modules.get("mathutils")
    if existing is not None and hasattr(existing, "Matrix") and hasattr(existing, "Vector"):
        return

    module = ModuleType("mathutils")
    module.Matrix = Matrix
    module.Vector = Vector
    sys.modules["mathutils"] = module
