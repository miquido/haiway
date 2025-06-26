# Package Organization

Haiway provides a structured approach to organizing Python projects that scales well with project complexity and promotes maintainability. While the framework doesn't strictly enforce this structure, following these guidelines significantly enhances code organization and team collaboration.

## Philosophy

The core philosophy behind Haiway's package organization is to create a clear separation of concerns, allowing developers to build modular and easily extensible applications. By following these principles, you'll create software that is not only easier to understand and maintain but also more resilient to changes and growth over time.

## Package Types

Haiway defines five distinct package types, each serving a specific purpose in the overall architecture. Package types are organized by their high-level role in building application layers from the most basic and common elements to the most specific and complex functionalities.

### Recommended Project Structure

```
src/
│
├── commons/                # Shared utilities, types, extensions
│   ├── __init__.py
│   ├── types.py
│   ├── config.py
│   └── ...
│
├── integrations/           # Third-party service connections
│   ├── __init__.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── state.py
│   │   └── config.py
│   └── redis/
│       ├── __init__.py
│       ├── types.py
│       ├── state.py
│       └── config.py
│
├── solutions/              # Low-level utilities and algorithms
│   ├── __init__.py
│   ├── user_management/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── state.py
│   │   └── config.py
│   └── encryption/
│       ├── __init__.py
│       ├── types.py
│       ├── state.py
│       └── config.py
│
├── features/               # High-level business functionalities
│   ├── __init__.py
│   ├── user_registration/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── state.py
│   │   └── config.py
│   └── chat_handling/
│       ├── __init__.py
│       ├── types.py
│       ├── state.py
│       └── config.py
│
└── entrypoints/            # Application entry points
    ├── web_api/
    │   ├── __init__.py
    │   ├── __main__.py
    │   └── config.py
    └── cli_tool/
        ├── __init__.py
        ├── __main__.py
        └── config.py
```

## Package Type Details

### 1. Commons Package

The commons package provides shared utilities, types, extensions, and helper functions used throughout your application. It serves as a foundation for all other packages.

**Key characteristics:**
- Cannot depend on any other package in your application
- Contains only truly common and widely used functionalities
- Should not be overloaded with too many responsibilities

**Example structure:**
```
commons/
├── __init__.py
├── types.py      # Common types and errors
├── config.py     # Global configuration settings
├── utils.py      # Utility functions
└── exceptions.py # Custom exception classes
```

**Example content:**
```python
# commons/types.py
from typing import Protocol, runtime_checkable
from haiway import State

@runtime_checkable
class Logging(Protocol):
    async def __call__(self, message: str, level: str = "INFO") -> None: ...

class ErrorInfo(State):
    code: str
    message: str
    details: dict[str, str] = {}

# commons/exceptions.py
class BusinessLogicError(Exception):
    """Base exception for business logic errors"""
    pass

class ValidationError(BusinessLogicError):
    """Raised when data validation fails"""
    pass
```

### 2. Integrations Package

Integration packages implement connections to third-party services, external APIs, or system resources. They serve as the bridge between your application and the outside world.

**Key characteristics:**
- Focus on a single integration or external service
- Should not depend on other packages except commons
- Located within a top-level "integrations" package

**Example structure:**
```
integrations/
├── __init__.py
├── database/
│   ├── __init__.py
│   ├── types.py
│   ├── state.py
│   ├── config.py
│   └── postgresql.py  # Implementation
└── email_service/
    ├── __init__.py
    ├── types.py
    ├── state.py
    ├── config.py
    └── smtp.py        # Implementation
```

