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
    def raising_read(fd: int, count: int, /) -> bytes:
        raise OSError("read failed")

    monkeypatch.setattr(files_module, "os", OSModuleStub(read=raising_read))
    # a real descriptor - the implementation seeks before reading
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
    def raising_write(fd: int, data: bytes, /) -> int:
        raise OSError("write failed")

    monkeypatch.setattr(files_module, "os", OSModuleStub(write=raising_write))
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
            await files_module._close_file_handle(file_handle, exclusive=False)

    finally:
        os.close(file_handle)

    assert isinstance(exc_info.value.__cause__, OSError)
    assert str(exc_info.value.__cause__) == "close failed"


@pytest.mark.asyncio
async def test_file_handle_is_cleared_even_if_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_close(file_handle: int, *, exclusive: bool) -> None:
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
