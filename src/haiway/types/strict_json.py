import math
from typing import NoReturn

__all__ = (
    "finite_float",
    "refused_constant",
)


def refused_constant(name: str) -> NoReturn:
    raise ValueError(f"'{name}' is not a json value")


def finite_float(text: str) -> float:
    number: float = float(text)
    if not math.isfinite(number):
        raise ValueError(f"'{text}' is not a finite number")

    return number
