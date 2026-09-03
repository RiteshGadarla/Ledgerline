from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    status: Literal["ok"]


@router.get("/health", response_model=HealthStatus)
async def health() -> HealthStatus:
    """Liveness probe. No auth, no dependencies -- just confirms the process is up."""
    return HealthStatus(status="ok")
