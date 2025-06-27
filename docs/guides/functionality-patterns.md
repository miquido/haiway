# Functionality Implementation Patterns

Haiway promotes a functional programming approach combined with structured concurrency. Unlike traditional object-oriented frameworks, Haiway emphasizes immutability, pure functions, and context-based state management. This guide explains how to effectively implement functionalities using Haiway's patterns.

## Functional Programming Principles

### Core Concepts

Functional programming centers around creating pure functions - functions that have no side effects and rely solely on their inputs to produce outputs. This approach promotes predictability, easier testing, and better modularity.

**Key functional concepts:**
- **Immutability**: Data structures are immutable, preventing unintended side effects
- **Pure Functions**: Functions depend only on their inputs and produce outputs without altering external state
- **Higher-Order Functions**: Functions that can take other functions as arguments or return them as results

Haiway balances functional purity with Python's flexibility by allowing limited side effects when necessary, though minimizing them is recommended for maintainability.

### Function-Based Architecture

Instead of preparing objects with internal state and methods, Haiway encourages creating structures containing sets of functions and providing state either through function arguments or contextually using execution scope state.

**Preferred approach: Explicit function arguments**
```python
from haiway import State

class UserData(State):
    id: str
    name: str
    email: str

# Pure function with explicit parameters
async def validate_user_email(user: UserData, email_validator: Callable[[str], bool]) -> bool:
    return email_validator(user.email)

# Usage
def is_valid_email(email: str) -> bool:
    return "@" in email and "." in email.split("@")[1]

user = UserData(id="1", name="Alice", email="alice@example.com")
is_valid = await validate_user_email(user, is_valid_email)
```

**When contextual state is beneficial:**
```python
from haiway import ctx, State
from typing import Protocol, runtime_checkable

@runtime_checkable
class EmailValidating(Protocol):
    async def __call__(self, email: str) -> bool: ...

class ValidationConfig(State):
    strict_mode: bool = False
    allowed_domains: Sequence[str] = ()

class ValidationService(State):
    email_validating: EmailValidating
    
    @classmethod
    async def validate_email(cls, email: str) -> bool:
        service = ctx.state(cls)
        return await service.email_validating(email)

# Implementation can access broader context
async def context_aware_email_validation(email: str) -> bool:
    config = ctx.state(ValidationConfig)
    
    basic_valid = "@" in email and "." in email.split("@")[1]
    
    if config.strict_mode:
        domain = email.split("@")[1]
        return basic_valid and domain in config.allowed_domains
    
    return basic_valid
```

## Functionality Structure

Haiway functionalities are modularized into two primary components: **interfaces** and **implementations**. This separation ensures clear contracts for functionalities, promoting modularity and ease of testing.

### 1. Defining Types

Interfaces define the public API of a functionality, specifying data types and functions without detailing implementation. Start by defining associated types - data structures and function types.

```python
# types.py
from typing import Protocol, runtime_checkable, Any
from collections.abc import Sequence, Mapping
from haiway import State

# State representing data structures
class Note(State):
    id: str
    title: str
    content: str
    tags: Sequence[str] = ()
    metadata: Mapping[str, str] = {}

class NoteFilter(State):
    tags: Sequence[str] = ()
    search_term: str | None = None
    limit: int = 100

# Protocol defining function signatures
@runtime_checkable
class NoteCreating(Protocol):
    async def __call__(self, title: str, content: str, **kwargs: Any) -> Note: ...

@runtime_checkable
class NoteFetching(Protocol):
    async def __call__(self, note_id: str) -> Note | None: ...

@runtime_checkable
class NoteSearching(Protocol):
    async def __call__(self, filter_params: NoteFilter) -> Sequence[Note]: ...

@runtime_checkable
class NoteUpdating(Protocol):
    async def __call__(self, note: Note, **kwargs: Any) -> Note: ...

@runtime_checkable
class NoteDeleting(Protocol):
    async def __call__(self, note_id: str) -> bool: ...
```

