try:
    import fcntl

except ModuleNotFoundError:  # Windows does not supprt fcntl
    fcntl = None

import os
from asyncio import Lock
from pathlib import Path
from types import TracebackType
from typing import Protocol, runtime_checkable

from haiway.context import ctx
from haiway.helpers.asynchrony import asynchronous
from haiway.state import State

__all__ = (
    "File",
    "FileAccess",
    "FileAccessing",
    "FileContext",
    "FileException",
    "FileReading",
    "FileWriting",
)


class FileException(Exception):
    """
    Exception raised for file operation errors.

    Raised when file operations fail, such as attempting to access
    a non-existent file without the create flag, or when file I/O
    operations encounter errors.
    """


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
    replacing any existing content. The write operation is atomic and
    includes proper synchronization to ensure data is written to disk.
    """

    async def __call__(
        self,
        content: bytes,
    ) -> None: ...


class File(State):
    """
    State container for file operations within a context scope.

    Provides access to file operations after a file has been opened using
    FileAccess within a context scope. Follows Haiway's pattern of accessing
    functionality through class methods that retrieve state from the current context.

    The file operations are provided through the reading and writing protocol
    implementations, which are injected when the file is opened.
    """

    @classmethod
    async def read(
        cls,
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
        return await ctx.state(cls).reading()

    @classmethod
    async def write(
        cls,
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
        await ctx.state(cls).writing(content)

    reading: FileReading
    writing: FileWriting


@runtime_checkable
class FileContext(Protocol):
    """
    Protocol for file context managers.

    Defines the interface for file context managers that handle the opening,
    access, and cleanup of file resources. Implementations ensure proper
    resource management and make file operations available through the File
    state class.

    The context manager pattern ensures that file handles are properly closed
    and locks are released even if exceptions occur during file operations.
    """

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
    ) -> bool | None:
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

        Returns
        -------
        bool | None
            None to allow exceptions to propagate
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

    async def __call__(
        self,
        path: Path | str,
        create: bool,
        exclusive: bool,
    ) -> FileContext: ...


@asynchronous
def _open_file_handle(
    path: Path,
    *,
    create: bool,
    exclusive: bool,
) -> int:
    file_handle: int
    if path.exists():
        file_handle = os.open(path, os.O_RDWR)

    elif create:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handle = os.open(path, os.O_RDWR | os.O_CREAT)

    else:
        raise FileException(f"File does not exist: {path}")

    if exclusive and fcntl is not None:
        fcntl.flock(file_handle, fcntl.LOCK_EX)  # pyright: ignore[reportAttributeAccessIssue] # windows

    return file_handle


@asynchronous
def _read_file_contents(
    file_handle: int,
    *,
    path: Path,
) -> bytes:
    os.lseek(file_handle, 0, os.SEEK_SET)
    bytes_remaining: int = path.stat().st_size

    # Read all bytes, handling partial reads
    chunks: list[bytes] = []
    while bytes_remaining > 0:
        chunk: bytes = os.read(file_handle, bytes_remaining)
        if not chunk:
            raise FileException("Unexpected end of file during read")

        bytes_remaining -= len(chunk)
        chunks.append(chunk)

    return b"".join(chunks)


@asynchronous
def _write_file_contents(
    file_handle: int,
    *,
    content: bytes,
) -> None:
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


@asynchronous
def _close_file_handle(
    file_handle: int,
    *,
    exclusive: bool,
) -> None:
    if exclusive and fcntl is not None:
        fcntl.flock(file_handle, fcntl.LOCK_UN)  # pyright: ignore[reportAttributeAccessIssue] # windows

    os.close(file_handle)


async def _file_access_context(
    path: Path | str,
    create: bool,
    exclusive: bool,
) -> FileContext:
    file_path: Path = Path(path)

    class FileAccessContext:
        __slots__ = (
            "_file_handle",
            "_file_path",
            "_lock",
        )

        def __init__(
            self,
            path: Path | str,
        ) -> None:
            self._file_handle: int | None = None
            self._file_path: Path = Path(path)
            self._lock: Lock = Lock()

        async def __aenter__(self) -> File:
            assert self._file_handle is None  # nosec: B101

            self._file_handle = await _open_file_handle(
                file_path,
                create=create,
                exclusive=exclusive,
            )

            async def read_file() -> bytes:
                assert self._file_handle is not None  # nosec: B101
                async with self._lock:
                    return await _read_file_contents(
                        self._file_handle,
                        path=file_path,
                    )

            async def write_file(content: bytes) -> None:
                assert self._file_handle is not None  # nosec: B101
                async with self._lock:
                    await _write_file_contents(
                        self._file_handle,
                        content=content,
                    )

            return File(
                reading=read_file,
                writing=write_file,
            )

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> bool | None:
            assert self._file_handle is not None  # nosec: B101
            await _close_file_handle(
                self._file_handle,
                exclusive=exclusive,
            )

    return FileAccessContext(path=path)


class FileAccess(State):
    """
    State container for file access configuration within a context scope.

    Provides the entry point for file operations within Haiway's context system.
    Follows the framework's pattern of using state classes to configure behavior
    that can be injected and replaced for testing.

    File access is scoped to the context, meaning only one file can be open
    at a time within a given context scope. This design ensures predictable
    resource usage and simplifies error handling.

    The default implementation provides standard filesystem access with
    optional file creation and exclusive locking. Custom implementations
    can be injected by replacing the accessing function.

    Examples
    --------
    Basic file operations:

    >>> async with ctx.scope("app", disposables=(FileAccess.open("config.json", create=True),)):
    ...     data = await File.read()
    ...     await File.write(b'{"setting": "value"}')

    Exclusive file access for critical operations:

    >>> async with ctx.scope("app", disposables=(FileAccess.open("config.json", exclusive=True),)):
    ...     content = await File.read()
    ...     processed = process_data(content)
    ...     await File.write(processed)
    """

    @classmethod
    async def open(
        cls,
        path: Path | str,
        create: bool = False,
        exclusive: bool = False,
    ) -> FileContext:
        """
        Open a file for reading and writing.

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
        FileContext
            A FileContext that manages the file lifecycle and provides access
            to file operations through the File state class

        Raises
        ------
        FileException
            If the file cannot be opened with the specified parameters, or if
            a file is already open in the current context scope
        """
        return await ctx.state(cls).accessing(
            path,
            create=create,
            exclusive=exclusive,
        )

    accessing: FileAccessing = _file_access_context
