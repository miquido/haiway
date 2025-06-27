# FastAPI Integration

This example demonstrates how to integrate Haiway with FastAPI to build robust web APIs with structured concurrency and dependency injection.

## Overview

The integration showcases:

- **Context Middleware** - Automatic context management for each request
- **Resource Management** - Database connections and other resources
- **Error Handling** - Structured error handling with tracing
- **Dependency Injection** - Clean separation of concerns using Haiway's state system

## Project Structure

```
src/
├── server/
│   ├── application.py          # FastAPI app setup
│   ├── middlewares/
│   │   └── context.py         # Haiway context middleware
│   └── routes/
│       ├── technical.py       # Health checks and status
│       └── todos.py          # Business logic endpoints
├── features/
│   └── todos/                 # Business logic layer
├── integrations/
│   └── postgres/             # Database integration
└── solutions/
    └── user_tasks/           # Implementation layer
```

## Application Setup

### Main Application

```python
from contextlib import asynccontextmanager
from logging import Logger, getLogger
from fastapi import FastAPI
from haiway import Disposables, setup_logging

from integrations.postgres import PostgresClient
from server.middlewares import ContextMiddleware
from server.routes import technical_router, todos_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager with Haiway integration"""
    setup_logging("server")
    logger: Logger = getLogger("server")
    
    if __debug__:
        logger.warning("Starting DEBUG server...")
    else:
        logger.info("Starting server...")

    # Setup disposable resources
    disposables = Disposables(
        PostgresClient(),
    )
    
    async with disposables as state:
        # Make state available to middleware
        app.extra["state"] = (*state,)
        
        logger.info("...server started...")
        yield  # Server runs here
        
    logger.info("...server shutdown!")

# Create FastAPI app with lifespan
app: FastAPI = FastAPI(
    title="haiway-fastapi",
    description="Example project using haiway with fastapi",
    version="0.1.0",
    lifespan=lifespan,
    openapi_url="/openapi.json" if __debug__ else None,
    docs_url="/swagger" if __debug__ else None,
    redoc_url="/redoc" if __debug__ else None,
)

# Add Haiway context middleware
app.add_middleware(ContextMiddleware)

# Include routers
app.include_router(technical_router)
app.include_router(todos_router)
```

## Context Middleware

The context middleware creates a Haiway context for each request:

```python
from haiway import MetricsLogger, ctx
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.exceptions import HTTPException
from logging import getLogger

class ContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] == "http":
            await self._handle_http(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    async def _handle_http(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        # Create context for each request
        async with ctx.scope(
            f"{scope.get('method', '')} {scope['path']}",
            *scope["app"].extra.get("state", ()),  # Include app state
            logger=getLogger("server"),
            metrics=MetricsLogger.handler(),
        ) as trace_id:
            
            # Add trace ID to response headers
            async def traced_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    headers["trace_id"] = f"{trace_id}"
                await send(message)

            try:
                return await self.app(scope, receive, traced_send)
                
            except HTTPException as exc:
                # Add trace ID to HTTP exceptions
                if exc.headers is None:
                    exc.headers = {"trace_id": f"{trace_id}"}
                elif isinstance(exc.headers, dict):
                    exc.headers["trace_id"] = f"{trace_id}"
                raise exc
                
            except BaseException as exc:
                # Handle unexpected errors
                ctx.log_error(f"Unhandled error: {exc}")
                raise HTTPException(
                    status_code=500,
                    headers={"trace_id": f"{trace_id}"},
                    detail="Internal server error",
                ) from exc
```

## State Definitions

### Business Logic State

```python
from haiway import State
from typing import Protocol, runtime_checkable
from uuid import UUID

class Todo(State):
    id: UUID
    title: str
    description: str
    completed: bool = False

@runtime_checkable  
class TodoFetching(Protocol):
    async def __call__(self, todo_id: UUID) -> Todo | None: ...

@runtime_checkable
class TodoCompleting(Protocol):
    async def __call__(self, todo_id: UUID) -> None: ...

class TodoService(State):
    fetching: TodoFetching
    completing: TodoCompleting
    
    @classmethod
    async def get_todo(cls, todo_id: UUID) -> Todo | None:
        service = ctx.state(cls)
        return await service.fetching(todo_id)
    
    @classmethod
    async def complete_todo(cls, todo_id: UUID) -> None:
        service = ctx.state(cls)
        await service.completing(todo_id)
```

### Database Integration State

