import os
import sys
from asyncio import Lock
from collections.abc import Awaitable, MutableSequence, Sequence
from errno import ENOENT
from pathlib import Path
from types import TracebackType
from typing import Final, NamedTuple, Protocol, final, overload, runtime_checkable

from haiway.attributes import State
from haiway.helpers.asynchrony import asynchronous
from haiway.helpers.statemethods import statemethod

if sys.platform != "win32":
    from fcntl import LOCK_EX as _LOCK_EX
    from fcntl import LOCK_UN as _LOCK_UN
    from fcntl import flock

    LOCK_EX: Final[int] = _LOCK_EX
    LOCK_UN: Final[int] = _LOCK_UN


else:
    LOCK_EX: Final[int] = 0
    LOCK_UN: Final[int] = 0

    def flock(fd: int, operation: int, /) -> None:
        return None


__all__ = (
    "Directory",
    "File",
    "FileException",
    "Files",
    "Paths",
)


@final
class Paths(NamedTuple):
    """
    Traversed filesystem entries grouped by type.

    Attributes
    ----------
    files : Sequence[Path]
        Non-symlink file entries discovered during traversal.
    directories : Sequence[Path]
        Non-symlink directory entries discovered during traversal.
    """

    files: Sequence[Path]
    directories: Sequence[Path]


@final
class FileException(Exception):
    """
    File operation failure.

    Raised by file and directory helpers when opening, reading, writing,
    traversing, or closing resources fails.
    """


@runtime_checkable
class DirectoryTraversing(Protocol):
    """
    Protocol for asynchronous directory traversal operations.

    Implementations return traversed directory entries for the requested root
    path and traversal mode.
    """

    def __call__(
        self,
        recursive: bool,
    ) -> Awaitable[Paths]: ...


@runtime_checkable
class PathTraversing(Protocol):
    """
    Protocol for asynchronous directory traversal operations.

    Implementations return traversed directory entries for the requested root
    path and traversal mode.
    """

    def __call__(
        self,
        path: Path | str,
        recursive: bool,
    ) -> Awaitable[Paths]: ...


@runtime_checkable
class FileReading(Protocol):
    """
    Protocol for asynchronous file reading operations.

    Implementations read the entire file contents and return them as bytes.
    The file position is managed internally and reading always returns the
    complete file contents from the beginning.
    """

    async def __call__(
        self,
    ) -> bytes: ...


@runtime_checkable
class FileWriting(Protocol):
    """
    Protocol for asynchronous file writing operations.

    Implementations write the provided content to the file, completely
    replacing any existing content. In the default implementation, writes are
    synchronized only by the per-FileAccessContext lock and become
    cross-context or cross-process safe only when Files.access(...,
    exclusive=True) acquires the file lock. The default implementation also
    fsyncs after each in-place update, but it is not atomic across processes
    unless exclusive=True is used or the caller adopts an atomic temp-file
    rename pattern.
    """

    async def __call__(
        self,
        content: bytes,
    ) -> None: ...


@runtime_checkable
class FileAccess(Protocol):
    """
    Protocol for file context managers.

    Defines the interface for file context managers that handle the opening,
    access, and cleanup of file resources. Implementations ensure proper
    resource management and make file operations available through the File
    state class.

    The context manager pattern ensures that file handles are properly closed
    and locks are released even if exceptions occur during file operations.
    """

    @property
    def path(self) -> Path: ...

    async def __aenter__(self) -> File:
        """
        Enter the file context and return file operations.

        Returns
        -------
        File
            A File state instance configured for the opened file

        Raises
        ------
        FileException
            If the file cannot be opened
        """
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """
        Exit the file context and clean up resources.

        Parameters
        ----------
        exc_type : type[BaseException] | None
            The exception type if an exception occurred
        exc_val : BaseException | None
            The exception value if an exception occurred
        exc_tb : TracebackType | None
            The exception traceback if an exception occurred
        """
        ...


