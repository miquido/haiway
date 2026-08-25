import os
import sys
from asyncio import Future, Lock, Task, ensure_future, shield, sleep
from collections.abc import Awaitable, Coroutine, Iterable, MutableSequence, Sequence
from errno import ELOOP, EMLINK, ENOENT, ENOTDIR
from pathlib import Path
from stat import S_ISLNK, S_ISREG
from types import TracebackType
from typing import Any, Final, NamedTuple, Protocol, final, overload, runtime_checkable

from haiway.attributes import State
from haiway.helpers.asynchrony import asynchronous
from haiway.helpers.statemethods import statemethod

if sys.platform != "win32":
    from fcntl import LOCK_EX as _LOCK_EX
    from fcntl import LOCK_NB as _LOCK_NB
    from fcntl import LOCK_UN as _LOCK_UN
    from fcntl import flock

    LOCK_EX: Final[int] = _LOCK_EX
    LOCK_NB: Final[int] = _LOCK_NB
    LOCK_UN: Final[int] = _LOCK_UN
else:
    LOCK_EX: Final[int] = 0
    LOCK_NB: Final[int] = 0
    LOCK_UN: Final[int] = 0

    def flock(fd: int, operation: int, /) -> None:
        return None


# O_NOFOLLOW is POSIX only - where it is missing the open follows a link like any
# other path, so the entry has to be examined before it, see `_refused_link`
NO_FOLLOW: Final[int] = getattr(os, "O_NOFOLLOW", 0)
# platforms disagree on which error a refused symlink produces
_NO_FOLLOW_ERRORS: Final[frozenset[int]] = frozenset((ELOOP, EMLINK))
# flags for stepping into a single path component - the component has to be a
# directory and must not be a link, so a planted link is refused instead of
# silently redirecting everything below it
_DIRECTORY_FLAGS: Final[int] = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | NO_FOLLOW
# opening relative to a directory descriptor is POSIX only - without it each
# path can only be resolved in full, leaving the ancestors up to the platform
_DIRECTORY_HANDLES: Final[bool] = (
    NO_FOLLOW != 0 and os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd
)
# waiting for a file lock is polled from the event loop, backing off between the
# attempts - see `_lock_file_handle` for why it can't simply block
_LOCK_RETRY_MIN: Final[float] = 0.005
_LOCK_RETRY_MAX: Final[float] = 0.25


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
class PathTraversing(Protocol):
    """
    Protocol for asynchronous directory traversal operations.
    Implementations list the entries of the requested root path - either its
    direct children or its whole subtree - grouped into files and directories.
    Symbolic links, and entries which are neither a regular file nor a
    directory, are expected to be left out.
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
        mode: int,
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
            If reading the file fails
        ContextStateMissing
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
            If writing the file fails
        ContextStateMissing
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


@asynchronous
def _open_file_handle(
    path: Path,
    *,
    create: bool,
    exclusive: bool,
    mode: int = 0o600,
) -> int:
    if exclusive and sys.platform == "win32":
        raise FileException("exclusive file locking is not supported on Windows")
    if not path.name:
        raise FileException(f"Not a file path: {path}")
    try:
        # never traverse a symlink - a link planted at the target would otherwise
        # redirect reads and writes to a file chosen by whoever created it
        flags: int = os.O_RDWR | NO_FOLLOW
        if create:
            flags |= os.O_CREAT

        if _refused_link(path):
            raise FileException(f"File is a symbolic link: {path}")

        file_handle: int = _open_file(
            path,
            flags=flags,
            create=create,
            mode=mode,
        )
        try:
            # O_NOFOLLOW refuses a link and nothing else - a fifo or a device
            # planted at the target would still be opened, and read unbounded
            if not S_ISREG(os.fstat(file_handle).st_mode):
                raise FileException(f"Not a regular file: {path}")

        except BaseException:
            os.close(file_handle)
            raise

        return file_handle

    except OSError as exc:
        if exc.errno == ENOENT and not create:
            raise FileException(f"File does not exist: {path}") from exc
        if exc.errno in _NO_FOLLOW_ERRORS and path.is_symlink():
            raise FileException(f"File is a symbolic link: {path}") from exc
        raise FileException(f"Failed to open file: {path}") from exc


async def _opened_file_handle(
    path: Path,
    *,
    create: bool,
    exclusive: bool,
    mode: int,
) -> int:
    # the descriptor is produced in an executor thread which cancellation cannot
    # reach - shielding keeps its outcome observable, so an open which nobody is
    # waiting for any more can still be closed instead of leaking
    opening: Task[int] = ensure_future(
        _open_file_handle(
            path,
            create=create,
            exclusive=exclusive,
            mode=mode,
        )
    )
    try:
        return await shield(opening)

    except BaseException:
        opening.add_done_callback(_discard_file_handle)
        raise


def _discard_file_handle(
    opening: Future[int],
    /,
) -> None:
    if opening.cancelled() or opening.exception() is not None:
        return  # nothing was opened

    try:
        os.close(opening.result())

    except OSError:
        pass  # abandoned already, there is nobody left to tell it failed


async def _lock_file_handle(
    file_handle: int,
    /,
) -> None:
    # a blocking LOCK_EX would hold an executor thread which cancellation cannot
    # reach and which the interpreter still waits for on shutdown - polling the
    # non blocking variant keeps the wait in the event loop, where giving up on
    # it releases the descriptor instead of leaking it with the lock held
    delay: float = _LOCK_RETRY_MIN
    while True:
        try:
            return flock(file_handle, LOCK_EX | LOCK_NB)

        except BlockingIOError:
            await sleep(delay)
            delay = min(2 * delay, _LOCK_RETRY_MAX)

        except OSError as exc:
            raise FileException("Failed to lock file handle") from exc


def _open_file(
    path: Path,
    /,
    *,
    flags: int,
    create: bool,
    mode: int,
) -> int:
    # directories carry the executable bit of the requested file mode, so they
    # stay traversable for exactly the same principals
    directory_mode: int = _directory_mode(mode)
    if not _DIRECTORY_HANDLES:
        if create:
            _create_directory(
                path.parent,
                mode=directory_mode,
            )

        # no directory handle to sync here, so a freshly created entry is left
        # exactly as durable as the platform chooses to make it
        return os.open(path, flags, mode)

    # the parent chain is walked one component at a time, so the file is opened
    # relative to a directory reached without traversing a single link
    directory_handle: int = _open_directory_handle(
        path.parent,
        create=create,
        mode=directory_mode,
    )
    try:
        file_handle: int = os.open(path.name, flags, mode, dir_fd=directory_handle)
        if create:
            try:
                # syncing the contents of a new file does not make the entry
                # pointing at it outlive a crash - the directory holds that
                os.fsync(directory_handle)

            except OSError:
                os.close(file_handle)
                raise

        return file_handle

    finally:
        os.close(directory_handle)


def _open_directory_handle(
    path: Path,
    /,
    *,
    create: bool,
    mode: int,
) -> int:
    # a relative path is anchored at the working directory, which the operating
    # system reports already resolved - the walk can start at the anchor itself
    absolute: Path = path if path.is_absolute() else Path(os.getcwd(), path)
    traversed: Path = Path(absolute.anchor)
    handle: int = os.open(traversed, _DIRECTORY_FLAGS)
    try:
        for part in absolute.parts[1:]:
            traversed = traversed / part
            next_handle: int = _open_subdirectory(
                part,
                path=traversed,
                directory_handle=handle,
                create=create,
                mode=mode,
            )
            os.close(handle)
            handle = next_handle

        return handle

    except BaseException:
        os.close(handle)
        raise


def _open_subdirectory(
    name: str,
    /,
    *,
    path: Path,
    directory_handle: int,
    create: bool,
    mode: int,
) -> int:
    try:
        return _open_directory(
            name,
            path=path,
            directory_handle=directory_handle,
        )

    except OSError as exc:
        # a link pointing nowhere is refused as a link, never treated as missing
        if exc.errno != ENOENT or not create:
            raise

    try:
        os.mkdir(name, mode, dir_fd=directory_handle)

    except FileExistsError:
        pass  # created concurrently - its mode is up to whoever won

    return _open_directory(
        name,
        path=path,
        directory_handle=directory_handle,
    )


def _open_directory(
    name: str,
    /,
    *,
    path: Path,
    directory_handle: int,
) -> int:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_handle)

    except OSError as exc:
        # asking for a directory makes some platforms report a refused link as
        # "not a directory" instead - the entry itself tells the two apart, and
        # the refusal already happened, this only explains it
        if exc.errno in _NO_FOLLOW_ERRORS or (
            exc.errno == ENOTDIR
            and _is_link(
                name,
                directory_handle=directory_handle,
            )
        ):
            raise FileException(f"Directory is a symbolic link: {path}") from exc

        raise


def _refused_link(
    path: Path,
    /,
) -> bool:
    # without O_NOFOLLOW nothing stops the open from resolving a link, so the
    # entry is described up front instead - the check cannot be atomic with the
    # open which follows it, yet a link left in place is still refused
    if NO_FOLLOW != 0:
        return False  # the open refuses it, and does so without a race

    try:
        return path.is_symlink()

    except OSError:
        return False  # unreadable - the open reports what is wrong with it


def _is_link(
    name: str,
    /,
    *,
    directory_handle: int,
) -> bool:
    try:
        return S_ISLNK(os.lstat(name, dir_fd=directory_handle).st_mode)

    except OSError:
        return False  # gone already - it can no longer be described


def _create_directory(
    path: Path,
    /,
    *,
    mode: int,
) -> None:
    # used where opening relative to a directory descriptor is unavailable -
    # `Path.mkdir(parents=True)` creates the missing ancestors without passing
    # the mode on, leaving them at the process umask default - each level is
    # created on its own instead, so the whole chain is restricted alike
    missing: list[Path] = []
    current: Path = path
    while not current.exists():
        missing.append(current)
        parent: Path = current.parent
        if parent == current:
            break  # reached the anchor - nothing above it can be created

        current = parent

    for directory in reversed(missing):
        try:
            directory.mkdir(mode=mode)

        except FileExistsError:
            pass  # created concurrently - its mode is up to whoever won


def _directory_mode(
    mode: int,
    /,
) -> int:
    # grant search permission wherever the file grants read or write
    directory_mode: int = mode
    for read_write, execute in ((0o600, 0o100), (0o060, 0o010), (0o006, 0o001)):
        if mode & read_write:
            directory_mode |= execute

    return directory_mode


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


async def _close_file_handle(
    file_handle: int,
    *,
    exclusive: bool,
) -> None:
    # deliberately without an executor hop - unlocking and closing return at
    # once, while an awaited hop could be abandoned by cancellation, leaking a
    # descriptor which still holds the lock
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
        "_mode",
        "path",
    )

    def __init__(
        self,
        path: Path | str,
        create: bool,
        exclusive: bool,
        mode: int = 0o600,
    ) -> None:
        self.path: Path = Path(path)
        self._create: bool = create
        self._exclusive: bool = exclusive
        self._mode: int = mode
        self._file_handle: int | None = None
        self._lock: Lock = Lock()

    async def __aenter__(self) -> File:
        assert self._file_handle is None  # nosec: B101
        file_handle: int = await _opened_file_handle(
            self.path,
            create=self._create,
            exclusive=self._exclusive,
            mode=self._mode,
        )
        try:
            if self._exclusive:
                await _lock_file_handle(file_handle)

        except BaseException:
            # cancellation included - waiting for the lock is meant to be given
            # up on, the descriptor opened for it is not meant to be left behind
            await _close_file_handle(file_handle, exclusive=False)
            raise

        self._file_handle = file_handle

        async def read_file() -> bytes:
            assert self._file_handle is not None  # nosec: B101
            return await self._exclusively(
                _read_file_contents(
                    self._file_handle,
                )
            )

        async def write_file(content: bytes) -> None:
            assert self._file_handle is not None  # nosec: B101
            await self._exclusively(
                _write_file_contents(
                    self._file_handle,
                    content=content,
                )
            )

        return File(
            path=self.path,
            reading=read_file,
            writing=write_file,
        )

    async def _exclusively[Result](
        self,
        operation: Coroutine[Any, Any, Result],
        /,
    ) -> Result:
        # the descriptor carries a single shared offset, and an operation which
        # its caller stopped waiting for keeps running in its executor thread -
        # the lock is held until it completes rather than until it is awaited
        try:
            await self._lock.acquire()

        except BaseException:
            operation.close()  # never started, so it has nothing to unwind
            raise

        running: Task[Result] = ensure_future(operation)
        running.add_done_callback(lambda _: self._lock.release())
        return await shield(running)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._file_handle is not None  # nosec: B101
        file_handle: int = self._file_handle
        self._file_handle = None
        try:
            # an abandoned operation outlives its caller - closing underneath it
            # would hand its bytes to whatever reuses the descriptor number
            await self._lock.acquire()

        finally:
            await _close_file_handle(
                file_handle,
                exclusive=self._exclusive,
            )


@asynchronous
def _traverse_path_contents(
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
            # os.walk discards errors unless told otherwise, which would turn an
            # unreadable subtree into a silently incomplete result
            for current_root, dir_names, file_names in os.walk(
                root,
                onerror=_traversal_failure,
            ):
                current: Path = Path(current_root)
                _collect(
                    (current / name for name in (*dir_names, *file_names)),
                    files=files,
                    directories=directories,
                )

        else:
            _collect(
                root.iterdir(),
                files=files,
                directories=directories,
            )

        return Paths(tuple(files), tuple(directories))

    except OSError as exc:
        raise FileException(f"Failed to traverse directory: {root}") from exc


def _traversal_failure(
    error: OSError,
    /,
) -> None:
    raise error


def _collect(
    entries: Iterable[Path],
    /,
    *,
    files: MutableSequence[Path],
    directories: MutableSequence[Path],
) -> None:
    for entry in entries:
        if entry.is_symlink():
            continue  # links are refused everywhere else, they are no result here

        elif entry.is_dir():
            directories.append(entry)

        elif entry.is_file():
            files.append(entry)

        # anything else - a socket, a fifo, a device - is not a traversal result


@final
class Directory(State):
    """
    State container for traversing a single directory root.
    The instance stores a root path and exposes `Directory.traverse()` as a
    context-aware statemethod so callers can list files and directories using
    the active Haiway scope. Both implementations are carried over to every
    nested directory, so a custom backend stays in use across a whole subtree.
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
        return _traversed(
            await self._traversing(self.path, recursive),
            traversing=self._traversing,
            accessing=self._accessing,
        )

    path: Path
    _traversing: PathTraversing
    _accessing: FileAccessing

    def __init__(
        self,
        path: Path | str,
        traversing: PathTraversing = _traverse_path_contents,
        accessing: FileAccessing = FileAccessContext,
    ) -> None:
        super().__init__(
            path=Path(path),
            _traversing=traversing,
            _accessing=accessing,
        )


