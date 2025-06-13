import asyncio
import fnmatch
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO, TextIO, cast

from haiway import ctx
from haiway.files.config import FileSystemConfig
from haiway.files.state import FileSystem
from haiway.files.types import FileContent, PathInfo

__all__ = ("local_file_system",)


async def local_file_existing(path: Path, /) -> bool:
    """Check if a file or directory exists on the local file system."""
    config = ctx.state(FileSystemConfig)
    resolved_path = config.resolve_path(path)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, resolved_path.exists)


async def local_file_listing(
    path: Path,
    /,
    *,
    recursive: bool = False,
    pattern: str | None = None,
) -> list[PathInfo]:
    """List files and directories in a local path."""
    config = ctx.state(FileSystemConfig)
    resolved_path = config.resolve_path(path)

    if not resolved_path.exists():
        raise FileNotFoundError(f"Path not found: {resolved_path}")

    if not resolved_path.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {resolved_path}")

    loop = asyncio.get_event_loop()

    def _list_files() -> list[PathInfo]:
        files: list[PathInfo] = []

        if recursive:
            iterator = resolved_path.rglob("*")
        else:
            iterator = resolved_path.iterdir()

        for item in iterator:
            if pattern and not fnmatch.fnmatch(item.name, pattern):
                continue

            try:
                stat = item.stat()
                path_info = PathInfo(
                    path=item,
                    exists=True,
                    is_file=item.is_file(),
                    is_directory=item.is_dir(),
                    size=stat.st_size if item.is_file() else None,
                )
                files.append(path_info)
            except (OSError, PermissionError):
                # Skip files we can't access
                continue

        return files

    return await loop.run_in_executor(None, _list_files)


async def local_file_reading(
    path: Path,
    /,
    *,
    encoding: str | None = None,
    binary: bool = False,
) -> FileContent:
    """Read content from a local file."""
    config = ctx.state(FileSystemConfig)
    resolved_path = config.resolve_path(path)

    if not resolved_path.exists():
        raise FileNotFoundError(f"File not found: {resolved_path}")

    if not resolved_path.is_file():
        raise IsADirectoryError(f"Path is not a file: {resolved_path}")

    if not config.is_allowed_file(resolved_path):
        raise PermissionError(f"File type not allowed: {resolved_path}")

    stat = resolved_path.stat()
    if stat.st_size > config.max_file_size:
        raise ValueError(f"File too large: {stat.st_size} bytes > {config.max_file_size}")

    loop = asyncio.get_event_loop()

    def _read_file() -> bytes | str:
        if binary:
            return resolved_path.read_bytes()
        else:
            file_encoding = encoding or config.default_encoding
            return resolved_path.read_text(encoding=file_encoding)

    data = await loop.run_in_executor(None, _read_file)

    return FileContent(
        data=data,
        encoding=encoding or config.default_encoding if not binary else None,
        path=resolved_path,
    )


async def local_file_writing(
    path: Path,
    content: bytes | str,
    /,
    *,
    encoding: str | None = None,
    create_parents: bool = True,
    overwrite: bool = True,
) -> None:
    """Write content to a local file."""
    config = ctx.state(FileSystemConfig)
    resolved_path = config.resolve_path(path)

    if not config.is_allowed_file(resolved_path):
        raise PermissionError(f"File type not allowed: {resolved_path}")

    if resolved_path.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {resolved_path}")

    if create_parents:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

    # Check file size before writing
    if isinstance(content, bytes):
        content_size = len(content)
    else:
        file_encoding = encoding or config.default_encoding
        content_size = len(content.encode(file_encoding))

    if content_size > config.max_file_size:
        raise ValueError(f"File too large: {content_size} bytes > {config.max_file_size}")

    loop = asyncio.get_event_loop()

    def _write_file() -> None:
        if isinstance(content, bytes):
            resolved_path.write_bytes(content)
        else:
            file_encoding = encoding or config.default_encoding
            resolved_path.write_text(content, encoding=file_encoding)

    await loop.run_in_executor(None, _write_file)


@asynccontextmanager
async def local_file_opening(
    path: Path,
    mode: str = "r",
    /,
    *,
    encoding: str | None = None,
    buffering: int = -1,
) -> AsyncGenerator[TextIO | BinaryIO, None]:
    """Open a local file for reading or writing."""
    config = ctx.state(FileSystemConfig)
    resolved_path = config.resolve_path(path)

    if not config.is_allowed_file(resolved_path):
        raise PermissionError(f"File type not allowed: {resolved_path}")

    # Create parent directories if writing
    if "w" in mode or "a" in mode:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

    file_encoding = encoding or (config.default_encoding if "b" not in mode else None)

    loop = asyncio.get_event_loop()

    def _open_file() -> TextIO | BinaryIO:
        return cast(
            TextIO | BinaryIO,
            open(
                resolved_path,
                mode,
                encoding=file_encoding,
                buffering=buffering,
            ),
        )

    file_handle = await loop.run_in_executor(None, _open_file)

    try:
        yield file_handle
    finally:
        await loop.run_in_executor(None, file_handle.close)


def local_file_system() -> FileSystem:
    """Create a FileSystem instance for local file operations."""
    return FileSystem(
        existing=local_file_existing,
        listing=local_file_listing,
        reading=local_file_reading,
        writing=local_file_writing,
        opening=local_file_opening,
    )
