## Organizing Packages

Haiway is a framework designed to help developers organize their code, manage state propagation, and handle dependencies effectively. While the framework does not strictly enforce its proposed package structure, adhering to these guidelines can significantly enhance the maintainability and scalability of your projects.

The core philosophy behind Haiway's package organization is to create a clear separation of concerns, allowing developers to build modular and easily extensible applications. By following these principles, you'll be able to create software that is not only easier to understand and maintain but also more resilient to changes and growth over time.

### Package Structure

In software development, especially in large-scale projects, proper organization is crucial. It helps developers navigate the codebase, understand the relationships between different components, and make changes with confidence. Haiway's package organization strategy is designed to address these needs by providing a clear structure that scales well with project complexity.

Haiway defines five distinct package types, each serving a specific purpose in the overall architecture of your application. Package types are organized by their high-level role in building application layers from the most basic and common elements to the most specific and complex functionalities to finally form an application entrypoint.

Here is a high-level overview of the project packages structure which will be explained in detail below.

```
src/
│
├── ...
│
├── entrypoint_a/  # application entrypoints
│   ├── __init__.py
│   ├── __main__.py
│   └── ...
├── entrypoint_b/
│   ├── __init__.py
│   ├── __main__.py
│   └── ...
│
├── features/ # high level functionalities
│   ├── __init__.py
│   ├── feature_a/
│   │   ├── __init__.py
│   │   └── ...
│   └── feature_b/
│       ├── __init__.py
│       └── ...
│
├── solutions/ # low level functionalities
│   ├── __init__.py
│   ├── solution_a/
│   │   ├── __init__.py
│   │   └── ...
│   └── solution_b/
│       ├── __init__.py
│       └── ...
│
├── integrations/ # third party services integrations
│   ├── __init__.py
│   ├── integration_a/
│   │   ├── __init__.py
│   │   └── ...
│   └── integration_b/
│       ├── __init__.py
│       └── ...
│
└── commons/ # common types, utilities and language extensions
    ├── __init__.py
    └── ...
```

#### Entrypoints

Entrypoint packages serve as the starting points for your application. They define how your application is invoked and interacted with from the outside world. Examples of entrypoints include command-line interfaces (CLIs), HTTP servers, or even graphical user interfaces (GUIs).

```
src/
│
├── entrypoint_a/
│   ├── __init__.py
│   ├── __main__.py
│   └── ...
├── entrypoint_b/
│   ├── __init__.py
│   ├── __main__.py
│   └── ...
└── ...
```

Entrypoints are top-level packages within your project's source directory. Your project can have multiple entrypoints, allowing for various ways to interact with your application or splitting its runtime into multiple pieces. No other packages should depend on entrypoint packages. They are the outermost layer of your application architecture. Each entrypoint should be isolated in its own package, promoting a clear separation between different parts of your application.

By keeping entrypoints separate, you maintain flexibility in how your application can be used while ensuring that the core functionality remains independent of any specific interface.

#### Features

Feature packages encapsulate the highest-level functions provided by your application. They represent the main capabilities or services that your application offers to its users. Examples of features could be user registration, chat handling, or data processing pipelines.

```
src/
│
├── ...
│
└── features/
    ├── __init__.py
    ├── feature_a/
    │   ├── __init__.py
    │   └── ...
    └── feature_b/
        ├── __init__.py
        └── ...
```

Feature packages are designed to be consumed by multiple entrypoints, allowing the same functionality to be accessed through different interfaces. All feature packages should be located within a top-level "features" package in your project's source directory. The top-level "features" package itself should not export any symbols. It serves purely as an organizational container. Feature packages should focus on high-level business logic and orchestration of lower-level components. However, they should not directly depend on any integrations, prioritizing solution packages usage instead.

By organizing your core application capabilities into feature packages, you create a clear delineation of what your application does, making it easier to understand, extend, and maintain the overall functionality.

#### Solutions

Solution packages provide smaller, more focused utilities and partial functionalities. They serve as the building blocks for your features, offering reusable components that can be combined to create more complex behaviors. While features implement complete and complex functionalities, solutions aim for simple, single-purpose helpers that allow building numerous features on top. Examples of solutions include storage mechanisms, user management, or encryption helpers.