**Example implementation:**
```python
# integrations/database/types.py
from typing import Protocol, runtime_checkable, Any
from collections.abc import Sequence, Mapping

@runtime_checkable
class QueryExecuting(Protocol):
    async def __call__(self, query: str, params: Mapping[str, Any] = {}) -> Sequence[Mapping[str, Any]]: ...

# integrations/database/state.py
from haiway import State, ctx
from .types import QueryExecuting

class DatabaseConfig(State):
    host: str = "localhost"
    port: int = 5432
    database: str = "myapp"
    username: str = "user"
    password: str = "password"

class DatabaseConnection(State):
    executing: QueryExecuting
    
    @classmethod
    async def execute_query(cls, query: str, params: Mapping[str, Any] = {}) -> Sequence[Mapping[str, Any]]:
        connection = ctx.state(cls)
        return await connection.executing(query, params)

# integrations/database/postgresql.py
import asyncpg
from contextlib import asynccontextmanager
from .state import DatabaseConnection, DatabaseConfig
from haiway import ctx

async def postgresql_query_executing(query: str, params: Mapping[str, Any] = {}) -> Sequence[Mapping[str, Any]]:
    config = ctx.state(DatabaseConfig)
    # Implementation would use actual connection pool
    # This is simplified for example
    return [{"id": 1, "name": "example"}]

@asynccontextmanager
async def postgresql_connection():
    config = ctx.state(DatabaseConfig)
    pool = await asyncpg.create_pool(
        host=config.host,
        port=config.port,
        database=config.database,
        user=config.username,
        password=config.password
    )
    try:
        yield DatabaseConnection(executing=postgresql_query_executing)
    finally:
        await pool.close()
```

### 3. Solutions Package

Solution packages provide smaller, focused utilities and partial functionalities. They serve as building blocks for features, offering reusable components that can be combined to create complex behaviors.

**Key characteristics:**
- Deliver low-level functionalities common across multiple features
- Cannot depend on feature or entrypoint packages
- Should be project-specific and abstract away direct integrations
- Implement algorithms and foundations for features

**Example structure:**
```
solutions/
├── __init__.py
├── user_management/
│   ├── __init__.py
│   ├── types.py
│   ├── state.py
│   └── config.py
└── authentication/
    ├── __init__.py
    ├── types.py
    ├── state.py
    └── config.py
```

**Example implementation:**
```python
# solutions/user_management/types.py
from typing import Protocol, runtime_checkable
from collections.abc import Sequence
from haiway import State

class User(State):
    id: str
    username: str
    email: str
    roles: Sequence[str] = ()

@runtime_checkable
class UserCreating(Protocol):
    async def __call__(self, username: str, email: str) -> User: ...

@runtime_checkable
class UserFetching(Protocol):
    async def __call__(self, user_id: str) -> User | None: ...

# solutions/user_management/state.py
from haiway import State, ctx
from .types import User, UserCreating, UserFetching

class UserService(State):
    creating: UserCreating
    fetching: UserFetching
    
    @classmethod
    async def create_user(cls, username: str, email: str) -> User:
        service = ctx.state(cls)
        return await service.creating(username, email)
    
    @classmethod
    async def get_user(cls, user_id: str) -> User | None:
        service = ctx.state(cls)
        return await service.fetching(user_id)

# Implementation using database integration
async def database_user_creating(username: str, email: str) -> User:
    from integrations.database import DatabaseConnection
    from uuid import uuid4
    
    user_id = str(uuid4())
    await DatabaseConnection.execute_query(
        "INSERT INTO users (id, username, email) VALUES (%(id)s, %(username)s, %(email)s)",
        {"id": user_id, "username": username, "email": email}
    )
    return User(id=user_id, username=username, email=email)

async def database_user_fetching(user_id: str) -> User | None:
    from integrations.database import DatabaseConnection
    
    results = await DatabaseConnection.execute_query(
        "SELECT * FROM users WHERE id = %(id)s",
        {"id": user_id}
    )
    
    if not results:
        return None
    
    user_data = results[0]
    return User(
        id=user_data["id"],
        username=user_data["username"],
        email=user_data["email"]
    )

def DatabaseUserService() -> UserService:
    return UserService(
        creating=database_user_creating,
        fetching=database_user_fetching
    )
```

### 4. Features Package

Feature packages encapsulate the highest-level functions provided by your application. They represent the main capabilities or services that your application offers to its users.

**Key characteristics:**
- Implement complete and complex functionalities
- Can be consumed by multiple entrypoints
- Should focus on high-level business logic and orchestration
- Should not directly depend on integrations (use solutions instead)

**Example structure:**
```
features/
├── __init__.py
├── user_registration/
│   ├── __init__.py
│   ├── types.py
│   ├── state.py
│   └── config.py
└── user_authentication/
    ├── __init__.py
    ├── types.py
    ├── state.py
    └── config.py
```

