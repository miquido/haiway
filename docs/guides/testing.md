# Testing

Haiway's architecture makes testing straightforward through dependency injection and context isolation. This guide covers testing strategies and best practices.

## Basic Testing Setup

### Test Dependencies

```bash
pip install pytest pytest-asyncio haiway
```

### Simple State Testing

```python
import pytest
from haiway import State

class Counter(State):
    value: int = 0
    
    def increment(self) -> "Counter":
        return self.updated(value=self.value + 1)
    
    def add(self, amount: int) -> "Counter":
        return self.updated(value=self.value + amount)

def test_counter_increment():
    counter = Counter()
    assert counter.value == 0
    
    incremented = counter.increment()
    assert incremented.value == 1
    assert counter.value == 0  # Original unchanged

def test_counter_add():
    counter = Counter(value=5)
    result = counter.add(10)
    assert result.value == 15

def test_counter_immutability():
    counter = Counter(value=10)
    
    with pytest.raises(AttributeError):
        counter.value = 20  # Should raise error
```

## Context-Based Testing

### Testing with Mock Dependencies

```python
import pytest
from haiway import ctx, State
from typing import Protocol, runtime_checkable

@runtime_checkable
class EmailSending(Protocol):
    async def __call__(self, to: str, subject: str, body: str) -> bool: ...

class NotificationService(State):
    email_sending: EmailSending
    
    @classmethod
    async def send_welcome_email(cls, user_email: str) -> bool:
        service = ctx.state(cls)
        return await service.email_sending(
            to=user_email,
            subject="Welcome!",
            body="Welcome to our service!"
        )

class MockEmailSending:
    def __init__(self):
        self.sent_emails = []
    
    async def __call__(self, to: str, subject: str, body: str) -> bool:
        self.sent_emails.append({
            "to": to,
            "subject": subject,
            "body": body
        })
        return True

@pytest.mark.asyncio
async def test_send_welcome_email():
    mock_email = MockEmailSending()
    service = NotificationService(email_sending=mock_email)
    
    async with ctx.scope("test", service):
        result = await NotificationService.send_welcome_email("alice@example.com")
        
        assert result is True
        assert len(mock_email.sent_emails) == 1
        assert mock_email.sent_emails[0]["to"] == "alice@example.com"
        assert "Welcome" in mock_email.sent_emails[0]["subject"]
```

### Testing Resource Management

```python
from contextlib import asynccontextmanager
import pytest

class DatabaseConnection:
    def __init__(self):
        self.connected = True
        self.queries = []
    
    async def execute(self, query: str) -> list[dict]:
        if not self.connected:
            raise RuntimeError("Not connected")
        self.queries.append(query)
        return [{"result": "data"}]
    
    async def close(self):
        self.connected = False

class DatabaseService(State):
    connection: DatabaseConnection
    
    @classmethod
    async def get_users(cls) -> list[dict]:
        service = ctx.state(cls)
        return await service.connection.execute("SELECT * FROM users")

@asynccontextmanager
async def test_database():
    """Test database resource"""
    connection = DatabaseConnection()
    try:
        yield DatabaseService(connection=connection)
    finally:
        await connection.close()

@pytest.mark.asyncio
async def test_database_service():
    async with ctx.scope("test", disposables=(test_database(),)):
        users = await DatabaseService.get_users()
        
        assert len(users) == 1
        assert users[0]["result"] == "data"
        
        # Verify connection was used
        db_service = ctx.state(DatabaseService)
        assert len(db_service.connection.queries) == 1
        assert "SELECT * FROM users" in db_service.connection.queries[0]
```

## Testing Async Patterns

### Testing Timeouts

```python
import asyncio
import pytest
from haiway.helpers import timeout

async def slow_operation():
    await asyncio.sleep(2)
    return "completed"

async def fast_operation():
    await asyncio.sleep(0.1)
    return "quick"

@pytest.mark.asyncio
async def test_timeout_success():
    result = await timeout(fast_operation(), seconds=1)
    assert result == "quick"

@pytest.mark.asyncio
async def test_timeout_failure():
    with pytest.raises(asyncio.TimeoutError):
        await timeout(slow_operation(), seconds=1)
```

