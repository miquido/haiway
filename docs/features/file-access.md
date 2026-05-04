# File Access

Haiway provides a functional, context-aware file access interface that integrates seamlessly with
the framework's state management and structured concurrency features. The file access system
supports asynchronous operations with proper resource management and optional exclusive locking.

## Overview

The file access system in Haiway follows the framework's core principles:

- **Functional Interface**: All operations are performed through class methods on the `File` state
- **Context Integration**: File access is scoped to the context, ensuring proper resource cleanup
- **Resource Safety**: Automatic file handle cleanup even when exceptions occur
- **Cross-Platform**: Works on Unix/Linux/macOS with exclusive locking, degrades gracefully on
  Windows

## Quick Start

### 1. Basic File Operations

Open and work with files using the context system:

```python
from haiway import ctx
from haiway.helpers import Files, File

async def read_config():
    # Open file for reading and writing
    async with ctx.scope(
        "read_config",
        disposables=(Files.access("config.json"),)
    ):
        # Read file contents
        content = await File.read()
        # Process the data
        print(f"Config data: {content.decode()}")

        # Write updated content
        new_config = b'{"setting": "updated"}'
        await File.write(new_config)
```

### 2. Creating New Files

Create files and their parent directories automatically:

```python
async def create_data_file():
    async with ctx.scope(
        "create_file",
        disposables=(Files.access("data/output.txt", create=True),)
    ):
        # Write initial content
        await File.write(b"Initial data\n")

        # Read it back
        content = await File.read()
        print(f"Written: {content.decode()}")
```

### 3. Exclusive File Access

Use exclusive locking for critical operations:

```python
async def update_shared_resource():
    # Exclusive lock prevents concurrent access
    async with ctx.scope(
        "exclusive_update",
        disposables=(Files.access("shared.json", exclusive=True),)
    ):
        # Read current state
        data = await File.read()
        parsed = json.loads(data)

        # Update state
        parsed["counter"] += 1
        parsed["last_update"] = datetime.now().isoformat()

        # Replace the file contents in-place
        await File.write(json.dumps(parsed).encode())
```

## File Access Options

### Files.access() Parameters

Configure file access behavior:

```python
Files.access(
    path="data/file.txt",      # Path as string or Path object
    create=False,              # Create file if it doesn't exist
    exclusive=False            # Use exclusive locking (Unix/Linux/macOS)
)
```

- **path**: File path to open (absolute or relative)
- **create**: If True, creates the file and parent directories if needed
- **exclusive**: If True, acquires an exclusive lock (prevents other processes from accessing)

## Error Handling

File and directory helpers raise `FileException` for operational failures (open, read, write,
traverse, close, and unlock). Original provider errors are preserved as `__cause__` for debugging.

```python
from haiway.helpers import FileException

async def safe_file_read():
    try:
        async with ctx.scope(
            "read_file",
            disposables=(Files.access("data.txt"),)
        ):
            content = await File.read()
            return content.decode()
    except FileException as e:
        print(f"File operation failed: {e}")
        return None
```

## Advanced Usage

### Working with Binary Data

```python
async def process_binary_file():
    async with ctx.scope(
        "binary_ops",
        disposables=(Files.access("image.png"),)
    ):
        # Read binary data
        image_data = await File.read()

        # Process the data
        processed = transform_image(image_data)

        # Write back as binary
        await File.write(processed)
```

### Text File Handling

```python
async def update_text_file():
    async with ctx.scope(
        "text_file",
        disposables=(Files.access("document.txt"),)
    ):
        # Read as text
        content = await File.read()
        text = content.decode("utf-8")

        # Modify text
        updated_text = text.replace("old", "new")

        # Write back as UTF-8
        await File.write(updated_text.encode("utf-8"))
```

### Configuration File Management