**Example implementation:**
```python
# features/user_registration/types.py
from typing import Protocol, runtime_checkable
from solutions.user_management.types import User

class RegistrationData(State):
    username: str
    email: str
    password: str

class RegistrationResult(State):
    success: bool
    user: User | None = None
    error_message: str | None = None

@runtime_checkable
class UserRegistering(Protocol):
    async def __call__(self, data: RegistrationData) -> RegistrationResult: ...

# features/user_registration/state.py
from haiway import State, ctx
from .types import RegistrationData, RegistrationResult, UserRegistering

class UserRegistrationService(State):
    registering: UserRegistering
    
    @classmethod
    async def register_user(cls, data: RegistrationData) -> RegistrationResult:
        service = ctx.state(cls)
        return await service.registering(data)

# Implementation using solutions
async def complete_user_registration(data: RegistrationData) -> RegistrationResult:
    from solutions.user_management import UserService
    from solutions.authentication import AuthenticationService
    
    try:
        # Create user
        user = await UserService.create_user(data.username, data.email)
        
        # Set up authentication
        await AuthenticationService.set_password(user.id, data.password)
        
        return RegistrationResult(success=True, user=user)
    
    except Exception as e:
        return RegistrationResult(
            success=False,
            error_message=f"Registration failed: {str(e)}"
        )

def CompleteUserRegistrationService() -> UserRegistrationService:
    return UserRegistrationService(registering=complete_user_registration)
```

### 5. Entrypoints Package

Entrypoint packages serve as the starting points for your application. They define how your application is invoked and interacted with from the outside world.

**Key characteristics:**
- Top-level packages within your project's source directory
- No other packages should depend on entrypoints
- Each entrypoint should be isolated in its own package
- Can have multiple entrypoints for different interfaces

**Example structure:**
```
entrypoints/
├── web_api/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── users.py
│   │   └── auth.py
│   └── middleware.py
└── cli_tool/
    ├── __init__.py
    ├── __main__.py
    ├── config.py
    └── commands/
        ├── __init__.py
        ├── user.py
        └── admin.py
```

**Example implementation:**
```python
# entrypoints/web_api/__main__.py
import asyncio
from fastapi import FastAPI
from haiway import ctx
from features.user_registration import CompleteUserRegistrationService
from solutions.user_management import DatabaseUserService
from integrations.database import postgresql_connection, DatabaseConfig

app = FastAPI()

async def setup_context():
    """Set up application context with all dependencies"""
    db_config = DatabaseConfig(
        host="localhost",
        database="myapp_production"
    )
    
    user_service = DatabaseUserService()
    registration_service = CompleteUserRegistrationService()
    
    return ctx.scope(
        "web-api",
        db_config,
        user_service,
        registration_service,
        disposables=(postgresql_connection(),)
    )

@app.post("/register")
async def register_user(username: str, email: str, password: str):
    from features.user_registration import UserRegistrationService, RegistrationData
    
    data = RegistrationData(username=username, email=email, password=password)
    result = await UserRegistrationService.register_user(data)
    
    if result.success:
        return {"success": True, "user_id": result.user.id}
    else:
        return {"success": False, "error": result.error_message}

async def main():
    async with await setup_context():
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    asyncio.run(main())
```

## Internal Package Structure

### Functionality Package Structure

Features, solutions, and integrations should follow a consistent internal structure:

```
functionality_package/
├── __init__.py      # Exports public API only
├── types.py         # Type definitions and protocols
├── state.py         # State declarations with classmethods
├── config.py        # Configuration constants
└── implementation.py # Optional: internal implementation details
```

#### File Responsibilities

**`__init__.py`**
- Exports only the package's public symbols
- Should not import internal implementation details
- Typically exports contents from `types.py` and `state.py`

```python
# __init__.py example
from .types import User, UserCreating, UserFetching
from .state import UserService
from .implementation import DatabaseUserService

__all__ = [
    "User",
    "UserCreating", 
    "UserFetching",
    "UserService",
    "DatabaseUserService"
]
```

**`types.py`**
- Contains data types, interfaces, and errors
- Should not depend on other files within the package
- Only type declarations, no logic

**`config.py`**
- Configuration constants and settings
- Should only depend on `types.py`
- Environment variables and defaults

