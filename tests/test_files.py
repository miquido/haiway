import asyncio
import os
import stat
import sys
import threading
import time
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from haiway import Directory, File, Files, ctx
from haiway.helpers import files as files_module
from haiway.helpers.files import FileAccess, FileException


class OSModuleStub:
    """
    Stand-in for the ``os`` module as seen by the module under test.

    Overrides only the named functions and delegates everything else to the real
    module, so patching stays contained to ``haiway.helpers.files`` instead of
    breaking ``os`` for the whole process.
    """

    def __init__(
        self,
        **overrides: Any,
    ) -> None:
        self.__dict__.update(overrides)

    def __getattr__(
        self,
        name: str,
    ) -> Any:
        return getattr(os, name)


@pytest.fixture
def pinned_umask() -> Generator[None]:
    """
    Pin the process umask so that requested file modes are applied verbatim.

    The kernel creates files with ``mode & ~umask``, which makes any assertion on
    exact permission bits depend on the umask of whoever runs the tests.
    """

    previous: int = os.umask(0o022)
    try:
        yield

    finally:
        os.umask(previous)


def assert_permissions(
    path: Path,
    expected: int,
) -> None:
    # Windows has no POSIX permission bits - a file reports 0o666 (0o444 once the
    # read-only attribute is set) no matter which mode it was created with, so the
    # only part of the expected mode observable there is the write access it grants
    if sys.platform == "win32":
        writable: int = path.stat().st_mode & stat.S_IWRITE
        assert bool(writable) is bool(expected & 0o200)

    else:
        assert stat.S_IMODE(path.stat().st_mode) == expected


@pytest.mark.asyncio
async def test_file_access_read_write_roundtrip(tmp_path: Path) -> None:
    file_path = tmp_path / "payload.bin"
    files_access = Files()

    async with ctx.scope(
        "file-roundtrip",
        files_access,
        disposables=(files_access.access(file_path, create=True),),
    ):
        assert await File.read() == b""
        await File.write(b"hello")
        assert await File.read() == b"hello"