```python
from haiway import State
import asyncpg

class PostgresConfig(State):
    host: str = "localhost"
    port: int = 5432
    database: str = "todos"
    username: str = "postgres"
    password: str = "password"

class PostgresClient(State):
    config: PostgresConfig
    pool: asyncpg.Pool
    
    @classmethod
    async def create_disposable(cls):
        """Factory method for creating disposable client"""
        config = PostgresConfig()
        pool = await asyncpg.create_pool(
            host=config.host,
            port=config.port,
            database=config.database,
            user=config.username,
            password=config.password,
        )
        
        try:
            yield cls(config=config, pool=pool)
        finally:
            await pool.close()
```

## Route Handlers

Routes can access the context and its state directly:

```python
from fastapi import APIRouter
from starlette.responses import Response
from uuid import UUID
from haiway import ctx

router = APIRouter()

@router.post(
    path="/todo/{identifier}/complete",
    description="Complete a TODO.",
    status_code=204,
    responses={
        204: {"description": "TODO has been completed!"},
        500: {"description": "Internal server error"},
    },
)
async def complete_todo_endpoint(identifier: UUID) -> Response:
    """Complete a todo item"""
    # Business logic function that uses context internally
    await complete_todo(identifier=identifier)
    return Response(status_code=204)

async def complete_todo(identifier: UUID) -> None:
    """Business logic function"""
    # Access service from context
    await TodoService.complete_todo(identifier)
```

## Resource Management

Resources are automatically managed through Haiway's disposables system:

```python
from contextlib import asynccontextmanager
from haiway import ctx

@asynccontextmanager
async def database_resource():
    """Database connection resource manager"""
    config = PostgresConfig()
    pool = await asyncpg.create_pool(
        host=config.host,
        port=config.port,
        database=config.database,
        user=config.username,
        password=config.password,
    )
    
    try:
        yield DatabaseState(pool=pool)
    finally:
        await pool.close()

# Usage in application lifespan
async with ctx.scope("app", disposables=(database_resource(),)):
    # Database is available to all request contexts
    pass
```

## Error Handling

The middleware provides structured error handling:

```python
# Automatic error logging with context
try:
    result = await some_operation()
except BusinessLogicError as exc:
    ctx.log_error(f"Business logic error: {exc}")
    raise HTTPException(status_code=400, detail=str(exc))
except Exception as exc:
    ctx.log_error(f"Unexpected error: {exc}")
    raise HTTPException(status_code=500, detail="Internal server error")
```

## Testing

Testing with Haiway and FastAPI:

```python
import pytest
from fastapi.testclient import TestClient
from haiway import ctx

class MockTodoRepository:
    async def complete_todo(self, todo_id: UUID) -> None:
        # Mock implementation
        pass

@pytest.mark.asyncio
async def test_complete_todo():
    mock_repo = MockTodoRepository()
    service = TodoService(repository=mock_repo)
    
    async with ctx.scope("test", service):
        # Test the business logic
        await complete_todo(UUID("12345678-1234-5678-9012-123456789012"))

def test_complete_todo_endpoint():
    with TestClient(app) as client:
        response = client.post("/todo/12345678-1234-5678-9012-123456789012/complete")
        assert response.status_code == 204
```

## Running the Example

1. **Install dependencies**:
   ```bash
   pip install fastapi uvicorn haiway[opentelemetry]
   ```

2. **Start the server**:
   ```bash
   uvicorn server.application:app --reload
   ```

3. **Access the API**:
   - Swagger UI: http://localhost:8000/swagger
   - ReDoc: http://localhost:8000/redoc
   - OpenAPI spec: http://localhost:8000/openapi.json

## Key Benefits

1. **Automatic Context Management** - Each request gets its own context with proper resource access
2. **Structured Error Handling** - Consistent error handling with tracing support
3. **Resource Cleanup** - Automatic cleanup of database connections and other resources
4. **Dependency Injection** - Clean separation of concerns using protocols and state
5. **Observability** - Built-in logging and metrics collection
6. **Type Safety** - Full type checking support throughout the application

## Best Practices

1. **Use Context Middleware** - Always add the context middleware for proper resource management
2. **Define Clear Protocols** - Use protocols to define service contracts
3. **Handle Errors Gracefully** - Provide meaningful error messages and proper HTTP status codes
4. **Test with Mocks** - Use dependency injection for easy testing
5. **Monitor Performance** - Leverage built-in metrics and tracing capabilities

This example demonstrates how Haiway's structured concurrency and state management naturally complement FastAPI's async capabilities, resulting in maintainable and robust web applications.