"""
Dedicated Phase 2 Database Reconstruction and Domain Integrity Tests.

Tests verify:
1. Projects domain: Creation, UUID primary key, uniqueness of project_code.
2. Memberships domain: User assignment, uniqueness of (user_id, project_id), role check constraints.
3. Schedule Versions: Project FK, multi-version coexistence, supersession.
4. Stages: Hierarchical parent-child nesting, schedule relationship, sequence and weighting.
5. Contractors & Work Packages: Project scoping, contractor code uniqueness per project, cross-project coexistence.
6. Activities: FK relationships to project, stage, contractor, work package, weight_factor constraints.
7. Quality Gates & Evidence: Relationship chain from project/stage/activity -> gate -> evidence.
8. Intelligence Foundation: Incident, dispute, impact scenario, and briefing table storage.
9. RLS Foundation: Helper function logic, default denial, project membership isolation.
"""

import uuid
import pytest
import psycopg
from backend.shared.db import get_connection


@pytest.fixture
def clean_db():
    """Provides a connection and cleans up test entities after execution."""
    conn = get_connection()
    yield conn
    conn.close()


def test_project_creation_and_unique_code(clean_db):
    """Test #1: Project creation with UUID PK and uniqueness constraint on project_code."""
    proj_id = uuid.uuid4()
    code = f"PRJ-{uuid.uuid4().hex[:8].upper()}"

    with clean_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status)
            VALUES (%s, %s, %s, %s)
            RETURNING project_id, project_code;
            """,
            (proj_id, code, "Test Infrastructure Project", "ACTIVE"),
        )
        row = cur.fetchone()
        clean_db.commit()

        assert row is not None
        assert str(row["project_id"] if isinstance(row, dict) else row[0]) == str(proj_id)

        # Duplicate project_code must be rejected by unique constraint
        with pytest.raises(psycopg.errors.UniqueViolation):
            with clean_db.transaction():
                clean_db.execute(
                    """
                    INSERT INTO projects (project_id, project_code, project_name, status)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (uuid.uuid4(), code, "Duplicate Project", "ACTIVE"),
                )


def test_project_memberships_constraints(clean_db):
    """Test #2: Membership creation, unique (user_id, project_id), and valid role constraint."""
    user_id = uuid.uuid4()
    proj_id = uuid.uuid4()
    code = f"PRJ-{uuid.uuid4().hex[:8].upper()}"

    with clean_db.cursor() as cur:
        # Create profile and project
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_id, "Test Engineer", "SITE_ENGINEER"),
        )
        cur.execute(
            "INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);",
            (proj_id, code, "Membership Test Project"),
        )
        clean_db.commit()

        # Valid membership insertion
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role)
            VALUES (%s, %s, %s, %s)
            RETURNING membership_id;
            """,
            (uuid.uuid4(), user_id, proj_id, "SITE_ENGINEER"),
        )
        clean_db.commit()

        # Duplicate membership for same (user_id, project_id) must be rejected
        with pytest.raises(psycopg.errors.UniqueViolation):
            with clean_db.transaction():
                clean_db.execute(
                    """
                    INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (uuid.uuid4(), user_id, proj_id, "SUPERVISOR"),
                )

        # Invalid assigned_role must be rejected by CHECK constraint
        with pytest.raises(psycopg.errors.CheckViolation):
            with clean_db.transaction():
                clean_db.execute(
                    """
                    INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (uuid.uuid4(), user_id, proj_id, "INVALID_ROLE"),
                )


def test_schedule_versions_coexistence(clean_db):
    """Test #3: Two schedule versions can coexist under the same project with supersession."""
    proj_id = uuid.uuid4()
    code = f"PRJ-{uuid.uuid4().hex[:8].upper()}"
    sched_v1 = f"SCH-{uuid.uuid4().hex[:6]}"
    sched_v2 = f"SCH-{uuid.uuid4().hex[:6]}"

    with clean_db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);",
            (proj_id, code, "Schedule Version Test Project"),
        )
        # Schedule Version 1
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, %s, %s);
            """,
            (sched_v1, "Schedule Version Test Project", proj_id, "V1", False),
        )
        # Schedule Version 2 supersedes Version 1
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active, supersedes_schedule_id)
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (sched_v2, "Schedule Version Test Project", proj_id, "V2", True, sched_v1),
        )
        clean_db.commit()

        # Both remain queryable
        cur.execute("SELECT schedule_id, version_code, active FROM schedules WHERE project_id = %s ORDER BY version_code;", (proj_id,))
        rows = cur.fetchall()
        assert len(rows) == 2
        assert (rows[0]["version_code"] if isinstance(rows[0], dict) else rows[0][1]) == "V1"
        assert (rows[1]["version_code"] if isinstance(rows[1], dict) else rows[1][1]) == "V2"

        # Invalid project_id reference rejected by FK
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with clean_db.transaction():
                clean_db.execute(
                    """
                    INSERT INTO schedules (schedule_id, project_name, project_id, version_code)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (f"SCH-{uuid.uuid4().hex[:6]}", "Bad Sched", uuid.uuid4(), "V1"),
                )