**Important naming conventions:**
- Function type names should use continuous tense adjectives (e.g., 'NoteCreating', 'DataProcessing', 'UserFetching')
- Always use single `__call__` method for maximum flexibility
- Use `**kwargs` for optional parameters that might alter behavior

### 2. Defining State

State represents immutable data required by functionalities and is propagated through contexts to maintain consistency and support dependency injection.

```python
# state.py
from haiway import State, ctx
from .types import (
    Note, NoteFilter, NoteCreating, NoteFetching, 
    NoteSearching, NoteUpdating, NoteDeleting
)

# Configuration state for the functionality
class NotesConfig(State):
    storage_path: str = "./notes"
    max_note_size: int = 10000
    auto_backup: bool = True

# State encapsulating the functionality with its interface
class NotesService(State):
    # Function implementations
    creating: NoteCreating
    fetching: NoteFetching
    searching: NoteSearching
    updating: NoteUpdating
    deleting: NoteDeleting
    
    # Class method interfaces for cleaner access
    @classmethod
    async def create_note(cls, title: str, content: str, **kwargs: Any) -> Note:
        service = ctx.state(cls)
        return await service.creating(title, content, **kwargs)
    
    @classmethod
    async def get_note(cls, note_id: str) -> Note | None:
        service = ctx.state(cls)
        return await service.fetching(note_id)
    
    @classmethod
    async def search_notes(cls, filter_params: NoteFilter) -> Sequence[Note]:
        service = ctx.state(cls)
        return await service.searching(filter_params)
    
    @classmethod
    async def update_note(cls, note: Note, **kwargs: Any) -> Note:
        service = ctx.state(cls)
        return await service.updating(note, **kwargs)
    
    @classmethod
    async def delete_note(cls, note_id: str) -> bool:
        service = ctx.state(cls)
        return await service.deleting(note_id)
```

### 3. Defining Implementation

Implementations provide concrete behavior for the defined interfaces, ensuring they conform to the specified contracts.

