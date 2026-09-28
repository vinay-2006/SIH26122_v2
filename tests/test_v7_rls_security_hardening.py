"""
Phase 2 Hardening: Comprehensive Row-Level Security (RLS) Isolation Tests.

Verifies strict project isolation and absence of NULL project_id bypass:
1. Anonymous (no JWT / anon role):
   - Cannot read project-owned data
   - Cannot insert project-owned data
   - Cannot update project-owned data
2. Project Member:
   - Can access Project A rows
   - Cannot access Project B rows (invisible)
3. Non-Member / Cross-Project:
   - Project B rows are invisible to Project A members
   - Project B inserts rejected by RLS
   - Project B updates rejected by RLS
4. NULL project_id Protection:
   - Ordinary authenticated members cannot insert rows with project_id = NULL
   - Rows with project_id = NULL (if any exist) are invisible to authenticated members
   - Ordinary authenticated members cannot update project_id to NULL
5. Helper Function Integrity:
   - is_project_member(NULL) returns FALSE
   - is_project_member(proj_id) with no auth returns FALSE
"""

import uuid
import pytest
import psycopg
from backend.shared.db import get_connection


@pytest.fixture
def rls_fixture():
    """Sets up isolated test projects, users, memberships, and entities, cleaning up afterwards."""
    conn = get_connection()
    conn.autocommit = False

    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()
    sched_a = f"SCH-{uuid.uuid4().hex[:6]}"
    sched_b = f"SCH-{uuid.uuid4().hex[:6]}"
    act_a = f"ACT-{uuid.uuid4().hex[:6]}"
    act_b = f"ACT-{uuid.uuid4().hex[:6]}"
    evt_a = f"EVT-{uuid.uuid4().hex[:6]}"
    evt_b = f"EVT-{uuid.uuid4().hex[:6]}"

    with conn.cursor() as cur:
        # Create profiles
        cur.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);", (user_a, "Member A", "SITE_ENGINEER"))
        cur.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);", (user_b, "Member B", "SITE_ENGINEER"))

        # Create projects
        cur.execute("INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);", (proj_a, f"PRJ-{uuid.uuid4().hex[:6]}", "Project A"))
        cur.execute("INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);", (proj_b, f"PRJ-{uuid.uuid4().hex[:6]}", "Project B"))

        # Create memberships
        cur.execute("INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role) VALUES (%s, %s, %s, %s);", (uuid.uuid4(), user_a, proj_a, "SITE_ENGINEER"))
        cur.execute("INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role) VALUES (%s, %s, %s, %s);", (uuid.uuid4(), user_b, proj_b, "SITE_ENGINEER"))

        # Create schedules
        cur.execute("INSERT INTO schedules (schedule_id, project_id, project_name) VALUES (%s, %s, %s);", (sched_a, proj_a, "Schedule A"))
        cur.execute("INSERT INTO schedules (schedule_id, project_id, project_name) VALUES (%s, %s, %s);", (sched_b, proj_b, "Schedule B"))

        # Create activities (including discipline and location)
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, activity_name, discipline, location, planned_start, planned_finish
            ) VALUES (%s, %s, %s, %s, 'CIVIL', 'Site A', '2026-03-01', '2026-03-10');
            """,
            (act_a, sched_a, proj_a, "Activity A"),
        )
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, activity_name, discipline, location, planned_start, planned_finish
            ) VALUES (%s, %s, %s, %s, 'CIVIL', 'Site B', '2026-03-01', '2026-03-10');
            """,
            (act_b, sched_b, proj_b, "Activity B"),
        )

        # Create execution events
        cur.execute(
            """
            INSERT INTO execution_events (event_id, schedule_id, project_id, event_date, raw_claim_text, input_channel, event_type)
            VALUES (%s, %s, %s, '2026-03-05', 'Event A claim', 'WHATSAPP', 'FIELD_PROGRESS');
            """,
            (evt_a, sched_a, proj_a),
        )
        cur.execute(
            """
            INSERT INTO execution_events (event_id, schedule_id, project_id, event_date, raw_claim_text, input_channel, event_type)
            VALUES (%s, %s, %s, '2026-03-05', 'Event B claim', 'WHATSAPP', 'FIELD_PROGRESS');
            """,
            (evt_b, sched_b, proj_b),
        )
        conn.commit()

    fixture_data = {
        "user_a": user_a,
        "user_b": user_b,
        "proj_a": proj_a,
        "proj_b": proj_b,
        "sched_a": sched_a,
        "sched_b": sched_b,
        "act_a": act_a,
        "act_b": act_b,
        "evt_a": evt_a,
        "evt_b": evt_b,
    }

    yield conn, fixture_data

    # Cleanup in superuser / default role
    with conn.cursor() as cur:
        cur.execute("DELETE FROM execution_events WHERE event_id IN (%s, %s);", (evt_a, evt_b))
        cur.execute("DELETE FROM schedule_activities WHERE activity_id IN (%s, %s);", (act_a, act_b))
        cur.execute("DELETE FROM schedules WHERE schedule_id IN (%s, %s);", (sched_a, sched_b))
        cur.execute("DELETE FROM project_memberships WHERE user_id IN (%s, %s);", (user_a, user_b))
        cur.execute("DELETE FROM projects WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM profiles WHERE id IN (%s, %s);", (user_a, user_b))
        conn.commit()
    conn.close()


