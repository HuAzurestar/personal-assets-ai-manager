"""Exact SQLite resource IDs; amounts and draft row indices are not IDs."""
from typing import Annotated

from pydantic import Field, StrictInt


SQLITE_ID_MAX = 2**63 - 1
PositiveId = Annotated[StrictInt, Field(gt=0, le=SQLITE_ID_MAX)]
NonnegativeId = Annotated[StrictInt, Field(ge=0, le=SQLITE_ID_MAX)]
