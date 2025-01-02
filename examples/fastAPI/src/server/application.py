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


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("server")

    logger: Logger = getLogger("server")
    if __debug__:
        logger.warning("Starting DEBUG server...")

    else:
        logger.info("Starting server...")

    disposables = Disposables(
        PostgresClient(),
    )
    async with disposables as state:
        app.extra["state"] = (*state,)

        logger.info("...server started...")
        yield  # suspend until server shutdown

    logger.info("...server shutdown!")


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
