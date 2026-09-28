"""
Standardized Security Error Contract for SETUAI V7.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from fastapi import HTTPException, status


class SecurityErrorCode:
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    INVALID_TOKEN = "INVALID_TOKEN"
    PROJECT_ACCESS_DENIED = "PROJECT_ACCESS_DENIED"
    SCHEDULE_ACCESS_DENIED = "SCHEDULE_ACCESS_DENIED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    ROLE_REQUIRED = "ROLE_REQUIRED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    INVALID_PROJECT_CONTEXT = "INVALID_PROJECT_CONTEXT"
    INVALID_SCHEDULE_CONTEXT = "INVALID_SCHEDULE_CONTEXT"


class SecurityException(HTTPException):
    """
    Standardized security exception that returns structured, non-leaking error responses.
    """
    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        content = {
            "error_code": error_code,
            "message": message,
        }
        if details:
            content["details"] = details
        super().__init__(
            status_code=status_code,
            detail=content,
            headers=headers,
        )


def raise_auth_required(message: str = "Authentication credentials are required") -> None:
    raise SecurityException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        error_code=SecurityErrorCode.AUTHENTICATION_REQUIRED,
        message=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def raise_invalid_token(message: str = "Invalid or expired authentication token") -> None:
    raise SecurityException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        error_code=SecurityErrorCode.INVALID_TOKEN,
        message=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def raise_project_denied(message: str = "Access denied to requested project") -> None:
    raise SecurityException(
        status_code=status.HTTP_403_FORBIDDEN,
        error_code=SecurityErrorCode.PROJECT_ACCESS_DENIED,
        message=message,
    )


def raise_schedule_denied(message: str = "Schedule does not belong to authorized project context") -> None:
    raise SecurityException(
        status_code=status.HTTP_403_FORBIDDEN,
        error_code=SecurityErrorCode.SCHEDULE_ACCESS_DENIED,
        message=message,
    )


def raise_permission_denied(
    permission: str,
    role: str,
    message: Optional[str] = None,
) -> None:
    raise SecurityException(
        status_code=status.HTTP_403_FORBIDDEN,
        error_code=SecurityErrorCode.PERMISSION_DENIED,
        message=message or f"Operation requires permission '{permission}', caller role is '{role}'",
        details={"required_permission": permission, "caller_role": role},
    )


def raise_role_required(
    required_roles: list[str],
    role: str,
    message: Optional[str] = None,
) -> None:
    raise SecurityException(
        status_code=status.HTTP_403_FORBIDDEN,
        error_code=SecurityErrorCode.ROLE_REQUIRED,
        message=message or f"Operation requires role in {required_roles}, caller role is '{role}'",
        details={"required_roles": required_roles, "caller_role": role},
    )


def raise_invalid_project_context(message: str = "Invalid or missing project context") -> None:
    raise SecurityException(
        status_code=status.HTTP_400_BAD_REQUEST,
        error_code=SecurityErrorCode.INVALID_PROJECT_CONTEXT,
        message=message,
    )


def raise_invalid_schedule_context(message: str = "Invalid or missing schedule context") -> None:
    raise SecurityException(
        status_code=status.HTTP_400_BAD_REQUEST,
        error_code=SecurityErrorCode.INVALID_SCHEDULE_CONTEXT,
        message=message,
    )


def raise_resource_not_found(resource: str = "Resource", identifier: Optional[str] = None) -> None:
    msg = f"{resource} not found" if identifier is None else f"{resource} '{identifier}' not found"
    raise SecurityException(
        status_code=status.HTTP_404_NOT_FOUND,
        error_code=SecurityErrorCode.RESOURCE_NOT_FOUND,
        message=msg,
    )
