import os
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import BinaryIO, TextIO

from haiway import State, ctx
from haiway.files.config import FileSystemConfig
from haiway.files.types import (
    FileContent,
    FileExisting,
    FileInfo,
    FileListing,
    FileOpening,
    FileReading,
    FileWriting,
    PathInfo,
)

__all__ = ("FileSystem",)


class FileSystem(State):
    """File system abstraction with functional interface."""

    existing: FileExisting
    listing: FileListing
    reading: FileReading
    writing: FileWriting
    opening: FileOpening

    @classmethod
    async def exists(cls, path: Path, /) -> bool:
        """Check if a file or directory exists."""
        return await ctx.state(cls).existing(path)

    @classmethod
    async def list_files(
        cls,
        path: Path,
        /,
        *,
        recursive: bool = False,
        pattern: str | None = None,
    ) -> list[PathInfo]:
        """List files and directories in a path."""
        return await ctx.state(cls).listing(
            path,
            recursive=recursive,
            pattern=pattern,
        )

    @classmethod
    async def read_file(
        cls,
        path: Path,
        /,
        *,
        encoding: str | None = None,
        binary: bool = False,
    ) -> FileContent:
        """Read file content."""
        return await ctx.state(cls).reading(
            path,
            encoding=encoding,
            binary=binary,
        )

    @classmethod
    async def write_file(
        cls,
        path: Path,
        content: bytes | str,
        /,
        *,
        encoding: str | None = None,
        create_parents: bool = True,
        overwrite: bool = True,
    ) -> None:
        """Write content to a file."""
        await ctx.state(cls).writing(
            path,
            content,
            encoding=encoding,
            create_parents=create_parents,
            overwrite=overwrite,
        )

    @classmethod
    def open_file(
        cls,
        path: Path,
        mode: str = "r",
        /,
        *,
        encoding: str | None = None,
        buffering: int = -1,
    ) -> AbstractAsyncContextManager[TextIO | BinaryIO]:
        """Open a file for reading or writing."""
        return ctx.state(cls).opening(
            path,
            mode,
            encoding=encoding,
            buffering=buffering,
        )

    @classmethod
    async def get_file_info(cls, path: Path, /) -> FileInfo:
        """Get detailed information about a file."""
        config = ctx.state(FileSystemConfig)
        resolved_path = config.resolve_path(path)

        if not await cls.exists(resolved_path):
            raise FileNotFoundError(f"File not found: {resolved_path}")

        stat = resolved_path.stat()
        return FileInfo(
            path=resolved_path,
            size=stat.st_size,
            modified_time=stat.st_mtime,
            created_time=getattr(stat, "st_birthtime", None),
            is_readable=resolved_path.is_file() and os.access(resolved_path, os.R_OK),
            is_writable=resolved_path.is_file() and os.access(resolved_path, os.W_OK),
        )