@runtime_checkable
class FileAccessing(Protocol):
    """
    Protocol for file access implementations.

    Defines the interface for creating file context managers with specific
    access patterns. Implementations handle the details of file opening,
    locking, and resource management.
    """

    def __call__(
        self,
        path: Path | str,
        create: bool,
        exclusive: bool,
    ) -> FileAccess: ...


@final
class File(State):
    """
    State container for file operations within a context scope.

    Provides access to file operations after a file has been opened using
    Files within a context scope. Follows Haiway's pattern of accessing
    functionality through class methods that retrieve state from the current context.

    The file operations are provided through the reading and writing protocol
    implementations, which are injected when the file is opened.
    """

    @overload
    @classmethod
    async def read(
        cls,
    ) -> bytes: ...

    @overload
    async def read(
        self,
    ) -> bytes: ...

    @statemethod
    async def read(
        self,
    ) -> bytes:
        """
        Read the complete contents of the file.

        Returns
        -------
        bytes
            The complete file contents as bytes

        Raises
        ------
        FileException
            If no file is currently open in the context
        """
        return await self._reading()

    @overload
    @classmethod
    async def write(
        cls,
        content: bytes,
        /,
    ) -> None: ...

    @overload
    async def write(
        self,
        content: bytes,
        /,
    ) -> None: ...

    @statemethod
    async def write(
        self,
        content: bytes,
        /,
    ) -> None:
        """
        Write content to the file, replacing existing content.

        Parameters
        ----------
        content : bytes
            The bytes content to write to the file

        Raises
        ------
        FileException
            If no file is currently open in the context
        """
        await self._writing(content)

    path: Path
    _reading: FileReading
    _writing: FileWriting

    def __init__(
        self,
        path: Path,
        reading: FileReading,
        writing: FileWriting,
    ) -> None:
        super().__init__(
            path=path,
            _reading=reading,
            _writing=writing,
        )


@final
class Directory(State):
    """
    State container for traversing a single directory root.

    The instance stores a root path and exposes `Directory.traverse()` as a
    context-aware statemethod so callers can list files and directories using
    the active Haiway scope.
    """

    @overload
    @classmethod
    async def traverse(
        cls,
        /,
        recursive: bool = False,
    ) -> Sequence[FileAccess | Directory]: ...

    @overload
    async def traverse(
        self,
        /,
        recursive: bool = False,
    ) -> Sequence[FileAccess | Directory]: ...

    @statemethod
    async def traverse(
        self,
        /,
        recursive: bool = False,
    ) -> Sequence[FileAccess | Directory]:
        """
        Traverse directory entries using configured traversal implementation.

        Parameters
        ----------
        recursive : bool, optional
            If True, include nested entries recursively. Default is False

        Returns
        -------
        Sequence[FileAccess | Directory]
            Traversed file access contexts and nested directory states.
        """
        paths: Paths = await self._traversing(recursive)
        return (
            *(
                FileAccessContext(
                    path=file,
                    create=False,
                    exclusive=False,
                )
                for file in paths.files
            ),
            *(
                Directory(
                    path=directory,
                    traversing=_directory_contents_traversal(directory),
                )
                for directory in paths.directories
            ),
        )

    path: Path
    _traversing: DirectoryTraversing

    def __init__(
        self,
        path: Path,
        traversing: DirectoryTraversing,
    ) -> None:
        super().__init__(
            path=path,
            _traversing=traversing,
        )


@asynchronous
def _open_file_handle(
    path: Path,
    *,
    create: bool,
    exclusive: bool,
) -> int:
    if exclusive and sys.platform == "win32":
        raise FileException("exclusive file locking is not supported on Windows")

    try:
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            flags: int = os.O_RDWR | os.O_CREAT

        else:
            flags = os.O_RDWR

        file_handle: int = os.open(path, flags)

        if exclusive:
            try:
                flock(file_handle, LOCK_EX)

            except OSError:
                os.close(file_handle)
                raise

        return file_handle

    except OSError as exc:
        if exc.errno == ENOENT and not create:
            raise FileException(f"File does not exist: {path}") from exc

        raise FileException(f"Failed to open file: {path}") from exc


