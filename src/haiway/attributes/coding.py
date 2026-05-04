import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID

__all__ = ("AttributesJSONEncoder",)


class AttributesJSONEncoder(json.JSONEncoder):
    def default(self, o: object) -> Any:
        if isinstance(o, UUID):
            return str(o)

        elif isinstance(o, datetime):
            return o.isoformat()

        elif isinstance(o, time):
            return o.isoformat()

        elif isinstance(o, date):
            return o.isoformat()

        elif isinstance(o, Path):
            return o.as_posix()

        else:
            return json.JSONEncoder.default(self, o)
