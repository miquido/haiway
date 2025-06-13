import fnmatch
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from io import BytesIO, StringIO
from pathlib import Path
from typing import BinaryIO, TextIO, cast

from haiway import ctx
from haiway.files.config import FileSystemConfig
from haiway.files.state import FileSystem
from haiway.files.types import FileContent, PathInfo

__all__ = (
    "MemoryFileSystem",
    "memory_file_system",
)


class MemoryFileSystem:
    """In-memory file system for testing and temporary operations."""

    def __init__(self) -> None:
        self._files: dict[Path, bytes] = {}
        self._directories: set[Path] = set()
        self._timestamps: dict[Path, float] = {}

    def _normalize_path(self, path: Path) -> Path:
        """Normalize a path for consistent storage."""
        return Path(str(path).replace("\\", "/"))

    def _ensure_parent_dirs(self, path: Path) -> None:
        """Ensure all parent directories exist."""
        current = path.parent
        while current != Path("/") and current != Path("."):
            self._directories.add(self._normalize_path(current))
            current = current.parent

    async def exists(self, path: Path, /) -> bool:
        """Check if a file or directory exists in memory."""
        normalized = self._normalize_path(path)
        return normalized in self._files or normalized in self._directories

    async def list_files(
        self,
        path: Path,
        /,
        *,
        recursive: bool = False,
        pattern: str | None = None,
    ) -> list[PathInfo]:
        """List files and directories in memory."""
        normalized = self._normalize_path(path)

        if not await self.exists(normalized):
            raise FileNotFoundError(f"Path not found: {path}")

        if normalized in self._files:
            raise NotADirectoryError(f"Path is not a directory: {path}")

        files: list[PathInfo] = []

        # Find all files and directories
        all_paths = set(self._files.keys()) | self._directories

        for item_path in all_paths:
            if recursive:
                # For recursive, check if path is ancestor
                try:
                    item_path.relative_to(normalized)
                except ValueError:
                    continue
            # For non-recursive, check if path is direct parent
            elif item_path.parent != normalized:
                continue

            if pattern and not fnmatch.fnmatch(item_path.name, pattern):
                continue

            is_file = item_path in self._files
            size = len(self._files[item_path]) if is_file else None

            path_info = PathInfo(
                path=item_path,
                exists=True,
                is_file=is_file,
                is_directory=not is_file,
                size=size,
            )
            files.append(path_info)

        return files

    async def read_file(
        self,
        path: Path,
        /,
        *,
        encoding: str | None = None,
        binary: bool = False,
    ) -> FileContent:
        """Read content from memory."""
        config = ctx.state(FileSystemConfig)
        normalized = self._normalize_path(path)

        if normalized not in self._files:
            raise FileNotFoundError(f"File not found: {path}")

        if not config.is_allowed_file(normalized):
            raise PermissionError(f"File type not allowed: {path}")

        raw_data = self._files[normalized]

        if len(raw_data) > config.max_file_size:
            raise ValueError(f"File too large: {len(raw_data)} bytes > {config.max_file_size}")

        if binary:
            data = raw_data
            file_encoding = None
        else:
            file_encoding = encoding or config.default_encoding
            data = raw_data.decode(file_encoding)

        return FileContent(
            data=data,
            encoding=file_encoding,
            path=normalized,
        )

    async def write_file(
        self,
        path: Path,
        content: bytes | str,
        /,
        *,
        encoding: str | None = None,
        create_parents: bool = True,
        overwrite: bool = True,
    ) -> None:
        """Write content to memory."""
        config = ctx.state(FileSystemConfig)
        normalized = self._normalize_path(path)

        if not config.is_allowed_file(normalized):
            raise PermissionError(f"File type not allowed: {path}")

        if normalized in self._files and not overwrite:
            raise FileExistsError(f"File already exists: {path}")

        if create_parents:
            self._ensure_parent_dirs(normalized)

        if isinstance(content, str):
            file_encoding = encoding or config.default_encoding
            raw_data = content.encode(file_encoding)
        else:
            raw_data = content

        if len(raw_data) > config.max_file_size:
            raise ValueError(f"File too large: {len(raw_data)} bytes > {config.max_file_size}")

        self._files[normalized] = raw_data
        self._timestamps[normalized] = time.time()

    def _create_read_handle(
        self, normalized: Path, mode: str, file_encoding: str | None
    ) -> TextIO | BinaryIO:
        """Create a file handle for reading."""
        if normalized not in self._files:
            raise FileNotFoundError(f"File not found: {normalized}")

        raw_data = self._files[normalized]

        if "b" in mode:
            return BytesIO(raw_data)
        else:
            text_data = raw_data.decode(file_encoding or "utf-8")
            return StringIO(text_data)

    def _create_write_handle(
        self, normalized: Path, mode: str, file_encoding: str | None
    ) -> TextIO | BinaryIO:
        """Create a file handle for writing."""
        if "w" in mode:
            self._ensure_parent_dirs(normalized)
            return BytesIO() if "b" in mode else StringIO()

        # Append mode
        if normalized in self._files:
            raw_data = self._files[normalized]
            if "b" in mode:
                handle = BytesIO(raw_data)
                handle.seek(0, 2)  # Seek to end
                return handle
            else:
                text_data = raw_data.decode(file_encoding or "utf-8")
                handle = StringIO(text_data)
                handle.seek(0, 2)  # Seek to end
                return handle

        return BytesIO() if "b" in mode else StringIO()

    def _save_content(
        self,
        normalized: Path,
        file_handle: TextIO | BinaryIO,
        mode: str,
        file_encoding: str | None,
    ) -> None:
        """Save content from file handle back to memory."""
        # BytesIO and StringIO have getvalue method, cast to access it
        content = cast(BytesIO | StringIO, file_handle).getvalue()

        if "b" in mode:
            if isinstance(content, bytes):
                self._files[normalized] = content
            else:
                self._files[normalized] = content.encode("utf-8")
        elif isinstance(content, str):
            self._files[normalized] = content.encode(file_encoding or "utf-8")
        else:
            self._files[normalized] = content

        self._timestamps[normalized] = time.time()

    @asynccontextmanager
    async def open_file(
        self,
        path: Path,
        mode: str = "r",
        /,
        *,
        encoding: str | None = None,
        buffering: int = -1,
    ) -> AsyncGenerator[TextIO | BinaryIO, None]:
        """Open a file-like object in memory."""
        config = ctx.state(FileSystemConfig)
        normalized = self._normalize_path(path)

        if not config.is_allowed_file(normalized):
            raise PermissionError(f"File type not allowed: {path}")

        file_encoding = encoding or (config.default_encoding if "b" not in mode else None)

        if "r" in mode:
            file_handle = self._create_read_handle(normalized, mode, file_encoding)
            try:
                yield file_handle
            finally:
                file_handle.close()
        else:
            file_handle = self._create_write_handle(normalized, mode, file_encoding)
            try:
                yield file_handle
            finally:
                self._save_content(normalized, file_handle, mode, file_encoding)
                file_handle.close()


def memory_file_system() -> FileSystem:
    """Create a FileSystem instance for in-memory operations."""
    memory_fs = MemoryFileSystem()

    return FileSystem(
        existing=memory_fs.exists,
        listing=memory_fs.list_files,
        reading=memory_fs.read_file,
        writing=memory_fs.write_file,
        opening=memory_fs.open_file,
    )
