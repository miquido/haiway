# Haiway

[![PyPI](https://img.shields.io/pypi/v/haiway)](https://pypi.org/project/haiway/)
![Python Version](https://img.shields.io/badge/Python-3.14+-blue)
[![License](https://img.shields.io/github/license/miquido/haiway)](https://github.com/miquido/haiway/blob/main/LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/miquido/haiway?style=social)](https://github.com/miquido/haiway)

## Modern Python framework for functional programming with structured concurrency

Haiway brings functional programming principles to Python's async ecosystem, providing a robust
foundation for building scalable, maintainable applications with immutable state management and
automatic resource cleanup.

## Why Haiway?

Building concurrent Python applications often involves complex state management, dependency
injection frameworks, and careful resource handling. Haiway simplifies these challenges through:

### 🔒 **Immutability First**

Type-safe data structures that prevent race conditions and ensure predictable behavior in concurrent
environments

### ⚡ **Zero-Config DI**

Context-based dependency injection without decorators, containers, or complex frameworks

### 🎯 **Functional Design**

Pure functions and explicit data flow make code easier to understand, test, and maintain

### 🔄 **Structured Concurrency**

Automatic task lifecycle management with guaranteed cleanup, even in error cases

## Core Principles

### 🎯 **Type Safety Throughout**

Full type checking with modern Python features - unions, generics, protocols. Runtime validation
ensures data integrity.

### 🧩 **Composable Building Blocks**

Small, focused components that combine into larger systems. No framework lock-in or magic.

### 🔄 **Explicit Over Implicit**

Dependencies are visible in type signatures. No hidden global state or surprising side effects.

### 🎭 **Async-Native**

Built for Python's async/await from the ground up. Includes utilities for retries, timeouts, and
concurrent operations.

## Getting Started

### 📥 [Installation](getting-started/installation.md)

```bash
pip install haiway
```

Set up your environment in minutes

### 🚀 [Quick Start](getting-started/quickstart.md)

Build your first Haiway application with our hands-on tutorial

### 📚 [First Steps](getting-started/first-steps.md)

Deep dive into core concepts with practical examples

## Learn Haiway

### Essential Concepts

- **[State Management](guides/state.md)** - Immutable data structures with validation and type
  safety
- **[Types](guides/types.md)** - `MISSING`, `Default`, `Immutable`, `Map`, `Meta`, and annotation
  metadata primitives
- **[Functionalities](guides/functionalities.md)** - Organizing business logic with protocols and
  implementations
- **[Utilities](guides/utilities.md)** - Environment helpers, async queues and streams, pagination,
  and shared runtime primitives
- **[Packages](guides/packages.md)** - Structuring larger applications with modular components

### Advanced Topics

For advanced usage patterns and implementation details, see the complete [Guides](guides/index.md)
section.

## When to Use Haiway

✅ **Great for:**

- Async web services and APIs
- Data processing pipelines
- Applications requiring strong typing and validation
- Systems with complex dependency graphs
- Projects emphasizing testability and maintainability

⚠️ **Consider alternatives for:**

- Simple scripts or one-off tools
- CPU-bound numerical computing
- Projects requiring mutable shared state

## Resources

### 💻 [GitHub](https://github.com/miquido/haiway)

Source code, issues, and contributions

### 💬 [Discussions](https://github.com/miquido/haiway/discussions)

Community support and feature requests

**Built by [Miquido](https://miquido.com)** Powering innovation in AI and software development
