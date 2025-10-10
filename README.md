# 🚗 haiway 🚕 🚚 🚙

[![PyPI](https://img.shields.io/pypi/v/haiway)](https://pypi.org/project/haiway/)
![Python Version](https://img.shields.io/badge/Python-3.13+-blue)
![License](https://img.shields.io/badge/License-MIT-green)
[![Docs](https://img.shields.io/badge/Documentation-yellow)](https://miquido.github.io/haiway/)
![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/miquido/haiway?utm_source=oss&utm_medium=github&utm_campaign=miquido%2Fhaiway&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)

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

## 🖥️ Install

With pip:

```bash
pip install haiway
```

## 👷 Contributing

As an open-source project in a rapidly evolving field, we welcome all contributions. Whether you can
add a new feature, enhance our infrastructure, or improve our documentation, your input is valuable
to us.

We welcome any feedback and suggestions! Feel free to open an issue or pull request.

## ⚖️ License

MIT License

Copyright (c) 2024-2025 Miquido

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
