"""Project knowledge: the authored context of a project. Every member reads (it carries no claim content); only the Project Manager writes."""
from __future__ import annotations

import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import permissions as P
from ..auth import ProjectAccess, require
from ..domain import knowledge as dk
from ._common import ERRORS, actor_of

router = APIRouter(prefix="/api/v2/projects/{project_id}/knowledge", tags=["project knowledge"], responses=ERRORS)
Section = Literal["OVERVIEW", "SCOPE", "CONTRACT", "SITE", "STAKEHOLDERS", "MILESTONES", "CONSTRAINTS", "RISKS", "SAFETY_QUALITY", "PROCUREMENT", "REPORTING", "GLOSSARY"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntryBody(Strict):
    section: Section
    title: str = Field(min_length=3, max_length=160)
    body: str = Field(min_length=3, max_length=8000)
    provenance: Literal["AUTHORED", "ILLUSTRATIVE", "NOT_SPECIFIED"] = Field(default="AUTHORED", description="where the statement comes from; entries generated from the project's records are marked FROM_RECORDS by the system")
    tags: List[str] = Field(default_factory=list, max_length=20)
    sort_order: int = Field(default=100, ge=0, le=100000)


@router.get("", summary="The project's knowledge entries, grouped in section order (every member)")
def entries(section: Optional[Section] = None, include_retired: bool = Query(False, description="Project Manager only"), access: ProjectAccess = Depends(require(P.VIEW_PROJECT_KNOWLEDGE))):
    return {"sections": [{"section": s, "label": dk.SECTION_LABEL[s]} for s in dk.SECTIONS], "items": dk.list_entries(actor_of(access), section, include_retired)}


@router.post("", status_code=201, summary="Add an entry (Project Manager)")
def create(body: EntryBody, access: ProjectAccess = Depends(require(P.MANAGE_PROJECT_KNOWLEDGE, writable=True))):
    return dk.upsert(actor_of(access), **body.model_dump())


@router.put("/{knowledge_id}", summary="Edit an entry; its version advances and the change is audited (Project Manager)")
def update(knowledge_id: uuid.UUID, body: EntryBody, access: ProjectAccess = Depends(require(P.MANAGE_PROJECT_KNOWLEDGE, writable=True))):
    return dk.upsert(actor_of(access), knowledge_id=knowledge_id, **body.model_dump())


@router.post("/{knowledge_id}/retire", summary="Retire an entry (it is kept, no longer used; Project Manager)")
def retire(knowledge_id: uuid.UUID, access: ProjectAccess = Depends(require(P.MANAGE_PROJECT_KNOWLEDGE, writable=True))):
    return dk.retire(actor_of(access), knowledge_id)