**`state.py`**
- State declarations for dependency and data injection
- Can use `types.py`, `config.py`, and optionally default implementations
- Should provide default values and/or factory methods

## Dependency Rules

### Allowed Dependencies

```
entrypoints → features → solutions → integrations → commons
     ↓           ↓          ↓            ↓
   (none)    (none)     (none)       (none)
```

- **Commons**: No dependencies on other packages
- **Integrations**: Can depend only on commons
- **Solutions**: Can depend on integrations and commons
- **Features**: Can depend on solutions, integrations, and commons (prefer solutions)
- **Entrypoints**: Can depend on all other package types

### Circular Dependencies

When circular dependencies occur within the same package type group, use these strategies:

#### 1. Contained Packages

Merge related packages that have circular dependencies:

```
solutions/
├── __init__.py
└── user_and_auth/          # Merged package
    ├── __init__.py
    ├── user_management/
    │   ├── __init__.py
    │   ├── types.py
    │   └── state.py
    └── authentication/
        ├── __init__.py
        ├── types.py
        └── state.py
```

#### 2. Shared Packages

Create a shared package for common interfaces:

```
solutions/
├── __init__.py
├── shared/                 # Shared interfaces
│   ├── __init__.py
│   └── types.py
├── user_management/
│   ├── __init__.py
│   ├── types.py
│   └── state.py
└── authentication/
    ├── __init__.py
    ├── types.py
    └── state.py
```

## Best Practices

### 1. Package Focus
Ensure each package contains only modules associated with its specific functionality. If a package grows too large, break it into smaller, focused packages.

### 2. Clear Public/Internal Separation
Maintain clear distinction between public and internal elements. Only export what's necessary for other parts of your application.

### 3. Avoid Circular Dependencies
Be vigilant about preventing circular dependencies. They often indicate that package boundaries need reconsideration.

### 4. Use Type Annotations
Leverage type annotations throughout your codebase for better readability and early error detection.

### 5. State Configuration
Provide default values and factory methods in `state.py` for easy configuration and usage.

### 6. Consistent Naming
Use consistent naming conventions across packages for better developer experience.

### 7. Project-Specific Rules
Some projects may benefit from additional rules. Keep them consistent across the project and ensure all team members understand them.

## Complete Example: E-commerce System

Here's how a complete e-commerce system might be organized:

```
src/
├── commons/
│   ├── __init__.py
│   ├── types.py        # Money, Address, etc.
│   └── exceptions.py   # BusinessError, ValidationError
│
├── integrations/
│   ├── payment_gateway/
│   │   ├── __init__.py
│   │   ├── types.py    # PaymentProcessing protocol
│   │   ├── state.py    # PaymentGateway state
│   │   └── stripe.py   # Stripe implementation
│   └── email_service/
│       ├── __init__.py
│       ├── types.py    # EmailSending protocol
│       ├── state.py    # EmailService state
│       └── sendgrid.py # SendGrid implementation
│
├── solutions/
│   ├── user_management/
│   │   ├── __init__.py
│   │   ├── types.py    # User, UserCreating, UserFetching
│   │   └── state.py    # UserService with classmethods
│   ├── inventory/
│   │   ├── __init__.py
│   │   ├── types.py    # Product, StockChecking, StockUpdating
│   │   └── state.py    # InventoryService
│   └── order_processing/
│       ├── __init__.py
│       ├── types.py    # Order, OrderCreating, OrderTracking
│       └── state.py    # OrderService
│
├── features/
│   ├── checkout/
│   │   ├── __init__.py
│   │   ├── types.py    # CheckoutData, CheckoutProcessing
│   │   └── state.py    # CheckoutService (orchestrates solutions)
│   └── user_registration/
│       ├── __init__.py
│       ├── types.py    # RegistrationData, UserRegistering
│       └── state.py    # RegistrationService
│
└── entrypoints/
    ├── web_store/
    │   ├── __init__.py
    │   ├── __main__.py  # FastAPI application
    │   └── routes/
    └── admin_cli/
        ├── __init__.py
        ├── __main__.py  # CLI application
        └── commands/
```

This organization provides clear separation of concerns, allows for easy testing through dependency injection, and scales well as the application grows in complexity.