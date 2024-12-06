import uvicorn

from server.application import app
from server.config import SERVER_HOST, SERVER_PORT

uvicorn.run(
    app,
    host=SERVER_HOST,
    port=SERVER_PORT,
)
