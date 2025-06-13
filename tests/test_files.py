import tempfile
from pathlib import Path

import pytest

from haiway import ctx
from haiway.files import (
    FileContent,
    FileSystem,
    FileSystemConfig,
    local_file_system,
    memory_file_system,
)


@pytest.mark.asyncio
async def test_memory_file_system_basic_operations():
    """Test basic file operations with memory file system."""
    config = FileSystemConfig()
    fs = memory_file_system()

    async with ctx.scope("test", config, fs):
        # Test file doesn't exist initially
        test_path = Path("/test/file.txt")
        assert not await FileSystem.exists(test_path)

        # Write content
        content = "Hello, World!"
        await FileSystem.write_file(test_path, content)

        # Check file exists
        assert await FileSystem.exists(test_path)

        # Read content back
        file_content = await FileSystem.read_file(test_path)
        assert isinstance(file_content, FileContent)
        assert file_content.data == content
        assert file_content.encoding == "utf-8"
        assert file_content.path == test_path


@pytest.mark.asyncio
async def test_memory_file_system_binary_operations():
    """Test binary file operations with memory file system."""
    config = FileSystemConfig()
    fs = memory_file_system()

    async with ctx.scope("test", config, fs):
        test_path = Path("/test/binary.bin")
        content = b"Binary content\x00\x01\x02"

        # Write binary content
        await FileSystem.write_file(test_path, content)

        # Read as binary
        file_content = await FileSystem.read_file(test_path, binary=True)
        assert isinstance(file_content.data, bytes)
        assert file_content.data == content
        assert file_content.encoding is None


@pytest.mark.asyncio
async def test_memory_file_system_listing():
    """Test directory listing with memory file system."""
    config = FileSystemConfig()
    fs = memory_file_system()

    async with ctx.scope("test", config, fs):
        # Create files in different directories
        await FileSystem.write_file(Path("/root/file1.txt"), "content1")
        await FileSystem.write_file(Path("/root/file2.txt"), "content2")
        await FileSystem.write_file(Path("/root/subdir/file3.txt"), "content3")

        # List root directory (non-recursive)
        files = await FileSystem.list_files(Path("/root"))
        file_names = {f.path.name for f in files}
        assert "file1.txt" in file_names
        assert "file2.txt" in file_names
        assert "subdir" in file_names
        assert "file3.txt" not in file_names  # Should not include subdirectory files

        # List root directory (recursive)
        files = await FileSystem.list_files(Path("/root"), recursive=True)
        file_names = {f.path.name for f in files}
        assert "file1.txt" in file_names
        assert "file2.txt" in file_names
        assert "subdir" in file_names
        assert "file3.txt" in file_names


@pytest.mark.asyncio
async def test_memory_file_system_pattern_matching():
    """Test pattern matching in directory listing."""
    config = FileSystemConfig()
    fs = memory_file_system()

    async with ctx.scope("test", config, fs):
        # Create files with different extensions
        await FileSystem.write_file(Path("/test/file1.txt"), "content1")
        await FileSystem.write_file(Path("/test/file2.py"), "content2")
        await FileSystem.write_file(Path("/test/file3.txt"), "content3")

        # List only .txt files
        files = await FileSystem.list_files(Path("/test"), pattern="*.txt")
        file_names = {f.path.name for f in files}
        assert file_names == {"file1.txt", "file3.txt"}


@pytest.mark.asyncio
async def test_memory_file_system_file_opening():
    """Test file opening with memory file system."""
    config = FileSystemConfig()
    fs = memory_file_system()

    async with ctx.scope("test", config, fs):
        test_path = Path("/test/file.txt")

        # Write using file handle
        async with FileSystem.open_file(test_path, "w") as f:
            f.write("Line 1\n")
            f.write("Line 2\n")

        # Read using file handle
        async with FileSystem.open_file(test_path, "r") as f:
            content = f.read()
            assert content == "Line 1\nLine 2\n"

        # Append to file
        async with FileSystem.open_file(test_path, "a") as f:
            f.write("Line 3\n")

        # Verify append worked
        file_content = await FileSystem.read_file(test_path)
        assert file_content.data == "Line 1\nLine 2\nLine 3\n"


