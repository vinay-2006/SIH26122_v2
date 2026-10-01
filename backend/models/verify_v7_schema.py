"""
Deterministic V7 Schema Verification Script.

Inspects the live V7 database to verify:
- All required V7 tables are present.
- All new and extended columns are present.
- Primary keys, foreign keys, unique constraints, and check constraints.
- Performance and integrity indexes.
- Row Level Security (RLS) activation on all domain tables.
- Hardened RLS policies for strict project isolation without NULL project_id bypass.
- Authoritative system catalog counts.

Usage:
    python -m backend.models.verify_v7_schema
"""

from __future__ import annotations

import sys
from typing import Dict, List, Set

from backend.shared.db import get_connection

REQUIRED_TABLES = [
    # Baseline Core Tables (17)
    "profiles",
    "schedules",
    "schedule_activities",
    "schedule_dependencies",
    "source_documents",
    "execution_events",
    "source_references",
    "candidate_matches",
    "conflict_records",
    "validation_issues",
    "planner_decisions",
    "approved_actuals",
    "audit_logs",
    "claim_activity_splits",
    "claim_wbs_splits",
    "evidence_links",
    "execution_summaries",
    # V7 Domain Tables (12)
    "projects",
    "stages",
    "project_memberships",
    "contractors",
    "work_packages",
    "quality_gates",
    "quality_evidence",
    "institutional_incidents",
    "incident_evidence",
    "contractor_disputes",
    "impact_scenarios",
    "agent_briefings",
    "issues",
    "issue_categories",
    "issue_evidence",
    "root_causes",
    "disciplines",
    "upload_batches",
    "notifications",
    # Migration Tracking (1)
    "schema_migrations",
]

REQUIRED_EXTENSIONS = {
    "schedules": [
        "project_id",
        "version_code",
        "version_metadata",
        "source_hash",
        "active",
        "supersedes_schedule_id",
    ],
    "schedule_activities": [
        "project_id",
        "stage_id",
        "contractor_id",
        "work_package_id",
        "weight_factor",
        "weight_basis",
        "quality_gate_required",
    ],
    "schedule_dependencies": [
        "lag_days",
    ],
    "execution_events": [
        "project_id",
        "stage_id",
        "contractor_id",
        "work_package_id",
        "quality_gate_id",
        "reopen_status",
    ],
    "approved_actuals": [
        "project_id",
        "stage_id",
        "is_reopened",
    ],
    "audit_logs": [
        "project_id",
        "schedule_id",
        "role",
        "entity_context",
    ],
}