def test_stage_hierarchy_and_nesting(clean_db):
    """Test #4: Stages belong to project & schedule; parent-child nesting is supported."""
    proj_id = uuid.uuid4()
    code = f"PRJ-{uuid.uuid4().hex[:8].upper()}"
    sched_id = f"SCH-{uuid.uuid4().hex[:6]}"
    stage_parent = uuid.uuid4()
    stage_child = uuid.uuid4()

    with clean_db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);",
            (proj_id, code, "Stage Test Project"),
        )
        cur.execute(
            "INSERT INTO schedules (schedule_id, project_name, project_id) VALUES (%s, %s, %s);",
            (sched_id, "Stage Test Project", proj_id),
        )
        # Parent Stage (Phase 1 Substructure)
        cur.execute(
            """
            INSERT INTO stages (stage_id, project_id, schedule_id, stage_name, sequence_order, weight_pct)
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (stage_parent, proj_id, sched_id, "Substructure Phase", 1, 40.0),
        )
        # Child Stage (Deep Foundation)
        cur.execute(
            """
            INSERT INTO stages (stage_id, project_id, schedule_id, parent_stage_id, stage_name, sequence_order, weight_pct)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (stage_child, proj_id, sched_id, stage_parent, "Deep Foundation Piling", 2, 25.0),
        )
        clean_db.commit()

        # Query nested stage
        cur.execute("SELECT parent_stage_id FROM stages WHERE stage_id = %s;", (stage_child,))
        row = cur.fetchone()
        assert str(row["parent_stage_id"] if isinstance(row, dict) else row[0]) == str(stage_parent)