@asynchronous
def _read_file_contents(
    file_handle: int,
) -> bytes:
    try:
        os.lseek(file_handle, 0, os.SEEK_SET)

        # Read all bytes to EOF, avoiding TOCTOU on externally changing file size.
        chunks: MutableSequence[bytes] = []
        while True:
            chunk: bytes = os.read(file_handle, 64 * 1024)
            if not chunk:
                break

            chunks.append(chunk)

        return b"".join(chunks)

    except OSError as exc:
        raise FileException("Failed to read file content") from exc


@asynchronous
def _write_file_contents(
    file_handle: int,
    *,
    content: bytes,
) -> None:
    try:
        os.lseek(file_handle, 0, os.SEEK_SET)

        # Write all bytes, handling partial writes
        offset: int = 0
        while offset < len(content):
            bytes_written: int = os.write(file_handle, content[offset:])
            if bytes_written == 0:
                raise FileException("Failed to write file content")

            offset += bytes_written

        os.ftruncate(file_handle, len(content))
        os.fsync(file_handle)

    except OSError as exc:
        raise FileException("Failed to write file content") from exc


@asynchronous
def _close_file_handle(
    file_handle: int,
    *,
    exclusive: bool,
) -> None:
    unlock_error: OSError | None = None
    if exclusive:
        try:
            flock(file_handle, LOCK_UN)

        except OSError as exc:
            unlock_error = exc

    try:
        os.close(file_handle)

    except OSError as exc:
        if unlock_error is not None:
            exc.add_note("unlock with flock(..., LOCK_UN) failed before close")

        raise FileException("Failed to close file handle") from exc

    if unlock_error is not None:
        raise FileException("Failed to unlock file handle") from unlock_error