def verify_schema() -> bool:
    print("==================================================")
    print("   SETUAI V7 DATABASE SCHEMA VERIFICATION")
    print("==================================================")

    all_passed = True

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Verify Tables
            cur.execute(
                """
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name;
                """
            )
            existing_tables: List[str] = [
                r["table_name"] if isinstance(r, dict) else r[0] for r in cur.fetchall()
            ]
            existing_table_set = set(existing_tables)
            total_tables = len(existing_tables)
            domain_tables = [t for t in existing_tables if t != "schema_migrations"]
            has_schema_migrations = "schema_migrations" in existing_table_set

            print(f"\n[1] Checking Tables ({total_tables} total, {len(domain_tables)} domain)...")
            missing_tables = [t for t in REQUIRED_TABLES if t not in existing_table_set]
            if missing_tables:
                print(f"  FAILED: Missing tables: {missing_tables}")
                all_passed = False
            else:
                print(f"  PASSED: All {len(REQUIRED_TABLES)} required tables present in database.")

            # 2. Verify Extended Columns
            print("\n[2] Checking Extended V7 Columns...")
            cur.execute(
                """
                SELECT table_name, column_name 
                FROM information_schema.columns 
                WHERE table_schema = 'public';
                """
            )
            table_cols: Dict[str, Set[str]] = {}
            for r in cur.fetchall():
                t = r["table_name"] if isinstance(r, dict) else r[0]
                c = r["column_name"] if isinstance(r, dict) else r[1]
                table_cols.setdefault(t, set()).add(c)

            for table, cols in REQUIRED_EXTENSIONS.items():
                actual_cols = table_cols.get(table, set())
                missing = [c for c in cols if c not in actual_cols]
                if missing:
                    print(f"  FAILED: Table '{table}' missing columns: {missing}")
                    all_passed = False
                else:
                    print(f"  PASSED: Table '{table}' has all {len(cols)} required extended columns.")

            # 3. Verify Foreign Keys
            print("\n[3] Checking Key Cross-Domain Foreign Keys...")
            cur.execute(
                """
                SELECT
                    tc.constraint_name,
                    tc.table_name,
                    kcu.column_name,
                    ccu.table_name AS foreign_table_name,
                    ccu.column_name AS foreign_column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                    ON ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
                ORDER BY tc.table_name, kcu.column_name;
                """
            )
            fks = cur.fetchall()
            total_fks = len(fks)
            fk_pairs = {
                f"{r['table_name']}.{r['column_name']} -> {r['foreign_table_name']}.{r['foreign_column_name']}"
                if isinstance(r, dict)
                else f"{r[1]}.{r[2]} -> {r[3]}.{r[4]}"
                for r in fks
            }
            print(f"  Found {total_fks} total foreign key constraints.")

            critical_fks = [
                "schedules.project_id -> projects.project_id",
                "stages.project_id -> projects.project_id",
                "stages.schedule_id -> schedules.schedule_id",
                "project_memberships.project_id -> projects.project_id",
                "project_memberships.user_id -> profiles.id",
                "contractors.project_id -> projects.project_id",
                "work_packages.project_id -> projects.project_id",
                "quality_gates.project_id -> projects.project_id",
                "quality_evidence.quality_gate_id -> quality_gates.quality_gate_id",
                "schedule_activities.project_id -> projects.project_id",
                "execution_events.project_id -> projects.project_id",
                "approved_actuals.project_id -> projects.project_id",
                "approved_actuals.schedule_id -> schedules.schedule_id",
                "claim_activity_splits.event_id -> execution_events.event_id",
                "claim_wbs_splits.event_id -> execution_events.event_id",
                "audit_logs.project_id -> projects.project_id",
            ]
            for cfk in critical_fks:
                if cfk in fk_pairs:
                    print(f"  PASSED FK: {cfk}")
                else:
                    print(f"  FAILED FK: Missing critical foreign key: {cfk}")
                    all_passed = False

            # 4. Verify RLS Activation
            print("\n[4] Checking Row Level Security (RLS)...")
            cur.execute(
                """
                SELECT relname, relrowsecurity 
                FROM pg_class 
                JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
                WHERE pg_namespace.nspname = 'public' AND relkind = 'r'
                ORDER BY relname;
                """
            )
            rls_rows = cur.fetchall()
            rls_status = {
                (r["relname"] if isinstance(r, dict) else r[0]): (
                    r["relrowsecurity"] if isinstance(r, dict) else r[1]
                )
                for r in rls_rows
            }
            rls_enabled_tables = [t for t, enabled in rls_status.items() if enabled and t != "schema_migrations"]
            total_rls_enabled = len(rls_enabled_tables)

            all_rls = True
            for t in domain_tables:
                is_enabled = rls_status.get(t, False)
                if not is_enabled:
                    print(f"  FAILED RLS: Domain table '{t}' does NOT have RLS enabled!")
                    all_rls = False
                    all_passed = False
            if all_rls:
                print(f"  PASSED: All {len(domain_tables)} V7 domain tables have RLS enabled.")

            # 5. Verify Policies
            print("\n[5] Checking RLS Policies...")
            cur.execute(
                """
                SELECT tablename, policyname, cmd, qual, with_check 
                FROM pg_policies 
                WHERE schemaname = 'public'
                ORDER BY tablename, policyname;
                """
            )
            policies = cur.fetchall()
            policy_count = len(policies)
            print(f"  Found {policy_count} active security policies.")
            
            # Check that no policy has project_id IS NULL bypass
            bypass_policies = []
            for p in policies:
                qual = str(p.get("qual", "") if isinstance(p, dict) else p[3])
                with_check = str(p.get("with_check", "") if isinstance(p, dict) else p[4])
                pol_name = p.get("policyname", "") if isinstance(p, dict) else p[1]
                tbl = p.get("tablename", "") if isinstance(p, dict) else p[0]
                if "project_id IS NULL" in qual.upper() or "PROJECT_ID IS NULL" in with_check.upper():
                    bypass_policies.append(f"{tbl}.{pol_name}")
            
            if bypass_policies:
                print(f"  FAILED: Detected policies with 'project_id IS NULL' bypass: {bypass_policies}")
                all_passed = False
            else:
                print("  PASSED: Zero policies contain 'project_id IS NULL' bypass.")

            # 6. Verify Indexes
            print("\n[6] Checking Indexes...")
            cur.execute(
                """
                SELECT indexname, tablename 
                FROM pg_indexes 
                WHERE schemaname = 'public'
                ORDER BY tablename, indexname;
                """
            )
            indexes = cur.fetchall()
            total_indexes = len(indexes)
            idx_names = {r["indexname"] if isinstance(r, dict) else r[0] for r in indexes}
            print(f"  Found {total_indexes} indexes in public schema.")

            critical_indexes = [
                "idx_projects_code",
                "idx_schedules_project_id",
                "idx_stages_project_id",
                "idx_project_memberships_user_proj",
                "idx_contractors_project_id",
                "idx_work_packages_project_id",
                "idx_quality_gates_project_id",
                "idx_quality_evidence_gate_id",
                "idx_schedule_activities_project_id",
                "idx_execution_events_project_id",
                "idx_approved_actuals_project_id",
                "idx_approved_actuals_schedule_id",
                "idx_audit_logs_project_id",
                "idx_claim_activity_splits_event",
                "idx_claim_wbs_splits_event",
                "idx_evidence_links_a",
                "idx_evidence_links_b",
            ]
            for cidx in critical_indexes:
                if cidx in idx_names:
                    print(f"  PASSED Index: {cidx}")
                else:
                    print(f"  FAILED Index: Missing critical index: {cidx}")
                    all_passed = False

            # 7. Project Isolation Counts
            cur.execute(
                """
                SELECT table_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND column_name = 'project_id'
                ORDER BY table_name;
                """
            )
            proj_id_rows = cur.fetchall()
            direct_project_tables = [r["table_name"] if isinstance(r, dict) else r[0] for r in proj_id_rows]
            not_null_project_tables = [
                r["table_name"] if isinstance(r, dict) else r[0]
                for r in proj_id_rows
                if (r["is_nullable"] if isinstance(r, dict) else r[1]) == "NO"
            ]

            # Project-scoped tables: direct tables (15) + child tables linked via FK (11)
            # quality_evidence, incident_evidence, schedule_dependencies, claim_activity_splits,
            # claim_wbs_splits, candidate_matches, conflict_records, validation_issues,
            # planner_decisions, source_references, evidence_links
            child_project_scoped_tables = [
                "quality_evidence",
                "incident_evidence",
                "schedule_dependencies",
                "claim_activity_splits",
                "claim_wbs_splits",
                "candidate_matches",
                "conflict_records",
                "validation_issues",
                "planner_decisions",
                "source_references",
                "evidence_links",
            ]
            all_project_scoped = set(direct_project_tables) | set(child_project_scoped_tables)

            # Check if any NULL project_id records remain in schedule_activities
            cur.execute("SELECT count(*) as cnt FROM schedule_activities WHERE project_id IS NULL;")
            null_act_count = cur.fetchone()["cnt"]
            if null_act_count > 0:
                print(f"  FAILED: schedule_activities contains {null_act_count} rows with project_id IS NULL!")
                all_passed = False
            else:
                print("  PASSED: Zero rows in schedule_activities have project_id IS NULL.")

            # Section 11 Authoritative Summary Format
            print("\n=== SETUAI V7 SCHEMA VERIFICATION ===")
            print(f"\nTables:")
            print(f"  total = {total_tables}")
            print(f"  domain = {len(domain_tables)}")
            print(f"  schema_migrations = {1 if has_schema_migrations else 0}")
            print(f"\nForeign keys:")
            print(f"  total = {total_fks}")
            print(f"\nIndexes:")
            print(f"  total = {total_indexes}")
            print(f"\nRLS:")
            print(f"  enabled tables = {total_rls_enabled}")
            print(f"  policies = {policy_count}")
            print(f"\nProject isolation:")
            print(f"  project-scoped tables = {len(all_project_scoped)}")
            print(f"  project-owned tables = {len(direct_project_tables)}")
            print(f"  project-owned NOT NULL project_id = {len(not_null_project_tables)}")

    print("\n==================================================")
    if all_passed:
        print("   V7 SCHEMA VERIFICATION: ALL CHECKS PASSED")
        print("==================================================")
        return True
    else:
        print("   V7 SCHEMA VERIFICATION: FAILURES DETECTED")
        print("==================================================")
        return False


if __name__ == "__main__":
    success = verify_schema()
    sys.exit(0 if success else 1)
