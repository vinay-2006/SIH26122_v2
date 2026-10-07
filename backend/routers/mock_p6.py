import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)



def require_dev_mode() -> None:
    """The mock P6 is a local stand-in for a real P6 EPPM REST server (P6_BASE_URL defaults to it).
    It has no authentication and accepts writes, so it exists only in development mode, using the
    same switch as the auth layer's unsigned-token fallback (AUTH_DEV_MODE=true, read per request).
    Otherwise the routes behave as if they do not exist (404), not as an auth challenge."""
    if os.getenv("AUTH_DEV_MODE", "false").strip().lower() != "true":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(
    prefix="/api/v1/mock-p6",
    tags=["mock-p6"],
    dependencies=[Depends(require_dev_mode)],
)

_received_payloads: List[Dict[str, Any]] = []


def get_received_payloads() -> List[Dict[str, Any]]:
    return list(_received_payloads)


def clear_received_payloads() -> None:
    global _received_payloads
    _received_payloads.clear()


@router.get("/health")
def health():
    return {"router": "mock-p6", "status": "ok"}


@router.get("/received")
def received():
    """Payloads the mock P6 has received since startup (for the demo's write-back view)."""
    return {"count": len(_received_payloads), "payloads": get_received_payloads()}


class P6ActivityPayload(BaseModel):
    Id: str
    StartDate: Optional[str] = None
    FinishDate: Optional[str] = None
    PercentComplete: Optional[float] = None


@router.post("/activities/{activity_id}")
def update_activity(activity_id: str, payload: P6ActivityPayload):
    """
    Local mock endpoint standing in for a real P6 EPPM REST API server.
    Accepts canonical P6 request shape: {"Id", "StartDate", "FinishDate", "PercentComplete"}.
    Validates that body Id matches path activity_id.
    """
    if payload.Id != activity_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payload Id '{payload.Id}' does not match path activity_id '{activity_id}'",
        )

    payload_dict = payload.model_dump(exclude_unset=True)
    _received_payloads.append(payload_dict)

    return {
        "status": "success",
        "activity_id": activity_id,
        "p6_id": payload.Id,
        "message": "Activity actuals updated in mock P6 EPPM",
    }
