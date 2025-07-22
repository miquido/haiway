## Functionalities

Haiway is a framework designed to facilitate the development of applications using the functional programming paradigm combined with structured concurrency concepts. Unlike traditional object-oriented frameworks, Haiway emphasizes immutability, pure functions, and context-based state management, enabling developers to build scalable and maintainable applications. By leveraging context managers combined with context variables, Haiway ensures safe state propagation in concurrent environments and simplifies dependency injection through function implementation propagation.

### Functional Basics

Functional programming centers around creating pure functions - functions that have no side effects and rely solely on their inputs to produce outputs. This approach promotes predictability, easier testing, and better modularity. While Python is inherently multi-paradigm and not strictly functional, Haiway encourages adopting functional principles where feasible to enhance code clarity and reliability.

Key functional concepts:
- Immutability: Data structures are immutable, preventing unintended side effects.
- Pure Functions: Functions depend only on their inputs and produce outputs without altering external state.
- Higher-Order Functions: Functions that can take other functions as arguments or return them as results.

Haiway balances functional purity with Python's flexibility by allowing limited side effects when necessary, though minimizing them is recommended for maintainability.

Instead of preparing objects with internal state and methods, Haiway encourages creating structures containing sets of functions and providing state either through function arguments or contextually using execution scope state. Using explicit function arguments is the preferred method; however, some functionalities may benefit from contextual, broader accessible state.

### Preparing Functionalities

In Haiway, functionalities are modularized into two primary components: interfaces and implementations. This separation ensures clear contracts for functionalities, promoting modularity and ease of testing.

### Defining Types

Interfaces define the public API of a functionality, specifying the data types and functions it exposes without detailing the underlying implementation. Preparing functionality starts from defining associated types - data structures and function types.

```python
# types.py
from typing import Protocol, Any, runtime_checkable
from haiway import State

# State representing the argument passed to the function
class FunctionArgument(State):
    value: Any

# Protocol defining the expected function signature
@runtime_checkable
class FunctionSignature(Protocol):
    async def __call__(self, argument: FunctionArgument) -> None: ...
```

In the example above, typing.Protocol is used to fully define the function signature, along with a custom structure serving as its argument. Function type names should emphasize the nature of their operations by using continuous tense adjectives, such as 'ElementCreating' or 'ValueLoading.'

### Defining State

State represents the immutable data required by functionalities. It is propagated through contexts to maintain consistency and support dependency injection. Haiway comes with a helpful base class `State` which utilizes dataclass-like transform combined with runtime type checking and immutability.

```python
# state.py
from my_functionality.types import FunctionSignature
from haiway import State

# State representing the state parameters needed by the functionality
class FunctionalityState(State):
    parameter: Any

# State encapsulating the functionality with its interface
class Functionality(State):
    function: FunctionSignature
```

This example shows a state required by the functionality as well as a container for holding function implementations. Both are intended to be propagated contextually to be accessible throughout the application and possibly altered when needed.

### Defining Implementation

Implementations provide concrete behavior for the defined interfaces, ensuring that they conform to the specified contracts.

```python
# implementation.py
from my_functionality.types import FunctionArgument
from my_functionality.state import FunctionalityState, Functionality
from haiway import ctx

# Concrete implementation of the FunctionInterface
async def function_implementation(argument: FunctionArgument) -> None:
    # Retrieve 'parameter' from the current context's state
    parameter = ctx.state(FunctionalityState).parameter
    # Implement the desired functionality using 'parameter' and 'argument.value'
    print(f"Parameter: {parameter}, Argument: {argument.value}")
    # Additional logic here...

# Factory function to instantiate the Functionality with its implementation
def functionality_implementation() -> Functionality:
    return Functionality(function=function_implementation)
```

In the example above, function_implementation is a concrete implementation of the previously declared function, and functionality_implementation is a factory method suitable for creating a full implementation of the Functionality.

Alternatively, instead of providing a factory method, some implementations may allow defining default values within state. This approach is also valid to implement and allows skipping explicit definitions of state by leveraging automatically created defaults.

### Classmethod Calls

Calls act as intermediaries that invoke the function implementations within the appropriate context. This abstraction simplifies access to functionalities by hiding non-essential details and access to various required components. When possible, the preferred way of defining calls is to put them within the functionality state type as class methods. This approach allows easier access to desired functions and improves ergonomics over the free functions access.

