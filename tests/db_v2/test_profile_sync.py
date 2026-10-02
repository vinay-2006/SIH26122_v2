"""Migration 0012: profiles follow auth.users without ever breaking a signup, and deleting a user deactivates instead of blocking."""
import pytest

from v2world import as_actor, build_world, claim, fails, one, sysmode, u


def new_auth_user(conn, email, meta='{}', uid=None):
    uid = uid or u()
    conn.execute("insert into auth.users (id, email, raw_user_meta_data) values (%s,%s,%s::jsonb)", (uid, email, meta))
    return uid


def profile(conn, uid):
    return conn.execute("select * from profiles where id = %s", (uid,)).fetchone()


def test_a_signup_with_an_email_gets_a_profile_with_a_sensible_name(conn):
    a = new_auth_user(conn, "Maya.Rao@example.org", '{"full_name": "  Maya Rao "}')
    b = new_auth_user(conn, "no.name@example.org")
    assert profile(conn, a)["full_name"] == "Maya Rao" and profile(conn, a)["is_active"] is True
    assert profile(conn, b)["full_name"] == "no.name"


@pytest.mark.parametrize("email", [None, "", "no-at-sign", "@x"])
def test_a_signup_without_a_usable_email_is_never_blocked_and_gets_no_profile(conn, email):
    uid = new_auth_user(conn, email)                     # phone / anonymous / malformed: the auth insert itself must succeed
    assert profile(conn, uid) is None


def test_an_email_clash_does_not_abort_the_signup(conn):
    new_auth_user(conn, "dup@example.org")
    other = u()
    conn.execute("savepoint s")
    # auth.users in the shim has its own unique(email); emulate a case-variant that only profiles' lower(email) index catches
    conn.execute("insert into auth.users (id, email) values (%s, %s)", (other, "DUP@example.org"))
    assert profile(conn, other) is None                  # warned and skipped: the user exists, simply with no profile (hence no access)


def test_changing_the_auth_email_updates_the_profile(conn):
    uid = new_auth_user(conn, "old@example.org")
    conn.execute("update auth.users set email = 'new@example.org' where id = %s", (uid,))
    assert profile(conn, uid)["email"] == "new@example.org"
    conn.execute("update auth.users set email = null where id = %s", (uid,))             # clearing an email never wipes the profile
    assert profile(conn, uid)["email"] == "new@example.org"
    clash = new_auth_user(conn, "taken@example.org")
    conn.execute("update auth.users set email = 'TAKEN@example.org' where id = %s", (uid,))   # would collide with another profile: warn, keep
    assert profile(conn, uid)["email"] == "new@example.org" and profile(conn, clash)["email"] == "taken@example.org"


def test_invitations_match_the_synchronised_email(w, conn):
    uid = new_auth_user(conn, "before@example.org")
    conn.execute("update auth.users set email = 'after@example.org' where id = %s", (uid,))
    sysmode(conn)
    conn.execute("insert into project_invitations (project_id, email, role, token_hash, invited_by, expires_at) values (%s,'after@example.org','SUPERVISOR',%s,%s,now()+interval '1 day')",
                 (w.project, "z" * 40, w.pm))
    assert one(conn, "select accept_project_invitation(%s,%s)", ("z" * 40, uid)) is not None


def test_deleting_an_auth_user_deactivates_the_profile_and_keeps_history(w, conn):
    sysmode(conn)
    c1 = claim(conn, w, w.a2)                                            # filed by w.se
    conn.execute("delete from auth.users where id = %s", (w.se,))        # was blocked by a foreign key before 0012
    p = profile(conn, w.se)
    assert p is not None and p["is_active"] is False and p["tokens_valid_after"] is not None
    assert one(conn, "select filed_by from execution_events where event_id = %s", (c1,)) == w.se       # history still names the person
    assert one(conn, "select count(*) from project_memberships where user_id = %s", (w.se,)) == 1


def test_a_returning_user_with_the_same_id_is_reactivated_not_duplicated(conn):
    uid = new_auth_user(conn, "back@example.org")
    conn.execute("delete from auth.users where id = %s", (uid,))
    assert profile(conn, uid)["is_active"] is False
    new_auth_user(conn, "back@example.org", uid=uid)
    assert profile(conn, uid)["is_active"] is True and one(conn, "select count(*) from profiles where id = %s", (uid,)) == 1


def test_profile_ids_no_longer_depend_on_a_foreign_key_to_auth(conn):
    assert one(conn, "select count(*) from pg_constraint where conrelid = 'profiles'::regclass and contype = 'f'") == 0
