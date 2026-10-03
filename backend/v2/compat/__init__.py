"""Legacy API contract (`/api/v1/*`) served on v2 data, so the original SetuAI pages run unmodified in v2 mode.

Nothing here is a second source of truth: every handler authorises like a v2 request (verified token -> ACTIVE membership of the project named by X-Project-ID ->
role permission), then calls the v2 domain services, which re-verify inside their transactions and sit on the database guards. Legacy identifiers are translated
explicitly: legacy `activity_id` = `external_activity_id` of the viewed schedule version, legacy `schedule_id` = v2 `version_id`, legacy `event_id` = v2 claim id.
"""


def routers():
    """all legacy-contract routers (imported lazily: they pull in the original extraction / evaluator modules)"""
    from . import claims_api
    return [claims_api.router]