@final
class FileAccessContext:
    __slots__ = (
        "_create",
        "_exclusive",
        "_file_handle",
        "_lock",
        "path",
    )

    def __init__(
        self,
        path: Path | str,
        create: bool,
        exclusive: bool,
    ) -> None:
        self.path: Path = Path(path)
        self._create: bool = create
        self._exclusive: bool = exclusive
        self._file_handle: int | None = None
        self._lock: Lock = Lock()

    async def __aenter__(self) -> File:
        assert self._file_handle is None  # nosec: B101

        self._file_handle = await _open_file_handle(
            self.path,
            create=self._create,
            exclusive=self._exclusive,
        )

        async def read_file() -> bytes:
            assert self._file_handle is not None  # nosec: B101
            async with self._lock:
                return await _read_file_contents(
                    self._file_handle,
                )

        async def write_file(content: bytes) -> None:
            assert self._file_handle is not None  # nosec: B101
            async with self._lock:
                await _write_file_contents(
                    self._file_handle,
                    content=content,
                )

        return File(
            path=self.path,
            reading=read_file,
            writing=write_file,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._file_handle is not None  # nosec: B101
        try:
            await _close_file_handle(
                self._file_handle,
                exclusive=self._exclusive,
            )

        finally:
            self._file_handle = None


@asynchronous
def _traverse_path_contents(  # noqa: C901, PLR0912
    path: Path | str,
    recursive: bool,
) -> Paths:
    root: Path = Path(path)
    if not root.exists():
        raise FileException(f"Directory does not exist: {root}")

    if not root.is_dir():
        raise FileException(f"Path is not a directory: {root}")

    files: MutableSequence[Path] = []
    directories: MutableSequence[Path] = []
    try:
        if recursive:
            for current_root, dir_names, file_names in os.walk(root):
                current: Path = Path(current_root)

                for file_name in file_names:
                    entry: Path = current / file_name
                    if entry.is_symlink():
                        continue  # skip symlinks

                    assert entry.is_file()  # nosec: B101
                    files.append(entry)

                for directory in dir_names:
                    entry: Path = current / directory
                    if entry.is_symlink():
                        continue  # skip symlinks

                    assert entry.is_dir()  # nosec: B101
                    directories.append(entry)

        else:
            for entry in root.iterdir():
                if entry.is_symlink():
                    continue  # skip symlinks

                elif entry.is_dir():
                    directories.append(entry)

                elif entry.is_file():
                    files.append(entry)

                else:
                    raise RuntimeError(f"Unsupported directory item at {entry}")

        return Paths(tuple(files), tuple(directories))

    except OSError as exc:
        raise FileException(f"Failed to traverse directory: {root}") from exc


def _directory_contents_traversal(
    path: Path | str,
) -> DirectoryTraversing:
    async def _traverse_directory_contents(
        recursive: bool,
    ) -> Paths:
        return await _traverse_path_contents(
            path=path,
            recursive=recursive,
        )

    return _traverse_directory_contents


@final
class Files(State):
    """
    State container for filesystem traversal and file access helpers.

    Provides statemethod APIs for directory traversal and scoped file access.
    The implementation is injected to keep boundaries testable and to allow
    custom adapters.
    """

    @overload
    @classmethod
    async def traverse(
        cls,
        /,
        path: Path | str,
        *,
        recursive: bool = False,
    ) -> Sequence[FileAccess | Directory]: ...

    @overload
    async def traverse(
        self,
        /,
        path: Path | str,
        *,
        recursive: bool = False,
    ) -> Sequence[FileAccess | Directory]: ...

    @statemethod
    async def traverse(
        self,
        /,
        path: Path | str,
        *,
        recursive: bool = False,
    ) -> Sequence[FileAccess | Directory]:
        """
        Traverse directory entries using configured traversal implementation.

        Parameters
        ----------
        path : Path | str
            Root directory path to traverse
        recursive : bool, optional
            If True, include nested entries recursively. Default is False

        Returns
        -------
        Sequence[FileAccess | Directory]
            Traversed file access contexts and nested directory states.
        """
        paths: Paths = await self._traversing(path, recursive)
        return (
            *(
                FileAccessContext(
                    path=file,
                    create=False,
                    exclusive=False,
                )
                for file in paths.files
            ),
            *(
                Directory(
                    path=directory,
                    traversing=_directory_contents_traversal(directory),
                )
                for directory in paths.directories
            ),
        )

    @overload
    @classmethod
    def access(  # pyright: ignore[reportInconsistentOverload]
        cls,
        /,
        path: Path | str,
        *,
        create: bool = False,
        exclusive: bool = False,
    ) -> FileAccess: ...

    @overload
    def access(
        self,
        /,
        path: Path | str,
        *,
        create: bool = False,
        exclusive: bool = False,
    ) -> FileAccess: ...

    @statemethod
    def access(
        self,
        /,
        path: Path | str,
        *,
        create: bool = False,
        exclusive: bool = False,
    ) -> FileAccess:
        """
        Prepare access to a file for reading and writing.

        Opens a file using the configured file access implementation. The file
        is opened with read and write permissions, and the entire file content
        is available through the File.read() and File.write() methods.

        Parameters
        ----------
        path : Path | str
            The file path to open, as a Path object or string
        create : bool, optional
            If True, create the file and parent directories if they don't exist.
            If False, raise FileException for missing files. Default is False
        exclusive : bool, optional
            If True, acquire an exclusive lock on the file for the duration of
            the context. This prevents other processes from accessing the file
            concurrently. Default is False

        Returns
        -------
        FileAccess
            A file access context manager that manages the file lifecycle and
            provides access to file operations through the File state class.

        Raises
        ------
        FileException
            If the file cannot be opened with the specified parameters.
        """
        return self._accessing(
            path,
            create=create,
            exclusive=exclusive,
        )

    _traversing: PathTraversing
    _accessing: FileAccessing

    def __init__(
        self,
        traversing: PathTraversing = _traverse_path_contents,
        accessing: FileAccessing = FileAccessContext,
    ) -> None:
        super().__init__(
            _traversing=traversing,
            _accessing=accessing,
        )