def _traversed(
    paths: Paths,
    /,
    *,
    traversing: PathTraversing,
    accessing: FileAccessing,
) -> Sequence[FileAccess | Directory]:
    return (
        # a traversed entry exists, nothing is created and the mode is unused
        *(accessing(file, create=False, exclusive=False, mode=0o600) for file in paths.files),
        *(Directory(directory, traversing, accessing) for directory in paths.directories),
    )


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
        return _traversed(
            await self._traversing(path, recursive),
            traversing=self._traversing,
            accessing=self._accessing,
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
        mode: int = 0o600,
    ) -> FileAccess: ...
    @overload
    def access(
        self,
        /,
        path: Path | str,
        *,
        create: bool = False,
        exclusive: bool = False,
        mode: int = 0o600,
    ) -> FileAccess: ...
    @statemethod
    def access(
        self,
        /,
        path: Path | str,
        *,
        create: bool = False,
        exclusive: bool = False,
        mode: int = 0o600,
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
            If True, acquire an exclusive ``flock`` for the duration of the
            context, waiting for a conflicting holder to release it. The lock is
            advisory - it excludes other holders of the same lock rather than
            every access to the file - and it is unavailable on Windows.
            Default is False
        mode : int, optional
            Permission bits applied to a file created by this call, defaulting
            to ``0o600`` - access limited to the current user, narrowed further
            by the process umask. Parent directories created along the way
            receive the same permissions extended with search access wherever
            read or write is granted. Ignored for a file which already exists,
            its permissions are never modified. Applied on POSIX platforms only
            - Windows has no equivalent permission bits.
        Returns
        -------
        FileAccess
            A file access context manager that manages the file lifecycle and
            provides access to file operations through the File state class.
        Raises
        ------
        FileException
            If the file cannot be opened with the specified parameters, if the
            path is not a regular file, or if the path or any of its parent
            directories is a symbolic link - links are never traversed.
        Notes
        -----
        Symbolic links are refused rather than followed, so a link planted at
        the target path cannot redirect reads or writes to another file. On
        POSIX platforms the same holds for every parent directory: each
        component is opened relative to the previous one and refused when it
        turns out to be a link, which also requires read access to each of
        them. Only regular files are accepted, so a fifo or a device planted at
        the target is refused as well. Path components are not normalized - a
        ``..`` still leads out of the directory preceding it.
        """
        return self._accessing(
            path,
            create=create,
            exclusive=exclusive,
            mode=mode,
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