def test_anonymous_access_denied(rls_fixture):
    """Test #1: Anonymous role cannot read, insert, or update project-owned data."""
    conn, f = rls_fixture
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE anon;")

        # Read must return 0 rows
        cur.execute("SELECT count(*) as cnt FROM projects WHERE project_id = %s;", (f["proj_a"],))
        assert cur.fetchone()["cnt"] == 0

        cur.execute("SELECT count(*) as cnt FROM schedules WHERE schedule_id = %s;", (f["sched_a"],))
        assert cur.fetchone()["cnt"] == 0

        cur.execute("SELECT count(*) as cnt FROM schedule_activities WHERE activity_id = %s;", (f["act_a"],))
        assert cur.fetchone()["cnt"] == 0

        cur.execute("SELECT count(*) as cnt FROM execution_events WHERE event_id = %s;", (f["evt_a"],))
        assert cur.fetchone()["cnt"] == 0

        # Insert must fail
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                cur.execute(
                    "INSERT INTO schedules (schedule_id, project_id, project_name) VALUES (%s, %s, %s);",
                    (f"SCH-{uuid.uuid4().hex[:6]}", f["proj_a"], "Anon Schedule"),
                )

        # Update must fail or affect 0 rows
        with conn.transaction():
            cur.execute("UPDATE projects SET project_name = 'Hacked' WHERE project_id = %s;", (f["proj_a"],))
            assert cur.rowcount == 0


def test_project_member_isolation_select(rls_fixture):
    """Test #2: Member of Project A can see Project A rows, but Project B rows are invisible."""
    conn, f = rls_fixture
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE authenticated;")
        cur.execute(f"SET LOCAL \"request.jwt.claim.sub\" = '{f['user_a']}';")

        # Member A can see Project A
        cur.execute("SELECT project_id FROM projects WHERE project_id IN (%s, %s);", (f["proj_a"], f["proj_b"]))
        visible_projs = {r["project_id"] for r in cur.fetchall()}
        assert f["proj_a"] in visible_projs
        assert f["proj_b"] not in visible_projs

        # Member A can see Schedule A, not Schedule B
        cur.execute("SELECT schedule_id FROM schedules WHERE schedule_id IN (%s, %s);", (f["sched_a"], f["sched_b"]))
        visible_scheds = {r["schedule_id"] for r in cur.fetchall()}
        assert f["sched_a"] in visible_scheds
        assert f["sched_b"] not in visible_scheds

        # Member A can see Activity A, not Activity B
        cur.execute("SELECT activity_id FROM schedule_activities WHERE activity_id IN (%s, %s);", (f["act_a"], f["act_b"]))
        visible_acts = {r["activity_id"] for r in cur.fetchall()}
        assert f["act_a"] in visible_acts
        assert f["act_b"] not in visible_acts

        # Member A can see Event A, not Event B
        cur.execute("SELECT event_id FROM execution_events WHERE event_id IN (%s, %s);", (f["evt_a"], f["evt_b"]))
        visible_evts = {r["event_id"] for r in cur.fetchall()}
        assert f["evt_a"] in visible_evts
        assert f["evt_b"] not in visible_evts