```python
# state.py - revisited
...
class Functionality(State):
    # define function call as a class method
    @classmethod
    async def function_call(cls, argument: FunctionArgument) -> None:
        # Invoke the function implementation from the contextual state
        await ctx.state(cls).function(argument=argument)

    function: FunctionSignature
```

Keeping it within the functionality interface class allows streamlined access and better control over the calls.

### Using Implementation

To utilize the defined functionalities within an application, contexts must be established to provide the necessary state and implementations. Below is an example of how to integrate Haiway functionalities into an application.

```python
# application.py
from my_functionality import functionality_implementation, Functionality, FunctionalityState
from haiway import ctx

# Example application function utilizing the functionality
async def application_function(argument: FunctionArgument) -> None:
    # Enter a context with the required state and functionality implementation
    async with ctx.scope(
        "example_context",
        functionality_implementation(),
        FunctionalityState(parameter="ExampleParameter")
    ):
        # Execute the functionality using the predefined helper
        await Functionality.function_call(FunctionArgument(value="SampleValue"))
```

Going through all of these layers may seem unnecessary at first, but in the long term, it creates a robust, modular system that is easy to manage and work with.

## Example

To better understand the whole idea, we can take a look at a more concrete example of a notes management functionality:

First, we define some basic types required by our functionality - management functions signatures and the note itself.

```python
# notes/types.py
from typing import Any, Protocol, runtime_checkable
from datetime import datetime
from uuid import UUID
from haiway import State

# State representing the note
class Note(State):
    identifier: UUID
    last_update: datetime
    content: str

# Protocol defining the note creation function
@runtime_checkable
class NoteCreating(Protocol):
    async def __call__(self, content: str, **extra: Any) -> Note: ...

# Protocol defining the note update function
@runtime_checkable
class NoteUpdating(Protocol):
    async def __call__(self, note: Note, **extra: Any) -> None: ...
```

Then we can define the state holding our functions and defining some context.

```python
# notes/state.py
from notes.types import NoteCreating, NoteUpdating, Note
from haiway import State, ctx
from typing import Any

# State providing contextual state for the functionality
class NotesDirectory(State):
    path: str = "./"

# State encapsulating the functionality with its interface
class Notes(State):
    # Call of note creation function
    @classmethod
    async def create_note(cls, content: str, **extra: Any) -> Note:
        # Invoke the function implementation from the contextual state
        return await ctx.state(cls).creating(content=content, **extra)

    # Call of note update function
    @classmethod
    async def update_note(cls, note: Note, **extra: Any) -> None:
        # Invoke the function implementation from the contextual state
        await ctx.state(cls).updating(note=note, **extra)

    # instance variables holding function implementations
    creating: NoteCreating
    updating: NoteUpdating
```

That allows us to provide a concrete implementation. Note that `extra` arguments would allow us to alter the `NotesDirectory` path for a single function call only. This might be a very important feature in some cases, i.e., when using recursive function calls.

```python
# notes/files.py
from notes.types import Note
from notes.state import Notes, NotesDirectory
from haiway import ctx
from typing import Any
from uuid import uuid4
from datetime import datetime

# Implementation of note creation function
async def file_note_create(content: str, **extra: Any) -> Note:
    # Retrieve path from the current context's state, updated if needed
    path = ctx.state(NotesDirectory).updated(**extra).path
    # Store note in file within the path...
    note = Note(
        identifier=uuid4(),
        last_update=datetime.now(),
        content=content
    )
    # Implementation for storing note in file...
    return note

# Implementation of note update function
async def file_note_update(note: Note, **extra: Any) -> None:
    # Retrieve path from the current context's state, updated if needed
    path = ctx.state(NotesDirectory).updated(**extra).path
    # Update the note...
    # Implementation for updating note in file...

# Factory function to instantiate the Notes utilizing files implementation
def file_notes() -> Notes:
    return Notes(
        creating=file_note_create,
        updating=file_note_update,
    )
```

You can now use the whole functionality by defining implementation for execution context and calling functionality methods.

```python
# example.py
from notes import Notes, file_notes, NotesDirectory
from haiway import ctx

# prepare the context with desired implementation
async with ctx.scope("example", file_notes(), NotesDirectory(path="./examples/note.txt")):
    # and access its methods contextually
    await Notes.create_note("This was an example of Haiway")
```
