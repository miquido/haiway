from fastapi import APIRouter
from starlette.responses import Response

__all__ = [
    "router",
]

router = APIRouter()


@router.get(
    path="/health",
    description="Server health check.",
    status_code=204,
    responses={
        204: {"description": "Server up and running!"},
        500: {"description": "Internal server error"},
    },
)
async def health() -> Response:
    return Response(status_code=204)