```
src/
│
├── ...
│
└── solutions/
    ├── __init__.py
    ├── solution_a/
    │   ├── __init__.py
    │   └── ...
    └── solution_b/
        ├── __init__.py
        └── ...
```

Solution packages deliver low-level functionalities that are common across multiple features. Solution packages cannot depend on any feature and entrypoint packages, maintaining a clear hierarchical structure. All solution packages should be located within a top-level "solutions" package in your project's source directory. Like the features package, the top-level "solutions" package itself should not export any symbols. Solutions should be project-specific and abstract away direct integrations with third parties and implement algorithms laying foundations for features.

By breaking down common functionalities into solution packages, you promote code reuse and maintain a clear separation between high-level features and their underlying implementations.

#### Integrations

Integration packages are responsible for implementing connections to third-party services, external APIs, or system resources. They serve as the bridge between your application and the outside world. Examples of integrations may be API clients or database connectors.

```
src/
│
├── ...
│
└── integrations/
    ├── __init__.py
    ├── integration_a/
    │   ├── __init__.py
    │   └── ...
    └── integration_b/
        ├── __init__.py
        └── ...
```

Each integration package should focus on a single integration or external service. They should not depend on other packages except for the commons package. All integration packages should be located within a top-level "integrations" package in your project's source directory. The top-level "integrations" package, like features and solutions, should not export any symbols.

By isolating integrations in their own packages, you make it easier to manage external dependencies, update or replace integrations, and maintain a clear boundary between your application's core logic and its interactions with external systems.

#### Commons

The commons package is a special package that provides shared utilities, types, extensions, and helper functions used throughout your application. It serves as a foundation for all other packages and may be used to resolve circular dependencies caused by type imports in some cases.

```
src/
│
├── ...
│
└── commons/
    ├── __init__.py
    └── ...
```

The commons package cannot depend on any other package in your application. It should contain only truly common and widely used functionalities. Care should be taken not to overload the commons package with too many responsibilities.

The commons package helps reduce code duplication and provides a centralized location for shared utilities and types, promoting consistency across your application.

### Internal Package Structure

To maintain consistency and improve code organization, Haiway recommends a specific internal structure for packages. This structure varies slightly depending on the package type, but generally follows a similar pattern. Features, solutions, and integrations, referred to also as functionalities, all follow a similar pattern when it comes to the package contents and organization.

#### Structure of Functionality Packages

Functionality (feature, solution, integration) packages should adhere to the following internal structure:

```
solution_or_feature/
│
├── __init__.py
├── config.py
├── state.py
├── ...
└── types.py
```

`__init__.py`: This file is responsible for exporting the package's public symbols. It's crucial to only export what is intended to be used outside the package. Anything not exported is considered internal and should not be accessed from outside the package. The `__init__.py` file should not import any internal or private elements of the package, especially direct implementations of underlying components. Typically it would export the state, and types contents.

`types.py`: This file contains definitions for data types, interfaces, and errors used within the package. It should not depend on any other file within the package and should not contain any logic—only type declarations. Types defined here can be partially or fully exported to allow for type annotations and checks in other parts of your application.

`config.py`: This file holds configuration and constants used by the package, including any relevant environment variables. It should only depend on types.py and not on any other module within the package.

`state.py`: This file contains state declarations for the package, used for dependency and data injection. It can use types.py, config.py, and optionally default implementations from other modules. State types should be exported to allow defining and updating implementations and contextual data or configuration. The state should provide default values and/or factory methods for easy configuration.

Other: Any additional files needed for internal implementation details. These files should be treated as internal and not exported.

#### Structure of Commons Package

The commons package has a more flexible structure, as it contains various utility functions and shared components. However, it should still maintain a clear organization:

```
commons/
│
├── __init__.py
├── config.py
├── types.py
└── ...
```

`__init__.py`: Exports the public API of the commons package.

`config.py`: Contains global configuration settings and constants.

`types.py`: Defines common types used throughout the application including errors.

Other: There are possibly multiple additional files within this package according to your project needs. Additional, nested packages are highly recommended for splitting complex and long files.

