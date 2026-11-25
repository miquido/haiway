import json
import math
from typing import Any, NoReturn


# what the standard library reads with non-finite numbers refused - the oracle of the
# strict JSON a State reads, kept apart from the hooks of the State it checks
def _refused_constant(name: str) -> NoReturn:
    raise ValueError(name)


def _finite_float(text: str) -> float:
    number: float = float(text)
    if not math.isfinite(number):
        raise ValueError(text)

    return number


def strict_loads(document: str | bytes) -> Any:
    return json.loads(document, parse_constant=_refused_constant, parse_float=_finite_float)