```python
# implementation.py
from datetime import datetime
from uuid import uuid4
from pathlib import Path
import json
from haiway import ctx
from .types import Note, NoteFilter
from .state import NotesConfig, NotesService

# Concrete implementation functions
async def file_note_creating(title: str, content: str, **kwargs: Any) -> Note:
    """Create a note and store it in the file system"""
    config = ctx.state(NotesConfig)
    
    # Create note with generated ID
    note = Note(
        id=str(uuid4()),
        title=title,
        content=content,
        tags=kwargs.get('tags', ()),
        metadata={
            'created_at': datetime.now().isoformat(),
            'storage_path': config.storage_path,
            **kwargs.get('metadata', {})
        }
    )
    
    # Store in file system
    note_path = Path(config.storage_path) / f"{note.id}.json"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(note_path, 'w') as f:
        json.dump(note.to_mapping(recursive=True), f, indent=2)
    
    return note

async def file_note_fetching(note_id: str) -> Note | None:
    """Fetch a note from the file system"""
    config = ctx.state(NotesConfig)
    note_path = Path(config.storage_path) / f"{note_id}.json"
    
    if not note_path.exists():
        return None
    
    try:
        with open(note_path, 'r') as f:
            data = json.load(f)
        
        return Note(
            id=data['id'],
            title=data['title'],
            content=data['content'],
            tags=data.get('tags', ()),
            metadata=data.get('metadata', {})
        )
    except (json.JSONDecodeError, KeyError):
        return None

async def file_note_searching(filter_params: NoteFilter) -> Sequence[Note]:
    """Search notes in the file system"""
    config = ctx.state(NotesConfig)
    storage_path = Path(config.storage_path)
    
    if not storage_path.exists():
        return ()
    
    notes = []
    for note_file in storage_path.glob("*.json"):
        try:
            with open(note_file, 'r') as f:
                data = json.load(f)
            
            note = Note(
                id=data['id'],
                title=data['title'],
                content=data['content'],
                tags=data.get('tags', ()),
                metadata=data.get('metadata', {})
            )
            
            # Apply filters
            if filter_params.tags:
                if not any(tag in note.tags for tag in filter_params.tags):
                    continue
            
            if filter_params.search_term:
                search_term = filter_params.search_term.lower()
                if search_term not in note.title.lower() and search_term not in note.content.lower():
                    continue
            
            notes.append(note)
            
            if len(notes) >= filter_params.limit:
                break
                
        except (json.JSONDecodeError, KeyError):
            continue
    
    return tuple(notes)

async def file_note_updating(note: Note, **kwargs: Any) -> Note:
    """Update a note in the file system"""
    config = ctx.state(NotesConfig)
    
    # Update note with new data
    updated_note = note.updated(
        title=kwargs.get('title', note.title),
        content=kwargs.get('content', note.content),
        tags=kwargs.get('tags', note.tags),
        metadata={
            **note.metadata,
            'updated_at': datetime.now().isoformat(),
            **kwargs.get('metadata', {})
        }
    )
    
    # Store updated note
    note_path = Path(config.storage_path) / f"{note.id}.json"
    with open(note_path, 'w') as f:
        json.dump(updated_note.to_mapping(recursive=True), f, indent=2)
    
    return updated_note

async def file_note_deleting(note_id: str) -> bool:
    """Delete a note from the file system"""
    config = ctx.state(NotesConfig)
    note_path = Path(config.storage_path) / f"{note_id}.json"
    
    if note_path.exists():
        note_path.unlink()
        return True
    
    return False

# Factory function to instantiate the service with file-based implementation
def FileNotesService() -> NotesService:
    """Factory function that creates a NotesService with file-based implementations"""
    return NotesService(
        creating=file_note_creating,
        fetching=file_note_fetching,
        searching=file_note_searching,
        updating=file_note_updating,
        deleting=file_note_deleting
    )
```

### 4. Alternative Implementation

The beauty of this pattern is that you can easily swap implementations:

```python
# database_implementation.py
from haiway import ctx
from integrations.database import DatabaseConnection
from .types import Note, NoteFilter
from .state import NotesService

async def database_note_creating(title: str, content: str, **kwargs: Any) -> Note:
    """Create a note in the database"""
    note_id = str(uuid4())
    
    await DatabaseConnection.execute_query(
        """
        INSERT INTO notes (id, title, content, tags, metadata)
        VALUES (%(id)s, %(title)s, %(content)s, %(tags)s, %(metadata)s)
        """,
        {
            'id': note_id,
            'title': title,
            'content': content,
            'tags': json.dumps(kwargs.get('tags', [])),
            'metadata': json.dumps({
                'created_at': datetime.now().isoformat(),
                **kwargs.get('metadata', {})
            })
        }
    )
    
    return Note(
        id=note_id,
        title=title,
        content=content,
        tags=kwargs.get('tags', ()),
        metadata={
            'created_at': datetime.now().isoformat(),
            **kwargs.get('metadata', {})
        }
    )

async def database_note_fetching(note_id: str) -> Note | None:
    """Fetch a note from the database"""
    results = await DatabaseConnection.execute_query(
        "SELECT * FROM notes WHERE id = %(id)s",
        {'id': note_id}
    )
    
    if not results:
        return None
    
    row = results[0]
    return Note(
        id=row['id'],
        title=row['title'],
        content=row['content'],
        tags=json.loads(row['tags']),
        metadata=json.loads(row['metadata'])
    )

# ... other database implementations

def DatabaseNotesService() -> NotesService:
    """Factory function that creates a NotesService with database implementations"""
    return NotesService(
        creating=database_note_creating,
        fetching=database_note_fetching,
        searching=database_note_searching,
        updating=database_note_updating,
        deleting=database_note_deleting
    )
```

## Using Implementations

To utilize functionalities within an application, contexts must be established to provide the necessary state and implementations.

