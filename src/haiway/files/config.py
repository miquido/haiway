from collections.abc import Set
from pathlib import Path

from haiway import State

__all__ = ("FileSystemConfig",)


class FileSystemConfig(State):
    """Configuration for file system operations."""

    base_path: Path | None = None
    default_encoding: str = "utf-8"
    create_parents: bool = True
    overwrite_existing: bool = True
    max_file_size: int = 100 * 1024 * 1024  # 100MB
    allowed_extensions: Set[str] | None = None
    blocked_extensions: Set[str] = {".exe", ".bat", ".sh", ".com", ".cmd"}

    def resolve_path(self, path: Path) -> Path:
        """Resolve a path relative to the base path if configured."""
        if self.base_path is None:
            return path.resolve()

        if path.is_absolute():
            return path

        return (self.base_path / path).resolve()

    def is_allowed_file(self, path: Path) -> bool:
        """Check if a file is allowed based on extension restrictions."""
        suffix = path.suffix.lower()

        if suffix in self.blocked_extensions:
            return False

        if self.allowed_extensions is not None:
            return suffix in self.allowed_extensions

        return True
