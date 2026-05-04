from pathlib import Path

import pytest

from haiway import Directory, File, Files, ctx
from haiway.helpers import files as files_module
from haiway.helpers.files import FileException, Paths


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

    directory = Directory(
        path=str(root),
        traversing=lambda recursive: files_module._traverse_path_contents(root, recursive),
    )
    async with ctx.scope("directory-traverse-alias", directory):
        traversed = await Directory.traverse()

    assert isinstance(traversed, Paths)
    assert set(traversed.files) == {first, second}
    assert set(traversed.directories) == {nested}


@pytest.mark.asyncio
async def test_open_file_handle_rejects_exclusive_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_path = tmp_path / "locked.bin"
    monkeypatch.setattr(files_module.sys, "platform", "win32")

    with pytest.raises(FileException, match="exclusive file locking is not supported on Windows"):
        await files_module._open_file_handle(file_path, create=True, exclusive=True)


@pytest.mark.asyncio
async def test_read_file_contents_translates_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising_read(fd: int, count: int, /) -> bytes:
        raise OSError("read failed")

    monkeypatch.setattr(files_module.os, "read", raising_read)

    with pytest.raises(FileException, match="Failed to read file content") as exc_info:
        await files_module._read_file_contents(1)

    assert isinstance(exc_info.value.__cause__, OSError)


@pytest.mark.asyncio
async def test_write_file_contents_translates_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising_write(fd: int, data: bytes, /) -> int:
        raise OSError("write failed")

    monkeypatch.setattr(files_module.os, "write", raising_write)

    with pytest.raises(FileException, match="Failed to write file content") as exc_info:
        await files_module._write_file_contents(1, content=b"abc")

    assert isinstance(exc_info.value.__cause__, OSError)


@pytest.mark.asyncio
async def test_close_file_handle_translates_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising_close(fd: int, /) -> None:
        raise OSError("close failed")

    monkeypatch.setattr(files_module.os, "close", raising_close)

    with pytest.raises(FileException, match="Failed to close file handle") as exc_info:
        await files_module._close_file_handle(1, exclusive=False)

    assert isinstance(exc_info.value.__cause__, OSError)


@pytest.mark.asyncio
async def test_file_handle_is_cleared_even_if_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_close(file_handle: int, *, exclusive: bool) -> None:
        raise FileException("closing failed")

    monkeypatch.setattr(files_module, "_close_file_handle", failing_close)
    context = files_module._file_access_context(
        tmp_path / "resource.bin",
        create=True,
        exclusive=False,
    )

    await context.__aenter__()
    with pytest.raises(FileException, match="closing failed"):
        await context.__aexit__(None, None, None)

    assert context._file_handle is None  # pyright: ignore[reportAttributeAccessIssue]


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

    assert isinstance(traversed, Paths)
    assert isinstance(traversed.files, tuple)
    assert isinstance(traversed.directories, tuple)
    assert set(traversed.files) == {first, second}
    assert set(traversed.directories) == {nested}


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

    assert isinstance(traversed, Paths)
    assert set(traversed.files) == {top_file, nested_file, deep_file}
    assert set(traversed.directories) == {nested, deep}


@pytest.mark.asyncio
async def test_files_access_traverse_requires_existing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    files_access = Files()

    async with ctx.scope("files-missing", files_access):
        with pytest.raises(FileException, match="Directory does not exist"):
            await Files.traverse(missing)