#### Structure of Entrypoint Packages

Entrypoint packages have a structure that reflects their role as the application's entry point:

```
entrypoint/
│
├── __init__.py
├── __main__.py
├── config.py
└── ...
```

`__init__.py`: Typically empty as entrypoints are usually not imported by other packages.

`__main__.py`: The entry point for the application, containing the code that runs when the package is run.

`config.py`: Configuration specific to this entrypoint.

Other: Despite feature packages containing high-level functionalities, each entrypoint should also internally organize its code including usage of nested packages according to the application needs.

### Circular Dependencies

When splitting your code into multiple small packages, you may encounter circular dependencies. While the base package organization of Haiway prevents some of these issues, they can still occur within the same package type group. There are two recommended solutions to address this situation:

#### Contained Packages

This approach involves creating an additional common package that contains the conflicting packages. This strategy allows you to resolve conflicts while keeping linked functionalities together. It is helpful to merge a few (at most three) packages that are linked together and commonly providing functionalities within that link, i.e., database storage of specific data and some linked service relying on that data.

```
src/
│
├── package_group/
│   ├── __init__.py
│   └── package_merged/
│       ├── __init__.py
│       ├── package_a/
│       │   ├── __init__.py
│       │   └── ...
│       ├── package_b/
│       │   ├── __init__.py
│       │   └── ...
│       └── ...
└── ...
```

`package_group`: Represents the broader category (e.g., features, solutions) where the packages are located.

`package_merged`: Represents the package merging conflicting packages; it should represent the merged functionalities' designated functionality. It should export all public symbols from encapsulated packages.

`package_a`, `package_b`: Are the original packages that had circular dependencies between them.

By placing linked packages within the common package, you create a new scope that can resolve the circular dependency issues. The `__init__.py` file in package_merged can then expose a unified interface, managing the interactions between package_a and package_b internally and exposing all required symbols.

#### Shared Packages

When the contained packages strategy can't be applied due to multiple dependencies spread across multiple packages, you can create an additional, shared package within the same package group. This shared package declares all required interfaces.

```
src/
│
├── package_group/
│   ├── __init__.py
│   ├── package_a/
│   │   ├── __init__.py
│   │   └── ...
│   ├── package_b/
│   │   ├── __init__.py
│   │   └── ...
│   └── package_shared/
│       ├── __init__.py
│       └── ...
└── ...
```

`package_group`: Remains the broader category where the packages are located as previously.

`package_a`, `package_b`: Are kept separate within the group as normal.

`package_shared`: Is introduced as a new package that contains shared interfaces and types to resolve circular dependencies between conflicting packages.

The shared package acts as an intermediary, defining interfaces that both package_a and package_b can depend on. This breaks the direct circular dependency between them. The shared package should only contain interface definitions and types, not implementations.

### Best Practices

To make the most of Haiway's package organization strategy, consider the following best practices:

- Package Focus: Ensure that each package contains only modules (files) associated with its specific functionality. If you find a package growing too large or handling multiple concerns, consider breaking it down into smaller, more focused packages.

- Clear Public/Internal Separation: Maintain a clear distinction between public and internal elements of your packages. Only export what is necessary for other parts of your application to use. This helps prevent unintended dependencies and makes it easier to refactor internal implementations without affecting other parts of your codebase.

- Avoid Circular Dependencies: Be vigilant about preventing circular dependencies between packages. This can lead to complex and hard-to-maintain code. If you find yourself needing to create a circular dependency, it's often a sign that your package boundaries need to be reconsidered.

- Use Type Annotations: Leverage type annotations throughout your codebase. This not only improves readability but also helps catch potential errors early in the development process. Strict type checking is strongly recommended for each project using the Haiway framework.

- State Configuration: When defining states in state.py, provide default values and factory methods. This makes it easier to configure and use your packages in different contexts.

- Consistent Naming: Use consistent naming conventions across your packages. This helps developers quickly understand the purpose and content of different files and modules.

- Project-Specific Rules: Some projects may benefit from additional rules applied to their codebase. You may introduce additional requirements, but keep them consistent across the project and ensure that all project members know and apply them correctly.