### Basic Usage

```python
# application.py
from haiway import ctx
from notes import FileNotesService, NotesConfig, NotesService, Note, NoteFilter

async def example_application():
    # Set up configuration and service
    config = NotesConfig(storage_path="./my_notes", auto_backup=True)
    service = FileNotesService()
    
    async with ctx.scope("notes-app", config, service):
        # Create a note
        note = await NotesService.create_note(
            title="My First Note",
            content="This is the content of my first note.",
            tags=("personal", "important"),
            metadata={"category": "journal"}
        )
        print(f"Created note: {note.id}")
        
        # Search for notes
        filter_params = NoteFilter(tags=("important",), limit=10)
        found_notes = await NotesService.search_notes(filter_params)
        print(f"Found {len(found_notes)} important notes")
        
        # Update the note
        updated_note = await NotesService.update_note(
            note,
            content="Updated content for my first note.",
            tags=note.tags + ("updated",)
        )
        print(f"Updated note: {updated_note.title}")
        
        # Fetch the note
        retrieved_note = await NotesService.get_note(note.id)
        if retrieved_note:
            print(f"Retrieved: {retrieved_note.title}")
        
        # Delete the note
        deleted = await NotesService.delete_note(note.id)
        print(f"Note deleted: {deleted}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(example_application())
```

### Switching Implementations

```python
# application_with_database.py
from notes import DatabaseNotesService, NotesConfig, NotesService
from integrations.database import postgresql_connection, DatabaseConfig

async def database_application():
    # Set up database and notes configuration
    db_config = DatabaseConfig(host="localhost", database="notes_db")
    notes_config = NotesConfig(auto_backup=False)  # No file backup needed
    service = DatabaseNotesService()
    
    async with ctx.scope(
        "notes-db-app", 
        db_config, 
        notes_config, 
        service,
        disposables=(postgresql_connection(),)
    ):
        # Same interface, different implementation
        note = await NotesService.create_note(
            title="Database Note",
            content="This note is stored in the database."
        )
        print(f"Created database note: {note.id}")
```

### Testing with Mocks

```python
# test_notes.py
import pytest
from unittest.mock import AsyncMock
from haiway import ctx
from notes import NotesService, NotesConfig, Note

class MockNotesService:
    def __init__(self):
        self.notes = {}
        self.creating = AsyncMock(side_effect=self._create_note)
        self.fetching = AsyncMock(side_effect=self._fetch_note)
        self.searching = AsyncMock(side_effect=self._search_notes)
        self.updating = AsyncMock(side_effect=self._update_note)
        self.deleting = AsyncMock(side_effect=self._delete_note)
    
    async def _create_note(self, title: str, content: str, **kwargs) -> Note:
        note = Note(
            id=f"mock-{len(self.notes)}",
            title=title,
            content=content,
            tags=kwargs.get('tags', ()),
            metadata=kwargs.get('metadata', {})
        )
        self.notes[note.id] = note
        return note
    
    async def _fetch_note(self, note_id: str) -> Note | None:
        return self.notes.get(note_id)
    
    async def _search_notes(self, filter_params) -> tuple[Note, ...]:
        return tuple(self.notes.values())
    
    async def _update_note(self, note: Note, **kwargs) -> Note:
        updated = note.updated(**kwargs)
        self.notes[note.id] = updated
        return updated
    
    async def _delete_note(self, note_id: str) -> bool:
        return self.notes.pop(note_id, None) is not None

@pytest.mark.asyncio
async def test_notes_service():
    config = NotesConfig()
    mock_service = MockNotesService()
    
    # Create service state with mock implementations
    service_state = NotesService(
        creating=mock_service.creating,
        fetching=mock_service.fetching,
        searching=mock_service.searching,
        updating=mock_service.updating,
        deleting=mock_service.deleting
    )
    
    async with ctx.scope("test", config, service_state):
        # Test creating a note
        note = await NotesService.create_note("Test Note", "Test content")
        assert note.title == "Test Note"
        assert note.content == "Test content"
        
        # Test fetching the note
        fetched = await NotesService.get_note(note.id)
        assert fetched is not None
        assert fetched.title == "Test Note"
        
        # Test updating the note
        updated = await NotesService.update_note(note, title="Updated Test Note")
        assert updated.title == "Updated Test Note"
        assert updated.content == "Test content"  # Unchanged
        
        # Test deleting the note
        deleted = await NotesService.delete_note(note.id)
        assert deleted is True
        
        # Verify note is gone
        not_found = await NotesService.get_note(note.id)
        assert not_found is None
```

