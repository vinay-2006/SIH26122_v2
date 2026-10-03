"""Legacy API contract (`/api/v1/*`) served on v2 data, so the original SetuAI pages run unmodified in v2 mode.

Nothing here is a second source of truth: every handler authorises like a v2 request (verified token -> ACTIVE membership of the project named by X-Project-ID ->
role permission), then calls the v2 domain services, which re-verify inside their transactions and sit on the database guards. Legacy identifiers are translated
explicitly: legacy `activity_id` = `external_activity_id` of the viewed schedule version, legacy `schedule_id` = v2 `version_id`, legacy `event_id` = v2 claim id.
"""


def routers():
    """all legacy-contract routers (imported lazily: they pull in the original extraction / evaluator modules)"""
    from . import agent_api, batches_api, claims_api, dossier_api, impact_api, intelligence_api, issues_api, quality_api, reopen_api, review_api, schedule_api, updates_api
    return [agent_api.router, dossier_api.router, intelligence_api.router, review_api.router, impact_api.router, claims_api.router, updates_api.router, issues_api.router, schedule_api.router, batches_api.router, quality_api.router, reopen_api.router]