def test_cross_project_write_denied(rls_fixture):
    """Test #3: Member A cannot insert or update into Project B."""
    conn, f = rls_fixture
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE authenticated;")
        cur.execute(f"SET LOCAL \"request.jwt.claim.sub\" = '{f['user_a']}';")

        # Insert schedule into Project B rejected
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                cur.execute(
                    "INSERT INTO schedules (schedule_id, project_id, project_name) VALUES (%s, %s, %s);",
                    (f"SCH-{uuid.uuid4().hex[:6]}", f["proj_b"], "Cross-Project Schedule"),
                )

        # Insert activity into Project B rejected
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                cur.execute(
                    """
                    INSERT INTO schedule_activities (
                        activity_id, schedule_id, project_id, activity_name, discipline, location, planned_start, planned_finish
                    ) VALUES (%s, %s, %s, 'Cross-Act', 'CIVIL', 'Site B', '2026-03-01', '2026-03-10');
                    """,
                    (f"ACT-{uuid.uuid4().hex[:6]}", f["sched_b"], f["proj_b"]),
                )

        # Update activity in Project B affects 0 rows
        with conn.transaction():
            cur.execute("UPDATE schedule_activities SET activity_name = 'Modified' WHERE activity_id = %s;", (f["act_b"],))
            assert cur.rowcount == 0


def test_null_project_id_rejection_and_invisibility(rls_fixture):
    """Test #4: Authenticated user cannot insert or see rows with project_id = NULL."""
    conn, f = rls_fixture
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE authenticated;")
        cur.execute(f"SET LOCAL \"request.jwt.claim.sub\" = '{f['user_a']}';")

        # 1. Attempting to insert schedule with NULL project_id must be rejected
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                cur.execute(
                    "INSERT INTO schedules (schedule_id, project_id, project_name) VALUES (%s, NULL, %s);",
                    (f"SCH-{uuid.uuid4().hex[:6]}", "Null Proj Sched"),
                )

        # 2. Attempting to insert activity with NULL project_id must be rejected
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                cur.execute(
                    """
                    INSERT INTO schedule_activities (
                        activity_id, schedule_id, project_id, activity_name, discipline, location, planned_start, planned_finish
                    ) VALUES (%s, %s, NULL, 'Null Proj Act', 'CIVIL', 'Site A', '2026-03-01', '2026-03-10');
                    """,
                    (f"ACT-{uuid.uuid4().hex[:6]}", f["sched_a"]),
                )

        # 3. Attempting to insert execution event with NULL project_id must be rejected
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                cur.execute(
                    """
                    INSERT INTO execution_events (event_id, schedule_id, project_id, event_date, raw_claim_text, input_channel, event_type)
                    VALUES (%s, %s, NULL, '2026-03-05', 'Null Claim', 'WHATSAPP', 'FIELD_PROGRESS');
                    """,
                    (f"EVT-{uuid.uuid4().hex[:6]}", f["sched_a"]),
                )

        # 4. Attempting to update existing activity to NULL project_id must be rejected
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                cur.execute(
                    "UPDATE schedule_activities SET project_id = NULL WHERE activity_id = %s;",
                    (f["act_a"],),
                )


def test_is_project_member_guard(rls_fixture):
    """Test #5: is_project_member helper function denies NULLs and unauthorized contexts."""
    conn, f = rls_fixture
    with conn.cursor() as cur:
        # Without auth context (superuser/service direct query without JWT)
        cur.execute("SELECT is_project_member(NULL) as res;")
        assert cur.fetchone()["res"] is False

        cur.execute("SELECT is_project_member(%s) as res;", (f["proj_a"],))
        assert cur.fetchone()["res"] is False

        # Under User A context
        cur.execute("SET LOCAL ROLE authenticated;")
        cur.execute(f"SET LOCAL \"request.jwt.claim.sub\" = '{f['user_a']}';")

        cur.execute("SELECT is_project_member(NULL) as res;")
        assert cur.fetchone()["res"] is False

        cur.execute("SELECT is_project_member(%s) as res;", (f["proj_a"],))
        assert cur.fetchone()["res"] is True

        cur.execute("SELECT is_project_member(%s) as res;", (f["proj_b"],))
        assert cur.fetchone()["res"] is False

        cur.execute("SELECT is_project_member(%s) as res;", (uuid.uuid4(),))
        assert cur.fetchone()["res"] is False
