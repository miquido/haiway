# Guides

This section provides in-depth guides for working with Haiway's core concepts and patterns.

## Core Concepts

### [State Management](state.md)

Learn how to work with Haiway's immutable state system, including:

- Defining state classes with type validation
- Working with immutability and updates
- Using generic state types
- Path-based updates for nested structures
- Performance considerations

### [Types](types.md)

Reference for Haiway's foundational type primitives:

- Immutable containers such as `Immutable`, `Map`, and `Meta`
- Sentinel handling with `MISSING` and `Missing`
- Field defaults with `Default(...)`
- `Annotated` helpers such as `Alias`, `Description`, and `Specification`

### [Functionalities](functionalities.md)

Understand Haiway's functional programming approach:

- Protocol-based function interfaces
- Dependency injection through context
- Factory patterns for implementations
- Organizing business logic functionally
- Complete example: Notes management system

### [Packages](packages.md)

Structure larger applications effectively:

- Module organization patterns
- Package boundaries and interfaces
- Composing functionalities
- Managing dependencies between packages

### [Configuration Management](configuration.md)

Simple type-safe configuration with automatic defaults:

- Define configuration classes with State
- Load configurations from various backends
- Automatic fallback to contextual state and class defaults

### [Utilities](utilities.md)

Common runtime primitives exposed by Haiway:

- Environment loading and typed env parsing
- Async queue and stream coordination
- Pagination containers for integrations
- Collection normalization helpers
- Logging bootstrap and diagnostic formatting

### [Concurrent Processing](concurrent.md)

Master concurrent and parallel processing patterns:

- Structured concurrency with task spawning
- Context preservation across tasks
- Concurrent processing helpers
- Streaming multiple sources
- Performance optimization strategies

## Learning Path

1. **Start with State** - Understanding immutable state is fundamental to using Haiway effectively
1. **Learn Functionalities** - See how to organize business logic using functional patterns
1. **Manage Configuration** - Handle application configuration with type safety and automatic
   defaults
1. **Review Utilities** - Learn the shared helpers used for environment bootstrap and async
   coordination
1. **Master Concurrency** - Build high-performance applications with concurrent processing
1. **Scale with Packages** - Apply these concepts to structure larger applications
