"""
Contractor schemas for SETUAI V7.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ContractorCreate(BaseModel):
    contractor_code: str = Field(..., min_length=1, max_length=50, description="Project-unique contractor code")
    company_name: str = Field(..., min_length=1, max_length=255, description="Contractor company name")
    type_or_category: Optional[str] = Field(None, description="Category (e.g. MAIN_CONTRACTOR, SUBCONTRACTOR)")
    contract_reference: Optional[str] = Field(None, description="Contract reference number")
    contact_email: Optional[str] = Field(None, description="Contact email address")
    status: Optional[str] = Field("ACTIVE", description="Status: ACTIVE, INACTIVE, SUSPENDED, TERMINATED")
    active: Optional[bool] = Field(True, description="Active status flag")


class ContractorUpdate(BaseModel):
    company_name: Optional[str] = Field(None, min_length=1, max_length=255)
    type_or_category: Optional[str] = None
    contract_reference: Optional[str] = None
    contact_email: Optional[str] = None
    status: Optional[str] = None
    active: Optional[bool] = None


class ContractorResponse(BaseModel):
    contractor_id: uuid.UUID
    project_id: uuid.UUID
    contractor_code: str
    company_name: str
    type_or_category: Optional[str] = None
    contract_reference: Optional[str] = None
    contact_email: Optional[str] = None
    status: str
    active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