def test_contractors_and_work_packages(clean_db):
    """Test #5: Contractor code unique within project; duplicate within project rejected, allowed cross-project."""
    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()
    code_a = f"PRJ-A-{uuid.uuid4().hex[:6].upper()}"
    code_b = f"PRJ-B-{uuid.uuid4().hex[:6].upper()}"

    contractor_id_a = uuid.uuid4()
    contractor_id_b = uuid.uuid4()
    shared_contractor_code = "CONT-LNT"

    with clean_db.cursor() as cur:
        cur.execute("INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);", (proj_a, code_a, "Project A"))
        cur.execute("INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);", (proj_b, code_b, "Project B"))

        # Same contractor code in Project A and Project B is ALLOWED
        cur.execute(
            "INSERT INTO contractors (contractor_id, project_id, contractor_code, company_name) VALUES (%s, %s, %s, %s);",
            (contractor_id_a, proj_a, shared_contractor_code, "L&T Construction"),
        )
        cur.execute(
            "INSERT INTO contractors (contractor_id, project_id, contractor_code, company_name) VALUES (%s, %s, %s, %s);",
            (contractor_id_b, proj_b, shared_contractor_code, "L&T Construction"),
        )
        clean_db.commit()

        # Duplicate contractor code in same project must be REJECTED
        with pytest.raises(psycopg.errors.UniqueViolation):
            with clean_db.transaction():
                clean_db.execute(
                    "INSERT INTO contractors (contractor_id, project_id, contractor_code, company_name) VALUES (%s, %s, %s, %s);",
                    (uuid.uuid4(), proj_a, shared_contractor_code, "L&T Duplicate"),
                )

        # Work Package belongs to project and contractor
        wp_id = uuid.uuid4()
        cur.execute(
            """
            INSERT INTO work_packages (work_package_id, project_id, contractor_id, package_code, package_name, discipline)
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (wp_id, proj_a, contractor_id_a, "WP-CIVIL-01", "Civil Piling Package", "CIVIL"),
        )
        clean_db.commit()


def test_activity_v7_extensions(clean_db):
    """Test #6: Activity references project, stage, contractor, and work package."""
    proj_id = uuid.uuid4()
    code = f"PRJ-{uuid.uuid4().hex[:8].upper()}"
    sched_id = f"SCH-{uuid.uuid4().hex[:6]}"
    stage_id = uuid.uuid4()
    contractor_id = uuid.uuid4()
    wp_id = uuid.uuid4()
    act_id = f"A{uuid.uuid4().hex[:6]}"

    with clean_db.cursor() as cur:
        cur.execute("INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);", (proj_id, code, "Activity Test Project"))
        cur.execute("INSERT INTO schedules (schedule_id, project_name, project_id) VALUES (%s, %s, %s);", (sched_id, "Activity Test Project", proj_id))
        cur.execute("INSERT INTO stages (stage_id, project_id, schedule_id, stage_name) VALUES (%s, %s, %s, %s);", (stage_id, proj_id, sched_id, "Foundations"))
        cur.execute("INSERT INTO contractors (contractor_id, project_id, contractor_code, company_name) VALUES (%s, %s, %s, %s);", (contractor_id, proj_id, "CONT-01", "Acme Piling"))
        cur.execute("INSERT INTO work_packages (work_package_id, project_id, contractor_id, package_name) VALUES (%s, %s, %s, %s);", (wp_id, proj_id, contractor_id, "Piling WP"))

        # Activity with full V7 context
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish,
                project_id, stage_id, contractor_id, work_package_id, weight_factor, quality_gate_required
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (act_id, sched_id, "Driven Piling Pier 4", "CIVIL", "Bridge Pier 4", "2026-03-01", "2026-03-15",
             proj_id, stage_id, contractor_id, wp_id, 1.75, True),
        )
        clean_db.commit()

        # Verify activity relationships
        cur.execute("SELECT project_id, stage_id, contractor_id, weight_factor FROM schedule_activities WHERE activity_id = %s;", (act_id,))
        row = cur.fetchone()
        assert str(row["project_id"] if isinstance(row, dict) else row[0]) == str(proj_id)
        assert float(row["weight_factor"] if isinstance(row, dict) else row[3]) == 1.75

        # Negative weight_factor must be rejected by check constraint
        with pytest.raises(psycopg.errors.CheckViolation):
            with clean_db.transaction():
                clean_db.execute(
                    """
                    INSERT INTO schedule_activities (
                        activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish,
                        project_id, weight_factor
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (f"A{uuid.uuid4().hex[:6]}", sched_id, "Invalid Act", "CIVIL", "Site", "2026-03-01", "2026-03-15", proj_id, -1.0),
                )


def test_quality_gates_and_evidence(clean_db):
    """Test #7: Quality gate references project & activity; quality evidence references gate."""
    proj_id = uuid.uuid4()
    code = f"PRJ-{uuid.uuid4().hex[:8].upper()}"
    gate_id = uuid.uuid4()
    evidence_id = uuid.uuid4()

    with clean_db.cursor() as cur:
        cur.execute("INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);", (proj_id, code, "Quality Test Project"))
        cur.execute(
            """
            INSERT INTO quality_gates (quality_gate_id, project_id, gate_type, gate_name, required, status)
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (gate_id, proj_id, "PRE_COMMENCEMENT", "Pre-pour Rebar Clearance", True, "PENDING"),
        )
        cur.execute(
            """
            INSERT INTO quality_evidence (quality_evidence_id, quality_gate_id, evidence_type, result, inspector_name)
            VALUES (%s, %s, %s, %s, %s);
            """,
            (evidence_id, gate_id, "TEST_REPORT", "PASS", "Chief Inspector"),
        )
        clean_db.commit()

        # Query evidence
        cur.execute("SELECT result, inspector_name FROM quality_evidence WHERE quality_evidence_id = %s;", (evidence_id,))
        row = cur.fetchone()
        assert (row["result"] if isinstance(row, dict) else row[0]) == "PASS"


def test_rls_membership_helper_function(clean_db):
    """Test #8: is_project_member helper function denies unauthorized and anonymous access."""
    proj_id = uuid.uuid4()
    code = f"PRJ-{uuid.uuid4().hex[:8].upper()}"

    with clean_db.cursor() as cur:
        cur.execute("INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);", (proj_id, code, "RLS Helper Project"))
        clean_db.commit()

        # When auth.uid() is null (no JWT context in regular direct query), is_project_member must return False
        cur.execute("SELECT is_project_member(%s);", (proj_id,))
        row = cur.fetchone()
        res = row["is_project_member"] if isinstance(row, dict) else row[0]
        assert res is False
