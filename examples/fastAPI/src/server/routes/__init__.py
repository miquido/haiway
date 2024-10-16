from server.routes.technical import router as technical_router
from server.routes.todos import router as todos_router

__all__ = [
    "technical_router",
    "todos_router",
]
