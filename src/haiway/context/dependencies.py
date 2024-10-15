from abc import ABC, abstractmethod
from asyncio import Lock, gather, shield
from typing import ClassVar, Self, cast, final

__all__ = [
    "Dependencies",
    "Dependency",
]


class Dependency(ABC):
    @classmethod
    @abstractmethod
    async def prepare(cls) -> Self: ...

    async def dispose(self) -> None:  # noqa: B027
        pass


@final
class Dependencies:
    _lock: ClassVar[Lock] = Lock()
    _dependencies: ClassVar[dict[type[Dependency], Dependency]] = {}

    def __init__(self) -> None:
        raise NotImplementedError("Can't instantiate Dependencies")

    @classmethod
    async def dependency[Requested: Dependency](
        cls,
        dependency: type[Requested],
        /,
    ) -> Requested:
        async with cls._lock:
            if dependency not in cls._dependencies:
                cls._dependencies[dependency] = await dependency.prepare()

            return cast(Requested, cls._dependencies[dependency])

    @classmethod
    async def register(
        cls,
        dependency: Dependency,
        /,
    ) -> None:
        async with cls._lock:
            if current := cls._dependencies.get(dependency.__class__):
                await current.dispose()

            cls._dependencies[dependency.__class__] = dependency

    @classmethod
    async def dispose(cls) -> None:
        async with cls._lock:
            await shield(
                gather(
                    *[dependency.dispose() for dependency in cls._dependencies.values()],
                    return_exceptions=False,
                )
            )
            cls._dependencies.clear()
