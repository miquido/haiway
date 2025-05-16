__all__ = ("frozenlist",)

type frozenlist[Value] = tuple[Value, ...]
"""
A type alias for an immutable sequence of values.

This type represents an immutable list-like structure (implemented as a tuple)
that can be used when an immutable sequence is required. It provides the same
indexing and iteration capabilities as a list, but cannot be modified after creation.

The generic parameter Value specifies the type of elements stored in the sequence.

Examples
--------
```python
items: frozenlist[int] = (1, 2, 3)  # Create a frozen list of integers
first_item = items[0]               # Access elements by index
for item in items:                  # Iterate over elements
    process(item)
```
"""
