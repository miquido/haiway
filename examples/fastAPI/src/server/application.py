from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import Logger, getLogger

from fastapi import FastAPI
from haiway import Disposables, setup_logging

from integrations.postgres import PostgresClient
from server.middlewares import ContextMiddleware
from server.routes import technical_router, todos_router

__all__ = [
    "app",
]


async def startup(app: FastAPI) -> None:
    """
    Startup function is called when the server process starts.
    """
    setup_logging("server")  # setup logging

    logger: Logger = getLogger("server")
    if __debug__:
        logger.warning("Starting DEBUG server...")

    else:
        logger.info("Starting server...")

    # initialize all shared clients on startup
    disposables = Disposables(
        PostgresClient(),
    )
    app.extra["disposables"] = disposables

    # prepare common state for all endpoints
    app.extra["state"] = (*await disposables.__aenter__(),)

    logger.info("...server started!")


async def shutdown(app: FastAPI) -> None:
    """
    Shutdown function is called when server process ends.
    """
    # dispose all clients on shutdown
    await app.extra["disposables"].__aexit(
        None,
        None,
        None,
    )

    getLogger("server").info("...server shutdown!")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await startup(app)
    yield
    await shutdown(app)


app: FastAPI = FastAPI(
    title="haiway-fastapi",
    description="Example project using haiway with fastapi",
    version="0.1.0",
    lifespan=lifespan,
    openapi_url="/openapi.json" if __debug__ else None,
    docs_url="/swagger" if __debug__ else None,
    redoc_url="/redoc" if __debug__ else None,
)

# middlewares
app.add_middleware(ContextMiddleware)

# routes
app.include_router(technical_router)
app.include_router(todos_router)