@pytest.mark.asyncio
async def test_file_access_export_alias(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    root.mkdir()
    nested.mkdir()
    first = root / "a.txt"
    second = root / "b.txt"
    first.write_text("a")
    second.write_text("b")

    directory = Directory(path=str(root))
    async with ctx.scope("directory-traverse-alias", directory):
        traversed = await Directory.traverse()

    files = {entry.path for entry in traversed if isinstance(entry, FileAccess)}
    directories = {Path(entry.path) for entry in traversed if isinstance(entry, Directory)}

    assert files == {first, second}
    assert directories == {nested}


@pytest.mark.asyncio
async def test_open_file_handle_rejects_exclusive_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_path = tmp_path / "locked.bin"
    monkeypatch.setattr(files_module, "sys", SimpleNamespace(platform="win32"))

    with pytest.raises(FileException, match="exclusive file locking is not supported on Windows"):
        await files_module._open_file_handle(file_path, create=True, exclusive=True)


@pytest.mark.asyncio
async def test_read_file_contents_translates_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raising_read(fd: int, count: int, /, *offset: int) -> bytes:
        raise OSError("read failed")

    monkeypatch.setattr(files_module, "os", OSModuleStub(read=raising_read, pread=raising_read))
    # a real descriptor - the implementation inspects it before reading
    file_handle: int = os.open(tmp_path / "readable.bin", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with pytest.raises(FileException, match="Failed to read file content") as exc_info:
            await files_module._read_file_contents(file_handle)

    finally:
        os.close(file_handle)

    assert isinstance(exc_info.value.__cause__, OSError)
    # the failure comes from reading, not from anything preceding it
    assert str(exc_info.value.__cause__) == "read failed"


@pytest.mark.asyncio
async def test_write_file_contents_translates_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raising_write(fd: int, data: bytes, /, *offset: int) -> int:
        raise OSError("write failed")

    monkeypatch.setattr(files_module, "os", OSModuleStub(write=raising_write, pwrite=raising_write))
    # a real descriptor - the implementation seeks before writing
    file_handle: int = os.open(tmp_path / "writable.bin", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with pytest.raises(FileException, match="Failed to write file content") as exc_info:
            await files_module._write_file_contents(file_handle, content=b"abc")

    finally:
        os.close(file_handle)

    assert isinstance(exc_info.value.__cause__, OSError)
    # the failure comes from writing, not from anything preceding it
    assert str(exc_info.value.__cause__) == "write failed"


@pytest.mark.asyncio
async def test_close_file_handle_translates_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raising_close(fd: int, /) -> None:
        raise OSError("close failed")

    monkeypatch.setattr(files_module, "os", OSModuleStub(close=raising_close))
    file_handle: int = os.open(tmp_path / "closable.bin", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with pytest.raises(FileException, match="Failed to close file handle") as exc_info:
            files_module._close_file_handle(file_handle, exclusive=False)

    finally:
        os.close(file_handle)

    assert isinstance(exc_info.value.__cause__, OSError)
    assert str(exc_info.value.__cause__) == "close failed"


@pytest.mark.asyncio
async def test_file_handle_is_cleared_even_if_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_close(file_handle: int, *, exclusive: bool) -> None:
        raise FileException("closing failed")

    monkeypatch.setattr(files_module, "_close_file_handle", failing_close)
    context = files_module.FileAccessContext(
        tmp_path / "resource.bin",
        create=True,
        exclusive=False,
    )

    await context.__aenter__()
    file_handle: int = context._file_handle  # pyright: ignore[reportAttributeAccessIssue]
    try:
        with pytest.raises(FileException, match="closing failed"):
            await context.__aexit__(None, None, None)

        assert context._file_handle is None  # pyright: ignore[reportAttributeAccessIssue]

    finally:
        # the patched close never ran, the descriptor is still ours to release
        os.close(file_handle)


@pytest.mark.asyncio
async def test_files_access_traverse_direct_children(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    root.mkdir()
    nested.mkdir()
    first = root / "a.txt"
    second = root / "b.txt"
    first.write_text("a")
    second.write_text("b")
    (nested / "c.txt").write_text("c")

    files_access = Files()
    async with ctx.scope("files-direct", files_access):
        traversed = await Files.traverse(root)

    assert isinstance(traversed, tuple)
    files = {entry.path for entry in traversed if isinstance(entry, FileAccess)}
    directories = {Path(entry.path) for entry in traversed if isinstance(entry, Directory)}

    assert files == {first, second}
    assert directories == {nested}


@pytest.mark.asyncio
async def test_files_access_traverse_recursive(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    deep = nested / "deep"
    root.mkdir()
    nested.mkdir()
    deep.mkdir()
    top_file = root / "a.txt"
    nested_file = nested / "b.txt"
    deep_file = deep / "c.txt"
    top_file.write_text("a")
    nested_file.write_text("b")
    deep_file.write_text("c")

    files_access = Files()
    async with ctx.scope("files-recursive", files_access):
        traversed = await Files.traverse(root, recursive=True)

    files = {entry.path for entry in traversed if isinstance(entry, FileAccess)}
    directories = {Path(entry.path) for entry in traversed if isinstance(entry, Directory)}

    assert files == {top_file, nested_file, deep_file}
    assert directories == {nested, deep}


@pytest.mark.asyncio
async def test_files_access_traverse_requires_existing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    files_access = Files()

    async with ctx.scope("files-missing", files_access):
        with pytest.raises(FileException, match="Directory does not exist"):
            await Files.traverse(missing)


@pytest.mark.asyncio
async def test_file_access_creates_owner_only_file(
    tmp_path: Path,
    pinned_umask: None,
) -> None:
    file_path = tmp_path / "nested" / "secret.bin"
    files_access = Files()

    async with ctx.scope("file-permissions", files_access):
        async with Files.access(file_path, create=True) as file:
            await file.write(b"api-key")

    assert_permissions(file_path, 0o600)
    assert_permissions(file_path.parent, 0o700)


@pytest.mark.asyncio
async def test_file_access_applies_requested_mode(
    tmp_path: Path,
    pinned_umask: None,
) -> None:
    file_path = tmp_path / "shared" / "group.bin"
    files_access = Files()

    async with ctx.scope("file-mode", files_access):
        async with Files.access(file_path, create=True, mode=0o640) as file:
            await file.write(b"shared")

    assert_permissions(file_path, 0o640)
    # read access for the group requires search access on the directory
    assert_permissions(file_path.parent, 0o750)


@pytest.mark.asyncio
async def test_file_access_applies_mode_to_every_created_directory(
    tmp_path: Path,
    pinned_umask: None,
) -> None:
    file_path = tmp_path / "nested" / "deeper" / "deepest" / "secret.bin"
    files_access = Files()

    async with ctx.scope("file-nested-permissions", files_access):
        async with Files.access(file_path, create=True) as file:
            await file.write(b"api-key")

    assert_permissions(file_path, 0o600)
    # every directory created along the way is restricted alike - `Path.mkdir`
    # would leave the ancestors it creates at the umask default instead
    assert_permissions(file_path.parent, 0o700)
    assert_permissions(file_path.parent.parent, 0o700)
    assert_permissions(file_path.parent.parent.parent, 0o700)


@pytest.mark.asyncio
async def test_file_access_keeps_permissions_of_existing_file(tmp_path: Path) -> None:
    file_path = tmp_path / "existing.bin"
    file_path.write_bytes(b"content")
    file_path.chmod(0o644)
    files_access = Files()

    async with ctx.scope("file-existing", files_access):
        async with Files.access(file_path) as file:
            assert await file.read() == b"content"

    assert_permissions(file_path, 0o644)


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX symlinks")
@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX file locking")
@pytest.mark.asyncio
async def test_file_access_exclusive_lock_waits_for_release(tmp_path: Path) -> None:
    file_path = tmp_path / "contended.bin"
    files_access = Files()
    acquired: list[str] = []

    async def second() -> None:
        async with files_access.access(file_path, create=True, exclusive=True):
            acquired.append("second")

    async with ctx.scope("file-contended", files_access):
        async with files_access.access(file_path, create=True, exclusive=True):
            waiting = asyncio.create_task(second())
            # the lock is held here, so the waiter cannot get in
            await asyncio.sleep(0.05)
            assert acquired == []
            acquired.append("first")

        await asyncio.wait_for(waiting, 1)

    assert acquired == ["first", "second"]


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX file locking")
@pytest.mark.asyncio
async def test_file_access_exclusive_lock_wait_is_cancellable(tmp_path: Path) -> None:
    # a blocked acquisition used to hold an executor thread which cancellation
    # could not reach, leaving behind a descriptor which held the lock forever
    file_path = tmp_path / "cancelled.bin"
    files_access = Files()

    async def second() -> None:
        async with files_access.access(file_path, create=True, exclusive=True):
            pass

    async with ctx.scope("file-cancelled", files_access):
        async with files_access.access(file_path, create=True, exclusive=True):
            waiting = asyncio.create_task(second())
            await asyncio.sleep(0.05)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting

        # the abandoned attempt released everything it had opened
        async with asyncio.timeout(1):
            async with files_access.access(file_path, exclusive=True):
                pass


@pytest.mark.asyncio
async def test_file_access_abandoned_open_releases_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the descriptor is opened in an executor thread which cancellation cannot
    # reach, so an open nobody waits for any more still has to be released
    file_path = tmp_path / "abandoned.bin"
    opening = threading.Event()
    opened: list[int] = []
    closed: list[int] = []
    open_file = files_module._open_file

    def slow_open_file(path: Path, /, **arguments: Any) -> int:
        file_handle: int = open_file(path, **arguments)
        opened.append(file_handle)
        opening.set()
        time.sleep(0.2)  # hold the thread past the cancellation
        return file_handle

    def recording_close(file_handle: int, /) -> None:
        closed.append(file_handle)
        os.close(file_handle)

    monkeypatch.setattr(files_module, "_open_file", slow_open_file)
    monkeypatch.setattr(files_module, "os", OSModuleStub(close=recording_close))
    files_access = Files()

    async def abandoned() -> None:
        async with files_access.access(file_path, create=True):
            pass

    async with ctx.scope("file-abandoned", files_access):
        waiting = asyncio.create_task(abandoned())
        while not opening.is_set():
            await asyncio.sleep(0.01)

        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

        # the walk closes its own directory handles before the file is opened and
        # the numbers get reused, so only a close from here on proves anything
        settled: int = len(closed)
        # the shielded open finishes in its thread, then releases what it made
        async with asyncio.timeout(2):
            while opened[0] not in closed[settled:]:
                await asyncio.sleep(0.01)

    assert len(opened) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX fifo")
@pytest.mark.asyncio
async def test_file_access_refuses_irregular_file(tmp_path: Path) -> None:
    fifo_path = tmp_path / "planted.fifo"
    os.mkfifo(fifo_path)
    files_access = Files()

    async with ctx.scope("file-irregular", files_access):
        with pytest.raises(FileException, match="Not a regular file"):
            async with files_access.access(fifo_path):
                pass


@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX fifo")
@pytest.mark.asyncio
async def test_files_access_traverse_skips_irregular_entries(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    regular = root / "a.txt"
    regular.write_text("a")
    os.mkfifo(root / "b.fifo")

    files_access = Files()
    async with ctx.scope("files-irregular", files_access):
        direct = await Files.traverse(root)
        recursive = await Files.traverse(root, recursive=True)

    for traversed in (direct, recursive):
        assert {entry.path for entry in traversed if isinstance(entry, FileAccess)} == {regular}


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="requires POSIX permissions denying the current user",
)
@pytest.mark.asyncio
async def test_files_access_traverse_reports_unreadable_subtree(tmp_path: Path) -> None:
    # os.walk swallows errors by default, which would report a partial result as
    # if the traversal had succeeded
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "a.txt").write_text("a")
    nested.chmod(0o000)
    files_access = Files()

    try:
        async with ctx.scope("files-unreadable", files_access):
            with pytest.raises(FileException, match="Failed to traverse directory"):
                await Files.traverse(root, recursive=True)

    finally:
        nested.chmod(0o700)


@pytest.mark.asyncio
async def test_file_access_refuses_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"original")
    link = tmp_path / "link.bin"
    link.symlink_to(target)
    files_access = Files()

    async with ctx.scope("file-symlink", files_access):
        with pytest.raises(FileException, match="File is a symbolic link"):
            async with Files.access(link, create=True) as file:
                await file.write(b"redirected")

    assert target.read_bytes() == b"original"


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX symlinks")
@pytest.mark.asyncio
async def test_file_access_refuses_dangling_symbolic_link(tmp_path: Path) -> None:
    link = tmp_path / "dangling.bin"
    link.symlink_to(tmp_path / "missing.bin")
    files_access = Files()

    async with ctx.scope("file-dangling-symlink", files_access):
        with pytest.raises(FileException, match="File is a symbolic link"):
            async with Files.access(link, create=True):
                pass

    assert not (tmp_path / "missing.bin").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX symlinks")
@pytest.mark.asyncio
async def test_file_access_refuses_symbolic_link_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    files_access = Files()

    async with ctx.scope("file-symlink-parent", files_access):
        with pytest.raises(FileException, match="Directory is a symbolic link"):
            async with Files.access(link / "payload.bin", create=True) as file:
                await file.write(b"redirected")

    assert list(target.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX symlinks")
@pytest.mark.asyncio
async def test_file_access_refuses_dangling_symbolic_link_parent_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    link = tmp_path / "link"
    link.symlink_to(missing, target_is_directory=True)
    files_access = Files()

    async with ctx.scope("file-dangling-symlink-parent", files_access):
        with pytest.raises(FileException, match="Directory is a symbolic link"):
            async with Files.access(link / "payload.bin", create=True) as file:
                await file.write(b"redirected")

    assert not missing.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX symlinks")
@pytest.mark.asyncio
async def test_file_access_refuses_symbolic_link_ancestor_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    files_access = Files()

    async with ctx.scope("file-symlink-ancestor", files_access):
        with pytest.raises(FileException, match="Directory is a symbolic link"):
            async with Files.access(link / "nested" / "payload.bin", create=True) as file:
                await file.write(b"redirected")

    assert list(target.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX symlinks")
@pytest.mark.asyncio
async def test_file_access_refuses_symbolic_link_parent_of_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "payload.bin").write_bytes(b"original")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    files_access = Files()

    async with ctx.scope("file-symlink-parent-existing", files_access):
        with pytest.raises(FileException, match="Directory is a symbolic link"):
            async with Files.access(link / "payload.bin"):
                pass


@pytest.mark.asyncio
async def test_file_access_context_can_be_reused(tmp_path: Path) -> None:
    access = files_module.FileAccessContext(tmp_path / "reused.bin", create=True, exclusive=False)
    async with access as file:
        await file.write(b"first")

    async with asyncio.timeout(1):
        async with access as file:
            assert await file.read() == b"first"


@pytest.mark.asyncio
async def test_file_is_unusable_after_its_context_exits(tmp_path: Path) -> None:
    access = files_module.FileAccessContext(tmp_path / "closed.bin", create=True, exclusive=False)
    async with access as file:
        pass

    with pytest.raises(FileException, match="File is closed"):
        await asyncio.wait_for(file.read(), 1)


@pytest.mark.asyncio
async def test_file_access_is_bound_to_its_event_loop(tmp_path: Path) -> None:
    access = files_module.FileAccessContext(tmp_path / "bound.bin", create=True, exclusive=False)
    async with access as file:
        failure: list[BaseException] = []

        def other_loop() -> None:
            try:
                asyncio.run(file.read())

            except BaseException as exc:
                failure.append(exc)

        await asyncio.to_thread(other_loop)

    assert len(failure) == 1
    assert isinstance(failure[0], FileException)
    assert "different event loop" in str(failure[0])


@pytest.mark.asyncio
async def test_file_access_exit_waits_for_abandoned_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    closed: list[float] = []
    finished: list[float] = []
    write = files_module._write_file_contents.__wrapped__  # pyright: ignore[reportFunctionMemberAccess]

    def slow_write(file_handle: int, *, content: bytes) -> None:
        started.set()
        time.sleep(0.2)
        write(file_handle, content=content)
        finished.append(time.monotonic())

    def recording_close(file_handle: int, /) -> None:
        closed.append(time.monotonic())
        os.close(file_handle)

    monkeypatch.setattr(files_module, "_write_file_contents", files_module.asynchronous(slow_write))
    monkeypatch.setattr(files_module, "os", OSModuleStub(close=recording_close))
    access = files_module.FileAccessContext(
        tmp_path / "abandoned.bin", create=True, exclusive=False
    )
    async with access as file:
        writing = asyncio.create_task(file.write(b"data"))
        await asyncio.to_thread(started.wait, 1)
        writing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await writing

    # the descriptor outlived the caller until the operation was done with it
    assert finished and closed and closed[-1] >= finished[0]
    assert (tmp_path / "abandoned.bin").read_bytes() == b"data"


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX directory handles")
@pytest.mark.asyncio
async def test_file_access_syncs_directory_only_for_created_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synced: list[int] = []

    def recording_fsync(fd: int, /) -> None:
        synced.append(fd)
        os.fsync(fd)

    monkeypatch.setattr(files_module, "os", OSModuleStub(fsync=recording_fsync))
    path = tmp_path / "durable.bin"
    async with files_module.FileAccessContext(path, create=True, exclusive=False):
        pass

    assert len(synced) == 1
    async with files_module.FileAccessContext(path, create=True, exclusive=False):
        pass

    assert len(synced) == 1  # opening an existing file syncs nothing


@pytest.mark.asyncio
async def test_file_access_roundtrips_large_and_shrinking_content(tmp_path: Path) -> None:
    large: bytes = os.urandom(300_000)
    async with files_module.FileAccessContext(
        tmp_path / "large.bin", create=True, exclusive=False
    ) as file:
        await file.write(large)
        assert await file.read() == large
        await file.write(b"short")
        assert await file.read() == b"short"


def _descriptor_is_open(file_handle: int) -> bool:
    try:
        os.fstat(file_handle)

    except OSError:
        return False

    return True


@pytest.mark.asyncio
async def test_file_access_concurrent_reads_return_complete_content(tmp_path: Path) -> None:
    content: bytes = os.urandom(200_000)
    async with files_module.FileAccessContext(
        tmp_path / "concurrent.bin", create=True, exclusive=False
    ) as file:
        await file.write(content)
        results = await asyncio.gather(*(file.read() for _ in range(16)))

    assert all(result == content for result in results)


@pytest.mark.asyncio
async def test_file_access_tracks_running_operations_until_done(tmp_path: Path) -> None:
    access = files_module.FileAccessContext(tmp_path / "tracked.bin", create=True, exclusive=False)
    async with access as file:
        await file.write(b"data")
        await file.read()
        # nothing stays referenced once the operations are done
        assert access._running == set()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_file_access_closes_descriptor_when_body_raises(tmp_path: Path) -> None:
    access = files_module.FileAccessContext(tmp_path / "raising.bin", create=True, exclusive=False)
    with pytest.raises(RuntimeError, match="body failed"):
        async with access:
            file_handle: int = access._file_handle  # pyright: ignore[reportPrivateUsage, reportAssignmentType]
            raise RuntimeError("body failed")

    assert not _descriptor_is_open(file_handle)


@pytest.mark.asyncio
async def test_file_access_rejects_second_enter_while_open(tmp_path: Path) -> None:
    access = files_module.FileAccessContext(tmp_path / "twice.bin", create=True, exclusive=False)
    async with access:
        with pytest.raises(FileException, match="already open"):
            await access.__aenter__()

    # the refused attempt left the open one intact and closable
    async with access as file:
        assert await file.read() == b""


@pytest.mark.asyncio
async def test_file_access_exit_without_enter_fails(tmp_path: Path) -> None:
    access = files_module.FileAccessContext(tmp_path / "never.bin", create=True, exclusive=False)
    with pytest.raises(FileException, match="File is closed"):
        await access.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_file_access_exit_from_different_loop_fails_and_keeps_descriptor(
    tmp_path: Path,
) -> None:
    access = files_module.FileAccessContext(tmp_path / "foreign.bin", create=True, exclusive=False)
    await access.__aenter__()
    file_handle: int = access._file_handle  # pyright: ignore[reportPrivateUsage, reportAssignmentType]
    failure: list[BaseException] = []

    def other_loop() -> None:
        try:
            asyncio.run(access.__aexit__(None, None, None))

        except BaseException as exc:
            failure.append(exc)

    await asyncio.to_thread(other_loop)
    assert len(failure) == 1
    assert isinstance(failure[0], FileException)
    # refused without touching anything - the owning loop still closes it
    assert _descriptor_is_open(file_handle)
    await access.__aexit__(None, None, None)
    assert not _descriptor_is_open(file_handle)


def test_file_access_can_be_entered_on_another_loop_after_exit(tmp_path: Path) -> None:
    access = files_module.FileAccessContext(tmp_path / "moving.bin", create=True, exclusive=False)

    async def use(content: bytes) -> bytes:
        async with access as file:
            await file.write(content)
            return await file.read()

    assert asyncio.run(use(b"first")) == b"first"
    assert asyncio.run(use(b"second")) == b"second"


@pytest.mark.asyncio
async def test_cancelled_exit_postpones_close_until_operation_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    write = files_module._write_file_contents.__wrapped__  # pyright: ignore[reportFunctionMemberAccess, reportPrivateUsage]

    def blocked_write(file_handle: int, *, content: bytes) -> None:
        started.set()
        release.wait(2)
        write(file_handle, content=content)

    monkeypatch.setattr(
        files_module,
        "_write_file_contents",
        files_module.asynchronous(blocked_write),
    )
    access = files_module.FileAccessContext(
        tmp_path / "postponed.bin", create=True, exclusive=False
    )
    file = await access.__aenter__()
    file_handle: int = access._file_handle  # pyright: ignore[reportPrivateUsage, reportAssignmentType]
    writing = asyncio.create_task(file.write(b"payload"))
    await asyncio.to_thread(started.wait, 1)
    writing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await writing

    exiting = asyncio.create_task(access.__aexit__(None, None, None))
    await asyncio.sleep(0.05)
    exiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await exiting

    # the exit gave up waiting, yet the operation still owns the descriptor
    assert _descriptor_is_open(file_handle)
    release.set()
    async with asyncio.timeout(2):
        while _descriptor_is_open(file_handle):
            await asyncio.sleep(0.01)

    assert (tmp_path / "postponed.bin").read_bytes() == b"payload"


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX file locking")
@pytest.mark.asyncio
async def test_postponed_close_releases_exclusive_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    write = files_module._write_file_contents.__wrapped__  # pyright: ignore[reportFunctionMemberAccess, reportPrivateUsage]

    def blocked_write(file_handle: int, *, content: bytes) -> None:
        started.set()
        release.wait(2)
        write(file_handle, content=content)

    monkeypatch.setattr(
        files_module,
        "_write_file_contents",
        files_module.asynchronous(blocked_write),
    )
    path = tmp_path / "locked.bin"
    access = files_module.FileAccessContext(path, create=True, exclusive=True)
    file = await access.__aenter__()
    writing = asyncio.create_task(file.write(b"x"))
    await asyncio.to_thread(started.wait, 1)
    exiting = asyncio.create_task(access.__aexit__(None, None, None))
    await asyncio.sleep(0.05)
    exiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await exiting

    writing.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await writing

    # whoever waits for the lock gets it once the postponed close has run
    async with asyncio.timeout(2):
        async with files_module.FileAccessContext(path, create=False, exclusive=True):
            pass


@pytest.mark.asyncio
async def test_abandoned_close_swallows_failures() -> None:
    read_end, write_end = os.pipe()
    os.close(read_end)
    os.close(write_end)
    # already closed - closing again fails, and nobody is left to be told
    files_module._close_abandoned_file_handle(read_end, exclusive=False)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_write_continues_after_partial_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content: bytes = bytes(range(256)) * 8
    real_pwrite = os.pwrite
    real_write = os.write

    def stingy_pwrite(fd: int, data: Any, offset: int, /) -> int:
        return real_pwrite(fd, bytes(data[:7]), offset)

    def stingy_write(fd: int, data: Any, /) -> int:
        return real_write(fd, bytes(data[:7]))

    monkeypatch.setattr(
        files_module,
        "os",
        OSModuleStub(pwrite=stingy_pwrite, write=stingy_write),
    )
    async with files_module.FileAccessContext(
        tmp_path / "partial.bin", create=True, exclusive=False
    ) as file:
        await file.write(content)
        assert await file.read() == content


@pytest.mark.asyncio
async def test_write_fails_when_nothing_gets_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        files_module,
        "os",
        OSModuleStub(
            pwrite=lambda fd, data, offset: 0,
            write=lambda fd, data: 0,
        ),
    )
    async with files_module.FileAccessContext(
        tmp_path / "stuck.bin", create=True, exclusive=False
    ) as file:
        with pytest.raises(FileException, match="Failed to write file content"):
            await file.write(b"abc")


@pytest.mark.asyncio
async def test_read_assembles_content_from_short_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content: bytes = os.urandom(5000)
    real_pread = os.pread
    real_read = os.read

    def short_pread(fd: int, count: int, offset: int, /) -> bytes:
        return real_pread(fd, min(count, 999), offset)

    def short_read(fd: int, count: int, /) -> bytes:
        return real_read(fd, min(count, 999))

    path = tmp_path / "short.bin"
    path.write_bytes(content)
    monkeypatch.setattr(files_module, "os", OSModuleStub(pread=short_pread, read=short_read))
    async with files_module.FileAccessContext(path, create=False, exclusive=False) as file:
        assert await file.read() == content


@pytest.mark.asyncio
async def test_read_returns_content_of_file_which_changed_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "growing.bin"
    path.write_bytes(b"a" * 10)
    real_fstat = os.fstat

    def stale_fstat(fd: int, /) -> os.stat_result:
        result = real_fstat(fd)
        with open(path, "ab") as appended:  # grows right after being measured
            appended.write(b"b" * 200_000)

        return result

    monkeypatch.setattr(files_module, "os", OSModuleStub(fstat=stale_fstat))
    handle: int = os.open(path, os.O_RDWR)
    try:
        content: bytes = await files_module._read_file_contents(handle)  # pyright: ignore[reportPrivateUsage]

    finally:
        os.close(handle)

    # the size it was measured at is only a first guess, reading goes on to EOF
    assert content == b"a" * 10 + b"b" * 200_000


@pytest.mark.asyncio
async def test_file_access_without_positional_io_roundtrips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(files_module, "_POSITIONAL_IO", False)
    content: bytes = os.urandom(150_000)
    async with files_module.FileAccessContext(
        tmp_path / "seeking.bin", create=True, exclusive=False
    ) as file:
        await file.write(content)
        assert await file.read() == content
        await file.write(b"tiny")
        assert await file.read() == b"tiny"
        # reading again starts from the beginning each time
        assert await file.read() == b"tiny"


@pytest.mark.asyncio
async def test_file_access_missing_file_without_create_fails(tmp_path: Path) -> None:
    with pytest.raises(FileException, match="File does not exist"):
        await files_module.FileAccessContext(
            tmp_path / "absent.bin", create=False, exclusive=False
        ).__aenter__()

    assert not (tmp_path / "absent.bin").exists()


@pytest.mark.asyncio
async def test_file_access_missing_directory_without_create_fails(tmp_path: Path) -> None:
    with pytest.raises(FileException, match="File does not exist"):
        await files_module.FileAccessContext(
            tmp_path / "missing" / "absent.bin", create=False, exclusive=False
        ).__aenter__()

    assert not (tmp_path / "missing").exists()


@pytest.mark.asyncio
async def test_file_access_create_keeps_existing_content(tmp_path: Path) -> None:
    path = tmp_path / "existing.bin"
    path.write_bytes(b"kept")
    async with files_module.FileAccessContext(path, create=True, exclusive=False) as file:
        assert await file.read() == b"kept"


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX directory handles")
@pytest.mark.asyncio
async def test_file_access_create_opens_file_created_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "raced.bin"
    real_open = os.open

    def racing_open(name: Any, flags: int, *arguments: int, **keywords: Any) -> int:
        if flags & os.O_EXCL:
            # somebody else wins between the existence check and the creation
            path.write_bytes(b"winner")

        return real_open(name, flags, *arguments, **keywords)

    monkeypatch.setattr(files_module, "os", OSModuleStub(open=racing_open))
    async with files_module.FileAccessContext(path, create=True, exclusive=False) as file:
        assert await file.read() == b"winner"


@pytest.mark.skipif(sys.platform == "win32", reason="requires symbolic links")
@pytest.mark.asyncio
@pytest.mark.parametrize("create", [False, True])
@pytest.mark.parametrize("dangling", [False, True])
async def test_file_access_refuses_symlink_target(
    tmp_path: Path,
    create: bool,
    dangling: bool,
) -> None:
    real = tmp_path / "real.bin"
    if not dangling:
        real.write_bytes(b"secret")

    link = tmp_path / "link.bin"
    link.symlink_to(real)
    with pytest.raises(FileException, match="symbolic link"):
        await files_module.FileAccessContext(link, create=create, exclusive=False).__aenter__()

    # nothing was created or written through the link
    assert real.read_bytes() == b"secret" if not dangling else not real.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="requires symbolic links")
@pytest.mark.asyncio
async def test_file_access_refuses_symlinked_parent(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "linked").symlink_to(target)
    with pytest.raises(FileException, match="symbolic link"):
        await files_module.FileAccessContext(
            tmp_path / "linked" / "file.bin", create=True, exclusive=False
        ).__aenter__()

    assert list(target.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX file locking")
@pytest.mark.asyncio
async def test_file_access_exclusive_lock_released_on_exit(tmp_path: Path) -> None:
    path = tmp_path / "released.bin"
    async with files_module.FileAccessContext(path, create=True, exclusive=True):
        pass

    async with asyncio.timeout(1):
        async with files_module.FileAccessContext(path, create=False, exclusive=True):
            pass


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX entries")
@pytest.mark.asyncio
async def test_traversal_leaves_out_links_and_special_entries(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_bytes(b"x")
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "nested.txt").write_bytes(b"y")
    (tmp_path / "file-link").symlink_to(tmp_path / "file.txt")
    (tmp_path / "dir-link").symlink_to(tmp_path / "dir")
    (tmp_path / "broken-link").symlink_to(tmp_path / "absent")
    os.mkfifo(tmp_path / "pipe")
    for recursive in (False, True):
        paths = await files_module._traverse_path_contents(tmp_path, recursive)  # pyright: ignore[reportPrivateUsage]
        assert {path.name for path in paths.files} == (
            {"file.txt", "nested.txt"} if recursive else {"file.txt"}
        )
        assert {path.name for path in paths.directories} == {"dir"}


@pytest.mark.asyncio
async def test_traversal_recursive_reaches_every_depth(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (tmp_path / "a" / "one.txt").write_bytes(b"1")
    (deep / "three.txt").write_bytes(b"3")
    paths = await files_module._traverse_path_contents(tmp_path, True)  # pyright: ignore[reportPrivateUsage]
    assert set(paths.directories) == {
        tmp_path / "a",
        tmp_path / "a" / "b",
        deep,
    }
    assert set(paths.files) == {tmp_path / "a" / "one.txt", deep / "three.txt"}
    assert len(paths.files) + len(paths.directories) == 5  # nothing listed twice


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="requires permissions which are enforced",
)
@pytest.mark.asyncio
async def test_traversal_reports_unreadable_subtree(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "hidden.txt").write_bytes(b"x")
    locked.chmod(0)
    try:
        with pytest.raises(FileException, match="Failed to traverse directory"):
            await files_module._traverse_path_contents(tmp_path, True)  # pyright: ignore[reportPrivateUsage]

    finally:
        locked.chmod(0o700)