### Testing Retries

```python
import pytest
from haiway.helpers import retry

class UnreliableService:
    def __init__(self, fail_count: int = 2):
        self.fail_count = fail_count
        self.attempt_count = 0
    
    async def operation(self):
        self.attempt_count += 1
        if self.attempt_count <= self.fail_count:
            raise ValueError(f"Attempt {self.attempt_count} failed")
        return f"Success on attempt {self.attempt_count}"

@pytest.mark.asyncio
async def test_retry_success():
    service = UnreliableService(fail_count=2)
    
    result = await retry(
        service.operation(),
        max_attempts=3,
        delay=0.1
    )
    
    assert result == "Success on attempt 3"
    assert service.attempt_count == 3

@pytest.mark.asyncio
async def test_retry_failure():
    service = UnreliableService(fail_count=5)
    
    with pytest.raises(ValueError):
        await retry(
            service.operation(),
            max_attempts=3,
            delay=0.1
        )
    
    assert service.attempt_count == 3
```

### Testing Concurrent Operations

```python
import pytest
from haiway.helpers import process_concurrently

async def process_item(item: str) -> str:
    if item == "error":
        raise ValueError("Processing failed")
    return f"processed-{item}"

@pytest.mark.asyncio
async def test_concurrent_processing_success():
    items = ["item1", "item2", "item3"]
    
    results = await process_concurrently(
        process_item,
        items,
        max_concurrency=2
    )
    
    assert len(results) == 3
    assert all(r.startswith("processed-") for r in results)

@pytest.mark.asyncio
async def test_concurrent_processing_with_errors():
    items = ["item1", "error", "item3"]
    
    results = await process_concurrently(
        process_item,
        items,
        max_concurrency=2,
        stop_on_error=False
    )
    
    assert len(results) == 3
    assert isinstance(results[1], ValueError)
    assert results[0] == "processed-item1"
    assert results[2] == "processed-item3"
```

## Testing Fixtures

### Reusable Test Fixtures

```python
import pytest
from haiway import ctx

@pytest.fixture
async def mock_services():
    """Fixture providing mock services"""
    class MockUserService:
        def __init__(self):
            self.users = {"123": {"id": "123", "name": "Alice"}}
        
        async def get_user(self, user_id: str) -> dict | None:
            return self.users.get(user_id)
    
    class MockEmailService:
        def __init__(self):
            self.sent_emails = []
        
        async def send_email(self, to: str, subject: str, body: str) -> bool:
            self.sent_emails.append({"to": to, "subject": subject, "body": body})
            return True
    
    return {
        "user_service": MockUserService(),
        "email_service": MockEmailService()
    }

@pytest.fixture
async def test_context(mock_services):
    """Test context with mock services"""
    user_service_state = UserService(service=mock_services["user_service"])
    email_service_state = EmailService(service=mock_services["email_service"])
    
    async with ctx.scope("test", user_service_state, email_service_state) as scope:
        yield scope

@pytest.mark.asyncio
async def test_user_workflow(test_context, mock_services):
    async with test_context:
        # Test user retrieval
        user = await UserService.get_user("123")
        assert user["name"] == "Alice"
        
        # Test email sending
        success = await EmailService.send_welcome_email(user["id"])
        assert success is True
        
        # Verify email was sent
        emails = mock_services["email_service"].sent_emails
        assert len(emails) == 1
        assert emails[0]["to"] == "123"
```

### Parameterized Tests

