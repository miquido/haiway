from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import Logger, getLogger
from typing import Final

from fastapi import FastAPI
from haiway import Dependencies, Structure, frozenlist

from server.middlewares import ContextMiddleware
from server.routes import technical_router, todos_router

__all__ = [
    "app",
]

# define common state available for all endpoints
STATE: Final[frozenlist[Structure]] = ()


async def startup(app: FastAPI) -> None:
    """
    Startup function is called when the server process starts.
    """
    logger: Logger = getLogger("server")
    if __debug__:
        logger.warning("Starting DEBUG server...")

    else:
        logger.info("Starting server...")

    app.extra["state"] = STATE  # include base state for all endpoints

    logger.info("...server started!")


async def shutdown(app: FastAPI) -> None:
    """
    Shutdown function is called when server process ends.
    """
    await Dependencies.dispose()  # dispose all dependencies on shutdown

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