```python
import json
import yaml
from datetime import datetime

async def manage_config():
    # JSON configuration
    async with ctx.scope(
        "json_config",
        disposables=(Files.access("config.json", create=True),)
    ):
        try:
            data = json.loads(await File.read())
        except (json.JSONDecodeError, FileException):
            data = {"version": "1.0", "settings": {}}

        data["settings"]["updated"] = True
        await File.write(json.dumps(data, indent=2).encode())

    # YAML configuration
    async with ctx.scope(
        "yaml_config",
        disposables=(Files.access("config.yaml", create=True),)
    ):
        try:
            content = await File.read()
            data = yaml.safe_load(content) or {}
        except FileException:
            data = {}

        data["last_run"] = datetime.now().isoformat()
        await File.write(yaml.dump(data).encode())
```

### Durable In-Place Updates

Writes are in-place with `os.write` followed by `fsync`; they are **not** copy-on-write or rename
atomic. Use exclusive locks plus your own temp-file strategy if you need atomic replacement.

```python
async def atomic_update():
    async with ctx.scope(
        "atomic",
        disposables=(Files.access("critical.dat", exclusive=True),)
    ):
        # Read current state
        current = await File.read()

        # Process data (may take time)
        processed = expensive_computation(current)

        # Replace file contents and fsync the result
        await File.write(processed)
        # File is automatically fsync'd to ensure durability
```

## Testing

Mock file operations for testing:

```python
from haiway import State
from haiway.helpers import File, FileReading, FileWriting

# Create mock implementations
class MockFileStorage:
    def __init__(self):
        self.files = {}

    async def read_file(self, path: str) -> bytes:
        if path not in self.files:
            raise FileException(f"File not found: {path}")
        return self.files[path]

    async def write_file(self, path: str, content: bytes) -> None:
        self.files[path] = content

# Use in tests
async def test_file_processing():
    storage = MockFileStorage()
    storage.files["input.txt"] = b"test data"

    # Create mock file state
    file_state = File(
        reading=lambda: storage.read_file("input.txt"),
        writing=lambda content: storage.write_file("output.txt", content)
    )

    async with ctx.scope("test", file_state):
        # Test file operations
        data = await File.read()
        assert data == b"test data"

        await File.write(b"processed data")
        assert storage.files["output.txt"] == b"processed data"
```

## Best Practices

1. **Always use as disposable**: Ensures file handles are properly closed
1. **Handle exceptions**: Wrap file operations in try/except blocks
1. **Use exclusive locking**: For critical updates that must not overlap
1. **Create parent directories**: Use `create=True` when writing to new locations
1. **Avoid multiple concurrent opens**: You can open more than one file in a scope; the newest
   `File` state shadows earlier ones. Prefer one file per scope to avoid surprises.

## Platform Considerations

### Unix/Linux/macOS

- Full support for exclusive file locking via fcntl
- In-place writes with proper fsync
- File permissions respected

### Windows

- No exclusive locking (fcntl not available)
- Still provides in-place writes with fsync
- File handles properly managed

## Implementation Details

The file access system uses:

- **OS-level file operations**: Direct use of os.open, os.read, os.write for efficiency
- **Exclusive locking**: fcntl.flock on supported platforms
- **Durable writes**: In-place writes, truncate, and fsync ensure durability
- **Async wrappers**: File I/O operations run in thread pool to avoid blocking

## Custom Implementations

Create custom file access implementations by implementing the protocols:

```python
from haiway.helpers import FileAccess, FileAccessing, File

class RemoteFileAccess:
    def __init__(self, server_url: str):
        self.server_url = server_url

    def __call__(
        self,
        path: Path | str,
        create: bool,
        exclusive: bool,
    ) -> FileAccess:
        # Return a context manager that provides File operations
        return RemoteFileContext(self.server_url, path, create, exclusive)

# Use custom implementation
async with ctx.scope(
    "remote_files",
    Files(accessing=RemoteFileAccess("https://files.example.com"))
):
    async with ctx.scope(
        "read_remote",
        disposables=(Files.access("remote/data.txt"),)
    ):
        content = await File.read()
```

This allows integration with cloud storage, databases, or any custom file storage backend while
maintaining the same interface.
