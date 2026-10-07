"""Project permissions by project role. The database enforces the same separation independently (migrations 0001-0014); the domain
services re-check the role inside each transaction. A permission here is only the first, cheap gate."""
from __future__ import annotations

from typing import Dict, Set

PM, SUP, SE = "PROJECT_MANAGER", "SUPERVISOR", "SITE_ENGINEER"

VIEW_PROJECT, VIEW_SCHEDULE = "VIEW_PROJECT", "VIEW_SCHEDULE"
MANAGE_PROJECT, MANAGE_SETTINGS = "MANAGE_PROJECT", "MANAGE_SETTINGS"
MANAGE_MEMBERS, VIEW_MEMBERS = "MANAGE_MEMBERS", "VIEW_MEMBERS"
MANAGE_SCHEDULE, ACTIVATE_SCHEDULE = "MANAGE_SCHEDULE", "ACTIVATE_SCHEDULE"
UPLOAD_REPORT = "UPLOAD_REPORT"                  # report / photograph / evidence files (Site Engineer: all kinds)
UPLOAD_EVIDENCE = "UPLOAD_EVIDENCE"              # issue evidence (Supervisor and Site Engineer)
VIEW_DOCUMENTS = "VIEW_DOCUMENTS"                # role-filtered inside the service
SUBMIT_CLAIM, VIEW_OWN_CLAIMS = "SUBMIT_CLAIM", "VIEW_OWN_CLAIMS"
REVIEW_CLAIMS = "REVIEW_CLAIMS"                  # queue, claim detail, decisions
VIEW_CLAIM_COUNTS = "VIEW_CLAIM_COUNTS"          # aggregate counts only
REPORT_ISSUE, VIEW_ISSUES, RESOLVE_ISSUE = "REPORT_ISSUE", "VIEW_ISSUES", "RESOLVE_ISSUE"
VIEW_DASHBOARD, VIEW_AUDIT = "VIEW_DASHBOARD", "VIEW_AUDIT"
VIEW_ROOT_CAUSES = "VIEW_ROOT_CAUSES"            # root causes: Supervisor and Project Manager
REQUEST_REOPEN, APPROVE_REOPEN = "REQUEST_REOPEN", "APPROVE_REOPEN"       # governed reopen of a completed activity: engineers and supervisors request, a supervisor decides
VIEW_QUALITY, MANAGE_QUALITY, SUBMIT_QUALITY_EVIDENCE = "VIEW_QUALITY", "MANAGE_QUALITY", "SUBMIT_QUALITY_EVIDENCE"      # quality gates: all read; Supervisor manages / decides; engineers and supervisors submit evidence

# Page-level permissions (each guards one screen and its own endpoints, independently of the broader permission above it):
#   VIEW_WBS_EXPLORER          WBS Explorer: Supervisor and Site Engineer, not the Project Manager
#   VIEW_AUDIT_TRAIL           Audit Trail page, chain verification and dossier: Project Manager only (the Supervisor keeps VIEW_AUDIT for the per-activity history)
#   VIEW_PROJECT_INTELLIGENCE  Project Intelligence (supervising agent) briefing / questions: Project Manager and Site Engineer, not the Supervisor
VIEW_WBS_EXPLORER, VIEW_AUDIT_TRAIL, VIEW_PROJECT_INTELLIGENCE = "VIEW_WBS_EXPLORER", "VIEW_AUDIT_TRAIL", "VIEW_PROJECT_INTELLIGENCE"
#   VIEW_PROJECT_KNOWLEDGE     read the authored project context (every member; it carries no claim content) -- MANAGE_PROJECT_KNOWLEDGE writes it (Project Manager)
VIEW_PROJECT_KNOWLEDGE, MANAGE_PROJECT_KNOWLEDGE = "VIEW_PROJECT_KNOWLEDGE", "MANAGE_PROJECT_KNOWLEDGE"

ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    PM: {VIEW_PROJECT, VIEW_SCHEDULE, MANAGE_PROJECT, MANAGE_SETTINGS, MANAGE_MEMBERS, VIEW_MEMBERS, MANAGE_SCHEDULE, ACTIVATE_SCHEDULE,
         VIEW_DOCUMENTS, VIEW_CLAIM_COUNTS, VIEW_ISSUES, VIEW_DASHBOARD, VIEW_AUDIT, VIEW_ROOT_CAUSES, VIEW_QUALITY, VIEW_AUDIT_TRAIL, VIEW_PROJECT_INTELLIGENCE, VIEW_PROJECT_KNOWLEDGE, MANAGE_PROJECT_KNOWLEDGE},
    SUP: {VIEW_PROJECT, VIEW_SCHEDULE, VIEW_MEMBERS, UPLOAD_EVIDENCE, VIEW_DOCUMENTS, REVIEW_CLAIMS, VIEW_CLAIM_COUNTS, REPORT_ISSUE, VIEW_ISSUES, RESOLVE_ISSUE,
          VIEW_DASHBOARD, VIEW_AUDIT, VIEW_ROOT_CAUSES, VIEW_QUALITY, MANAGE_QUALITY, SUBMIT_QUALITY_EVIDENCE, REQUEST_REOPEN, APPROVE_REOPEN, VIEW_WBS_EXPLORER, VIEW_PROJECT_KNOWLEDGE},
    SE: {VIEW_PROJECT, VIEW_SCHEDULE, UPLOAD_REPORT, UPLOAD_EVIDENCE, VIEW_DOCUMENTS, SUBMIT_CLAIM, VIEW_OWN_CLAIMS, REPORT_ISSUE, VIEW_ISSUES, VIEW_DASHBOARD, VIEW_QUALITY, SUBMIT_QUALITY_EVIDENCE, REQUEST_REOPEN, VIEW_WBS_EXPLORER, VIEW_PROJECT_INTELLIGENCE, VIEW_PROJECT_KNOWLEDGE},
}
