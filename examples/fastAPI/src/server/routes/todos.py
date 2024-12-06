from uuid import UUID

from fastapi import APIRouter
from starlette.responses import Response

from features.todos import complete_todo

__all__ = [
    "router",
]

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
    await complete_todo(identifier=identifier)
    return Response(status_code=204)