@pytest.mark.asyncio
async def test_file_system_config_restrictions():
    """Test file system configuration restrictions."""
    config = FileSystemConfig(
        blocked_extensions={".exe", ".bat"},
        max_file_size=10,  # 10 bytes max
    )
    fs = memory_file_system()

    async with ctx.scope("test", config, fs):
        # Test blocked extension
        with pytest.raises(PermissionError, match="File type not allowed"):
            await FileSystem.write_file(Path("/test/malware.exe"), "content")

        # Test file size limit
        with pytest.raises(ValueError, match="File too large"):
            await FileSystem.write_file(Path("/test/large.txt"), "This content is too large")


@pytest.mark.asyncio
async def test_file_system_config_allowed_extensions():
    """Test allowed extensions configuration."""
    config = FileSystemConfig(
        allowed_extensions={".txt", ".py"},
    )
    fs = memory_file_system()

    async with ctx.scope("test", config, fs):
        # Test allowed extension
        await FileSystem.write_file(Path("/test/allowed.txt"), "content")

        # Test disallowed extension
        with pytest.raises(PermissionError, match="File type not allowed"):
            await FileSystem.write_file(Path("/test/disallowed.json"), "content")


@pytest.mark.asyncio
async def test_file_system_config_base_path():
    """Test base path resolution."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config = FileSystemConfig(base_path=Path(temp_dir))
        fs = local_file_system()

        async with ctx.scope("test", config, fs):
            # Write to relative path
            relative_path = Path("test.txt")
            content = "test content"
            await FileSystem.write_file(relative_path, content)

            # Verify file was created in base path
            actual_path = Path(temp_dir) / "test.txt"
            assert actual_path.exists()
            assert actual_path.read_text() == content


@pytest.mark.asyncio
async def test_local_file_system_operations():
    """Test local file system operations."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config = FileSystemConfig(base_path=Path(temp_dir))
        fs = local_file_system()

        async with ctx.scope("test", config, fs):
            test_path = Path("local_test.txt")
            content = "Local file content"

            # Write and read
            await FileSystem.write_file(test_path, content)
            file_content = await FileSystem.read_file(test_path)

            assert file_content.data == content
            assert await FileSystem.exists(test_path)

            # List directory
            files = await FileSystem.list_files(Path("."))
            file_names = {f.path.name for f in files}
            assert "local_test.txt" in file_names


@pytest.mark.asyncio
async def test_error_handling():
    """Test error handling for various scenarios."""
    config = FileSystemConfig()
    fs = memory_file_system()

    async with ctx.scope("test", config, fs):
        # Test reading non-existent file
        with pytest.raises(FileNotFoundError):
            await FileSystem.read_file(Path("/nonexistent.txt"))

        # Test listing non-existent directory
        with pytest.raises(FileNotFoundError):
            await FileSystem.list_files(Path("/nonexistent"))

        # Test overwrite protection
        test_path = Path("/test.txt")
        await FileSystem.write_file(test_path, "original")

        with pytest.raises(FileExistsError):
            await FileSystem.write_file(test_path, "new", overwrite=False)


@pytest.mark.asyncio
async def test_file_info_retrieval():
    """Test getting file information."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config = FileSystemConfig(base_path=Path(temp_dir))
        fs = local_file_system()

        async with ctx.scope("test", config, fs):
            test_path = Path("info_test.txt")
            content = "File info test content"

            await FileSystem.write_file(test_path, content)

            file_info = await FileSystem.get_file_info(test_path)

            assert file_info.path.name == "info_test.txt"
            assert file_info.size == len(content.encode())
            assert file_info.is_readable
            assert file_info.is_writable
            assert file_info.modified_time > 0