## Advanced Patterns

### Extra Parameters Pattern

The `**kwargs` pattern allows for flexible function calls with additional parameters:

```python
# Usage with extra parameters
async def application_with_extras():
    async with ctx.scope("app", config, service):
        # Create note with extra storage path for this call only
        note = await NotesService.create_note(
            title="Special Note",
            content="Content",
            tags=("special",),
            storage_path="./special_notes"  # Override default path
        )
```

### Implementation with Extra Handling

```python
async def flexible_note_creating(title: str, content: str, **kwargs: Any) -> Note:
    """Create note with flexible parameter handling"""
    config = ctx.state(NotesConfig)
    
    # Use override path if provided, otherwise use config
    storage_path = kwargs.get('storage_path', config.storage_path)
    
    # Extract known parameters
    tags = kwargs.get('tags', ())
    metadata = kwargs.get('metadata', {})
    
    # Create note with dynamic configuration
    note = Note(
        id=str(uuid4()),
        title=title,
        content=content,
        tags=tags,
        metadata={
            'created_at': datetime.now().isoformat(),
            'storage_path': storage_path,
            **metadata
        }
    )
    
    # Store using dynamic path
    note_path = Path(storage_path) / f"{note.id}.json"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(note_path, 'w') as f:
        json.dump(note.to_mapping(recursive=True), f, indent=2)
    
    return note
```

### Composing Functionalities

```python
# Combining multiple functionalities
from user_management import UserService, DatabaseUserService
from authentication import AuthService, JWTAuthService
from notifications import NotificationService, EmailNotificationService

async def complete_application():
    # Set up all services
    user_service = DatabaseUserService()
    auth_service = JWTAuthService()
    notification_service = EmailNotificationService()
    notes_service = FileNotesService()
    
    async with ctx.scope(
        "complete-app",
        user_service,
        auth_service,
        notification_service,
        notes_service,
        disposables=(postgresql_connection(),)
    ):
        # Services can interact through the same context
        user = await UserService.create_user("alice", "alice@example.com")
        token = await AuthService.create_session(user.id)
        
        await NotificationService.send_welcome_email(user.email)
        
        note = await NotesService.create_note(
            title="Welcome Note",
            content="Welcome to the application!",
            metadata={"created_by": user.id}
        )
```

## Best Practices

### 1. Keep Protocols Simple
Use single `__call__` method protocols for maximum flexibility and simplicity.

### 2. Use Meaningful Names
Function types should use continuous tense adjectives that clearly describe the operation.

### 3. Leverage Context Appropriately
Use explicit parameters when possible, context for broader configuration and shared state.

### 4. Provide Multiple Implementations
Design interfaces to support multiple implementations (file, database, memory, mock).

### 5. Test with Mocks
Use dependency injection to easily test with mock implementations.

### 6. Handle Errors Gracefully
Implement proper error handling in your implementations and propagate meaningful errors.

### 7. Document Interfaces
Clearly document what each protocol expects and what it returns.

### 8. Keep Functions Pure When Possible
Minimize side effects and prefer pure functions that are predictable and testable.

This pattern creates a robust, modular system that is easy to manage, test, and extend over time. The clear separation between interfaces and implementations, combined with Haiway's context system, provides powerful dependency injection capabilities while maintaining type safety and functional programming principles.