```python
import pytest
from haiway import State

class Calculator(State):
    precision: int = 2
    
    def add(self, a: float, b: float) -> float:
        return round(a + b, self.precision)
    
    def multiply(self, a: float, b: float) -> float:
        return round(a * b, self.precision)

@pytest.mark.parametrize("a,b,expected", [
    (1.0, 2.0, 3.0),
    (0.1, 0.2, 0.3),
    (-1.0, 1.0, 0.0),
    (3.14159, 2.71828, 5.86),
])
def test_calculator_add(a, b, expected):
    calc = Calculator()
    result = calc.add(a, b)
    assert result == expected

@pytest.mark.parametrize("precision,a,b,expected", [
    (0, 1.234, 2.567, 3.0),
    (1, 1.234, 2.567, 3.2),
    (2, 1.234, 2.567, 3.16),
    (3, 1.234, 2.567, 3.163),
])
def test_calculator_precision(precision, a, b, expected):
    calc = Calculator(precision=precision)
    result = calc.multiply(a, b)
    assert result == expected
```

## Integration Testing

### Testing with Real Resources

```python
import pytest
import asyncpg
from haiway import ctx

class DatabaseIntegrationTest:
    """Integration tests requiring a real database"""
    
    @pytest.fixture(scope="class")
    async def database_pool(self):
        """Real database connection for integration tests"""
        pool = await asyncpg.create_pool(
            host="localhost",
            port=5432,
            database="test_db",
            user="test_user",
            password="test_password"
        )
        
        # Setup test data
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100),
                    email VARCHAR(100)
                )
            """)
        
        yield pool
        
        # Cleanup
        async with pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS users")
        await pool.close()
    
    @pytest.mark.asyncio
    async def test_database_operations(self, database_pool):
        db_service = DatabaseService(pool=database_pool)
        
        async with ctx.scope("integration-test", db_service):
            # Test create
            user_id = await DatabaseService.create_user("Alice", "alice@example.com")
            assert user_id is not None
            
            # Test read
            user = await DatabaseService.get_user(user_id)
            assert user["name"] == "Alice"
            assert user["email"] == "alice@example.com"
            
            # Test update
            await DatabaseService.update_user(user_id, name="Alice Smith")
            updated_user = await DatabaseService.get_user(user_id)
            assert updated_user["name"] == "Alice Smith"
```

### Testing HTTP APIs

```python
import pytest
import aiohttp
from haiway import ctx

class APIIntegrationTest:
    """Integration tests for HTTP APIs"""
    
    @pytest.fixture
    async def http_session(self):
        """HTTP client session"""
        async with aiohttp.ClientSession() as session:
            yield session
    
    @pytest.mark.asyncio
    async def test_api_client(self, http_session):
        api_client = HTTPAPIClient(session=http_session, base_url="https://api.example.com")
        
        async with ctx.scope("api-test", api_client):
            # Test successful request
            data = await HTTPAPIClient.get_user("123")
            assert data["id"] == "123"
            
            # Test error handling
            with pytest.raises(APIError):
                await HTTPAPIClient.get_user("nonexistent")
```

## Performance Testing

### Load Testing

```python
import pytest
import asyncio
import time
from haiway import ctx

@pytest.mark.asyncio
async def test_concurrent_load():
    """Test system under concurrent load"""
    
    async def simulate_request():
        async with ctx.scope("request"):
            # Simulate request processing
            await asyncio.sleep(0.1)
            return "success"
    
    start_time = time.time()
    
    # Run 100 concurrent requests
    tasks = [simulate_request() for _ in range(100)]
    results = await asyncio.gather(*tasks)
    
    end_time = time.time()
    duration = end_time - start_time
    
    assert len(results) == 100
    assert all(r == "success" for r in results)
    assert duration < 2.0  # Should complete within 2 seconds
    
    print(f"Processed 100 requests in {duration:.2f} seconds")
```

### Memory Usage Testing

