from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import BinaryIO, Protocol, TextIO, runtime_checkable

from haiway import State

__all__ = (
    "FileContent",
    "FileExisting",
    "FileInfo",
    "FileListing",
    "FileOpening",
    "FileReading",
    "FileWriting",
    "PathInfo",
)


class PathInfo(State):
    """Information about a file system path."""

    path: Path
    exists: bool
    is_file: bool
    is_directory: bool
    size: int | None = None


class FileInfo(State):
    """Detailed information about a file."""

    path: Path
    size: int
    modified_time: float
    created_time: float | None = None
    is_readable: bool = True
    is_writable: bool = True


class FileContent(State):
    """File content with metadata."""

    data: bytes | str
    encoding: str | None = None
    path: Path | None = None


@runtime_checkable
class FileExisting(Protocol):
    """Check if a file or directory exists."""

    async def __call__(self, path: Path, /) -> bool: ...


@runtime_checkable
class FileListing(Protocol):
    """List files and directories in a path."""

    async def __call__(
        self,
        path: Path,
        /,
        *,
        recursive: bool = False,
        pattern: str | None = None,
    ) -> list[PathInfo]: ...


@runtime_checkable
class FileReading(Protocol):
    """Read file content."""

    async def __call__(
        self,
        path: Path,
        /,
        *,
        encoding: str | None = None,
        binary: bool = False,
    ) -> FileContent: ...


@runtime_checkable
class FileWriting(Protocol):
    """Write content to a file."""

    async def __call__(
        self,
        path: Path,
        content: bytes | str,
        /,
        *,
        encoding: str | None = None,
        create_parents: bool = True,
        overwrite: bool = True,
    ) -> None: ...


@runtime_checkable
class FileOpening(Protocol):
    """Open a file for reading or writing."""

    def __call__(
        self,
        path: Path,
        mode: str = "r",
        /,
        *,
        encoding: str | None = None,
        buffering: int = -1,
    ) -> AbstractAsyncContextManager[TextIO | BinaryIO]: ...
