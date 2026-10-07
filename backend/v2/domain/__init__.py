"""Execution-workflow domain services (Phase 3A): claims, supervisor decisions, issues, rollups, timelines.
No HTTP here. Every function takes a ProjectActor (identity + project role resolved from ACTIVE membership), runs in its own transaction as
that actor (so the database role guards apply) and raises ApiError with a stable `code` for every refusal."""