```python
import pytest
import asyncio
import psutil
import os

@pytest.mark.asyncio
async def test_memory_usage():
    """Test memory usage under load"""
    process = psutil.Process(os.getpid())
    initial_memory = process.memory_info().rss
    
    # Create many contexts and states
    contexts = []
    for i in range(1000):
        state = TestState(value=f"test-{i}")
        contexts.append(ctx.scope(f"test-{i}", state))
    
    # Enter all contexts
    for context in contexts:
        await context.__aenter__()
    
    peak_memory = process.memory_info().rss
    memory_increase = peak_memory - initial_memory
    
    # Exit all contexts
    for context in contexts:
        await context.__aexit__(None, None, None)
    
    final_memory = process.memory_info().rss
    
    # Memory should be mostly reclaimed
    assert final_memory - initial_memory < memory_increase * 0.1
    print(f"Memory increase: {memory_increase / 1024 / 1024:.2f} MB")
    print(f"Memory after cleanup: {(final_memory - initial_memory) / 1024 / 1024:.2f} MB")
```

## Testing Best Practices

### 1. Test Structure

```python
# Arrange, Act, Assert pattern
@pytest.mark.asyncio
async def test_user_service():
    # Arrange
    mock_repo = MockUserRepository()
    service = UserService(repository=mock_repo)
    
    # Act
    async with ctx.scope("test", service):
        result = await UserService.create_user("Alice", "alice@example.com")
    
    # Assert
    assert result.name == "Alice"
    assert result.email == "alice@example.com"
    assert len(mock_repo.users) == 1
```

### 2. Isolated Tests

```python
# Each test should be independent
@pytest.mark.asyncio
async def test_user_creation():
    """Test user creation in isolation"""
    mock_repo = MockUserRepository()  # Fresh mock for each test
    service = UserService(repository=mock_repo)
    
    async with ctx.scope("test", service):
        user = await UserService.create_user("Alice", "alice@example.com")
        assert user.name == "Alice"

@pytest.mark.asyncio
async def test_user_retrieval():
    """Test user retrieval in isolation"""
    mock_repo = MockUserRepository()  # Fresh mock for each test
    mock_repo.users["123"] = User(id="123", name="Bob", email="bob@example.com")
    service = UserService(repository=mock_repo)
    
    async with ctx.scope("test", service):
        user = await UserService.get_user("123")
        assert user.name == "Bob"
```

### 3. Error Testing

```python
@pytest.mark.asyncio
async def test_error_handling():
    """Test error scenarios"""
    
    class FailingRepository:
        async def get_user(self, user_id: str):
            raise RuntimeError("Database connection failed")
    
    service = UserService(repository=FailingRepository())
    
    async with ctx.scope("test", service):
        with pytest.raises(RuntimeError, match="Database connection failed"):
            await UserService.get_user("123")
```

### 4. Test Data Management

```python
@pytest.fixture
def user_data():
    """Fixture providing test user data"""
    return {
        "valid_user": {"name": "Alice", "email": "alice@example.com"},
        "invalid_user": {"name": "", "email": "invalid-email"},
        "test_users": [
            {"name": "Alice", "email": "alice@example.com"},
            {"name": "Bob", "email": "bob@example.com"},
            {"name": "Charlie", "email": "charlie@example.com"},
        ]
    }

@pytest.mark.asyncio
async def test_user_validation(user_data):
    with pytest.raises(ValueError):
        User(**user_data["invalid_user"])
    
    user = User(**user_data["valid_user"])
    assert user.name == "Alice"
```

## Continuous Integration

### GitHub Actions Example

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: test_db
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    
    steps:
    - uses: actions/checkout@v4
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.12'
    
    - name: Install dependencies
      run: |
        pip install -e .[dev]
    
    - name: Run tests
      run: |
        pytest --cov=haiway --cov-report=xml
      env:
        DATABASE_URL: postgresql://postgres:postgres@localhost/test_db
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
```

## Next Steps

- Learn about [Architecture](architecture.md) for better test design
- Explore [Context System](context-system.md) for advanced testing patterns
- See [Examples](../examples/index.md) for complete test suites