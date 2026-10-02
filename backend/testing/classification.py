"""
Test classification. Every test file is exactly one of:
  DB_FREE         no database (pure logic, or in-memory SQLite). Always safe.
  DB_READ         only SELECTs against a real Postgres.
  DB_WRITE        inserts/updates/deletes rows it creates.
  DB_DESTRUCTIVE  can delete/alter data it did not create (none currently; fix or isolate).
plus optional overlays:
  INTEGRATION     crosses phase boundaries / isolation / RLS
  E2E             full workflow through API/browser (none exist yet)
Anything not listed here is UNCLASSIFIED and is treated as DB_WRITE (fail closed).
Per-test overrides use "path::test_name".
"""
DB_FREE = {
    # v2 hardening: token verification, target guard, scale (no database)
    "tests/v2_hardening/test_jwt_verification.py", "tests/v2_hardening/test_target_guard.py", "tests/v2_hardening/test_reconcile_scale.py",
    # schedule import: parsers, validation, reconciliation, upload guard (pure logic, no database)
    "tests/schedule_import/test_parsers_golden.py", "tests/schedule_import/test_validation.py",
    "tests/schedule_import/test_reconcile.py", "tests/schedule_import/test_upload_guard.py",
    # pure logic
    "backend/routers/test_matching_semantic.py", "backend/shared/test_llm_extraction.py",
    "backend/shared/test_schedule.py", "backend/shared/test_tabular_extraction.py",
    "backend/shared/test_xer_parser.py", "tests/test_phase12_auth_jwks.py",
    "tests/test_phase5_execution_state.py", "tests/test_phase6_completed_exclusion.py",
    "tests/test_phase6_matching_eligibility.py", "tests/test_phase7_rework_matching.py",
    "tests/test_priority1_audit_chain_tamper.py", "tests/test_v6_m4_algorithms.py",
    "tests/test_v6_rule_fallback.py", "tests/test_v6_wbs_split.py",
    # in-memory SQLite
    "tests/test_activities_api.py", "tests/test_phase10_canonical_data.py",
    "tests/test_phase1_foundation.py", "tests/test_phase1a_approved_actuals.py",
    "tests/test_phase2_a1_core.py", "tests/test_phase2_a1_propagation.py",
    "tests/test_phase2_export.py", "tests/test_phase3_auto_export.py",
    "tests/test_phase3_dashboard_history.py", "tests/test_phase4_dashboard.py",
    "tests/test_phase5_institutional_memory.py", "tests/test_phase5_knowledge_graph.py",
    "tests/test_phase6_activity_history.py", "tests/test_phase6_ask_why.py",
    "tests/test_phase7_forecast.py", "tests/test_phase9_p6_adapter.py",
    "tests/test_db_test_guard.py", "tests/test_canonical_dataset_definition.py", "tests/test_local_demo_auth.py", "tests/test_schedule_export_roundtrip.py","tests/test_route_context_contract.py", "tests/test_audit_chain_verification.py",
    "tests/test_match_signals.py", "tests/test_execution_history.py",
}
DB_READ = {
    "backend/shared/test_schedule_repository.py", "backend/smoke_test.py",
    "tests/test_integration_seed_integrity.py",
}
DB_WRITE = {
    # clean-schema (db/migrations) suite: runs only against the isolated setuai_v2_integ database; each test rolls back
    "tests/db_v2/test_schema_and_constraints.py", "tests/db_v2/test_ledger_and_progress.py",
    "tests/db_v2/test_roles_and_authorization.py", "tests/db_v2/test_versioning.py", "tests/db_v2/test_rls.py",
    "tests/db_v2/test_runner_and_isolation.py", "tests/db_v2/test_import_staging_migrations.py",
    "tests/db_v2/test_profile_sync.py", "tests/db_v2/test_pool.py", "tests/db_v2/test_posture.py", "tests/db_v2/test_hosted_stamping.py",
    # schedule import service + v2 API against the isolated setuai_v2_integ database (commits, then truncates after each test)
    "tests/schedule_import/test_api_projects_members.py", "tests/schedule_import/test_api_schedule_flow.py",
    "tests/schedule_import/test_api_authorization.py", "tests/schedule_import/test_api_revision_reconciliation.py",
    "tests/schedule_import/test_api_scale.py", "tests/schedule_import/test_api_auth_hardening.py",
    "backend/routers/test_schedules.py", "backend/shared/test_schedule_index.py",
    "backend/shared/test_schedule_repository_integration.py", "backend/test_m2_intake.py",
    "backend/test_p0_stabilization.py",
    "tests/test_phase10_compound_impact.py",
    "tests/test_phase10_quality_itp_hold_points.py",  # unscoped DELETEs fixed to be project-scoped
    "tests/test_phase11_memory_retrieval.py", "tests/test_phase12_integration.py",
    "tests/test_phase12_supervising_agent.py", "tests/test_phase13_audit_dossier.py",
    "tests/test_phase1b_auth_rbac.py", "tests/test_phase1c_schedule_prereqs.py",
    "tests/test_phase2_db_reconstruction.py", "tests/test_phase3_auth_rbac.py",
    "tests/test_phase3_isolation.py", "tests/test_phase4_project.py",
    "tests/test_phase4_schedule_versions.py", "tests/test_phase5_stage_domain.py",
    "tests/test_phase5_stage_isolation.py", "tests/test_phase5_stage_state.py",
    "tests/test_phase6_matching_isolation.py", "tests/test_phase6_stage_scoping.py",
    "tests/test_phase7_actual_revisions.py", "tests/test_phase7_isolation_concurrency.py",
    "tests/test_phase7_reopen_workflow.py", "tests/test_phase7_summary_translation.py",
    "tests/test_phase8_impact_preview.py", "tests/test_phase8_progress_engine.py",
    "tests/test_phase9_xer_contractor_work_package.py", "tests/test_priority1_cross_schedule_isolation.py",
    "tests/test_priority1_decision_atomicity.py", "tests/test_priority1_supabase_rls_and_fks.py",
    "tests/test_v7_rls_security_hardening.py",
    "tests/test_integration_rls_and_audit_chain.py",
    "tests/test_integration_route_security.py",
    "tests/test_integration_revision_history.py",
    "tests/test_integration_intake_context.py",
    "tests/test_integration_workflow_state.py",
    "tests/test_integration_audit_verifier.py", "tests/test_integration_canonical_dataset.py",
    "tests/test_integration_prototype_dataset.py", "tests/test_integration_batch_intake.py",
    "tests/test_integration_execution_history.py",
}
DB_DESTRUCTIVE: set = set()
INTEGRATION = {
    "tests/test_phase12_integration.py", "tests/test_priority1_cross_schedule_isolation.py",
    "tests/test_phase3_isolation.py", "tests/test_phase5_stage_isolation.py",
    "tests/test_phase7_isolation_concurrency.py", "tests/test_v7_rls_security_hardening.py",
    "tests/test_priority1_supabase_rls_and_fks.py", "tests/test_phase6_matching_isolation.py",
    "tests/test_phase6_stage_scoping.py", "tests/test_integration_rls_and_audit_chain.py",
    "tests/test_integration_route_security.py", "tests/test_integration_revision_history.py",
    "tests/test_integration_seed_integrity.py", "tests/test_integration_canonical_dataset.py", "tests/test_integration_intake_context.py",
    "tests/test_integration_workflow_state.py", "tests/test_integration_audit_verifier.py",
    "tests/test_integration_prototype_dataset.py", "tests/test_integration_batch_intake.py",
    "tests/test_integration_execution_history.py",
}
E2E: set = set()
# tests that live in a DB_FREE file but do reach Postgres (found by running them without a DB)
OVERRIDES = {
    "tests/test_phase3_dashboard_history.py::test_dashboard_summary_endpoint_rbac": "db_read",
    "tests/test_phase4_dashboard.py::test_supervisor_access_succeeds": "db_read",
}


def classify(rel_path: str, test_name: str = "") -> str:
    key = f"{rel_path}::{test_name}"
    if key in OVERRIDES:
        return OVERRIDES[key]
    if rel_path in DB_DESTRUCTIVE:
        return "db_destructive"
    if rel_path in DB_WRITE:
        return "db_write"
    if rel_path in DB_READ:
        return "db_read"
    if rel_path in DB_FREE:
        return "db_free"
    return "unclassified"
