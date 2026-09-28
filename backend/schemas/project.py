"""
Project schemas for SETUAI V7.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    project_code: str = Field(..., min_length=2, max_length=50, description="Unique human-readable project identifier")
    project_name: str = Field(..., min_length=2, max_length=255, description="Full name of project")
    description: Optional[str] = Field(None, description="Detailed description")
    client_name: Optional[str] = Field(None, description="Client or owner organisation")
    project_type: Optional[str] = Field(None, description="Infrastructure sector/type (e.g. HIGHWAY, METRO, BRIDGE)")
    location: Optional[str] = Field(None, description="Geographic location name")
    latitude: Optional[float] = Field(None, ge=-90.0, le=90.0, description="Latitude in decimal degrees")
    longitude: Optional[float] = Field(None, ge=-180.0, le=180.0, description="Longitude in decimal degrees")
    geofence_radius_m: Optional[float] = Field(None, ge=0.0, description="Geofence radius in meters")
    planned_start: Optional[date] = Field(None, description="Planned start date")
    planned_finish: Optional[date] = Field(None, description="Planned finish date")
    contract_finish: Optional[date] = Field(None, description="Contractual finish date")


class ProjectUpdate(BaseModel):
    project_name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = None
    client_name: Optional[str] = None
    project_type: Optional[str] = None
    location: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(None, ge=-180.0, le=180.0)
    geofence_radius_m: Optional[float] = Field(None, ge=0.0)
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    contract_finish: Optional[date] = None


class ProjectResponse(BaseModel):
    project_id: uuid.UUID
    project_code: str
    project_name: str
    description: Optional[str] = None
    client_name: Optional[str] = None
    project_type: Optional[str] = None
    location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geofence_radius_m: Optional[float] = None
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    contract_finish: Optional[date] = None
    status: str
    created_by: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProjectListItem(BaseModel):
    project_id: uuid.UUID
    project_code: str
    project_name: str
    status: str
    assigned_role: str
