-- 0001 identity & access: profiles, platform grants, projects, settings, memberships, invitations + authorization helpers.
-- Authorization model: a role is a MEMBERSHIP on a project (not a property of the person). Platform-level authority
-- (PLATFORM_ADMIN, CREATE_PROJECT) is an explicit, revocable grant.

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

-- Backend-set per-transaction context (SET LOCAL app.actor_id = '<uuid>'). 'app.system' = 'on' is for migrations/seeds only.
CREATE FUNCTION app_actor() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.actor_id', true), '')::uuid $$;
CREATE FUNCTION app_is_system() RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('app.system', true), '') = 'on' $$;

CREATE TABLE profiles (
  id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE RESTRICT,
  full_name   TEXT NOT NULL CHECK (length(btrim(full_name)) >= 1),
  email       TEXT NOT NULL CHECK (position('@' IN email) > 1),
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_profiles_email ON profiles (lower(email));
CREATE TRIGGER trg_profiles_touch BEFORE UPDATE ON profiles FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Every auth user gets a profile (Supabase: trigger on auth.users; the same statement works on the local shim).
CREATE FUNCTION handle_new_user() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  INSERT INTO profiles (id, full_name, email)
  VALUES (NEW.id, coalesce(nullif(NEW.raw_user_meta_data->>'full_name', ''), split_part(NEW.email, '@', 1)), NEW.email)
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_auth_user_profile AFTER INSERT ON auth.users FOR EACH ROW EXECUTE FUNCTION handle_new_user();

CREATE TABLE platform_grants (
  grant_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  capability  TEXT NOT NULL CHECK (capability IN ('PLATFORM_ADMIN', 'CREATE_PROJECT')),
  granted_by  UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at  TIMESTAMPTZ,
  revoked_by  UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  CHECK ((revoked_at IS NULL) = (revoked_by IS NULL) OR revoked_by IS NULL)
);
CREATE UNIQUE INDEX uq_platform_grants_active ON platform_grants (user_id, capability) WHERE revoked_at IS NULL;

CREATE FUNCTION has_platform_capability(p_user UUID, p_cap TEXT) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT EXISTS (SELECT 1 FROM platform_grants WHERE user_id = p_user AND capability = p_cap AND revoked_at IS NULL) $$;

CREATE TABLE projects (
  project_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_code      TEXT NOT NULL UNIQUE CHECK (project_code ~ '^[A-Z0-9][A-Z0-9_-]{2,31}$'),
  project_name      TEXT NOT NULL CHECK (length(btrim(project_name)) >= 3),
  description       TEXT,
  client_name       TEXT,
  project_type      TEXT,
  location          TEXT,
  latitude          DOUBLE PRECISION CHECK (latitude  BETWEEN -90  AND 90),
  longitude         DOUBLE PRECISION CHECK (longitude BETWEEN -180 AND 180),
  geofence_radius_m REAL CHECK (geofence_radius_m IS NULL OR geofence_radius_m > 0),
  planned_start     DATE,
  planned_finish    DATE,
  contract_finish   DATE,
  lifecycle_status  TEXT NOT NULL DEFAULT 'UPCOMING' CHECK (lifecycle_status IN ('UPCOMING', 'ONGOING', 'COMPLETED')),
  record_status     TEXT NOT NULL DEFAULT 'ACTIVE'   CHECK (record_status IN ('ACTIVE', 'ARCHIVED')),
  created_by        UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (planned_finish IS NULL OR planned_start IS NULL OR planned_finish >= planned_start)
);
CREATE INDEX idx_projects_status ON projects (record_status, lifecycle_status);
CREATE TRIGGER trg_projects_touch BEFORE UPDATE ON projects FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE project_settings (
  project_id                 UUID PRIMARY KEY REFERENCES projects(project_id) ON DELETE RESTRICT,
  over_baseline_tolerance_pct NUMERIC(6,2) NOT NULL DEFAULT 10 CHECK (over_baseline_tolerance_pct BETWEEN 0 AND 100),
  completion_threshold_pct   NUMERIC(6,2) NOT NULL DEFAULT 95 CHECK (completion_threshold_pct BETWEEN 0 AND 100),
  working_days_per_week      SMALLINT NOT NULL DEFAULT 6 CHECK (working_days_per_week BETWEEN 1 AND 7),
  require_photo_evidence     BOOLEAN NOT NULL DEFAULT FALSE,
  extra                      JSONB NOT NULL DEFAULT '{}',
  updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_settings_touch BEFORE UPDATE ON project_settings FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE FUNCTION create_default_project_settings() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN INSERT INTO project_settings (project_id) VALUES (NEW.project_id); RETURN NEW; END $$;
CREATE TRIGGER trg_projects_default_settings AFTER INSERT ON projects FOR EACH ROW EXECUTE FUNCTION create_default_project_settings();

CREATE TABLE project_memberships (
  membership_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  user_id       UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  role          TEXT NOT NULL CHECK (role IN ('SITE_ENGINEER', 'SUPERVISOR', 'PROJECT_MANAGER')),
  status        TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REMOVED')),
  added_by      UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_membership_project_user UNIQUE (project_id, user_id)
);
CREATE INDEX idx_memberships_user ON project_memberships (user_id, status);
CREATE TRIGGER trg_memberships_touch BEFORE UPDATE ON project_memberships FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE project_invitations (
  invitation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
  email         TEXT NOT NULL CHECK (position('@' IN email) > 1),
  role          TEXT NOT NULL CHECK (role IN ('SITE_ENGINEER', 'SUPERVISOR')),
  token_hash    TEXT NOT NULL UNIQUE,                 -- only the hash is stored; the raw token is shown once to the inviter
  delivery      TEXT NOT NULL DEFAULT 'LINK' CHECK (delivery IN ('LINK', 'EMAIL')),
  invited_by    UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  expires_at    TIMESTAMPTZ NOT NULL,
  accepted_by   UUID REFERENCES profiles(id) ON DELETE RESTRICT,
  accepted_at   TIMESTAMPTZ,
  revoked_at    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((accepted_by IS NULL) = (accepted_at IS NULL)),
  CHECK (accepted_at IS NULL OR revoked_at IS NULL)
);
CREATE INDEX idx_invitations_project ON project_invitations (project_id, created_at DESC);
CREATE UNIQUE INDEX uq_invitation_open ON project_invitations (project_id, lower(email))
  WHERE accepted_at IS NULL AND revoked_at IS NULL;

-- ------------------------------------------------------------------ authorization helpers
CREATE FUNCTION project_role_of(p_project UUID, p_user UUID) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT role FROM project_memberships WHERE project_id = p_project AND user_id = p_user AND status = 'ACTIVE' $$;

CREATE FUNCTION is_project_member(p_project UUID) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT auth.uid() IS NOT NULL AND project_role_of(p_project, auth.uid()) IS NOT NULL $$;

-- Tripwire used by write triggers: the transaction's actor must hold one of the roles on the project.
-- (The backend ALSO checks; this is the second wall for a privileged connection.)
CREATE FUNCTION require_project_actor(p_project UUID, p_roles text[], p_what text) RETURNS void
LANGUAGE plpgsql STABLE AS $$
DECLARE v_actor UUID := app_actor(); v_role text;
BEGIN
  IF app_is_system() THEN RETURN; END IF;
  IF v_actor IS NULL THEN
    RAISE EXCEPTION '% requires an authenticated actor (app.actor_id not set)', p_what USING ERRCODE = '42501';
  END IF;
  v_role := project_role_of(p_project, v_actor);
  IF v_role IS NULL OR NOT (v_role = ANY (p_roles)) THEN
    RAISE EXCEPTION '% requires role % on the project (actor has %)', p_what, p_roles, coalesce(v_role, 'no membership')
      USING ERRCODE = '42501';
  END IF;
END $$;

CREATE FUNCTION trg_require_pm() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_project UUID;
BEGIN
  v_project := CASE WHEN TG_OP = 'DELETE' THEN OLD.project_id ELSE NEW.project_id END;
  PERFORM require_project_actor(v_project, ARRAY['PROJECT_MANAGER'], TG_TABLE_NAME || ' ' || TG_OP);
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

-- Only a user holding an active CREATE_PROJECT grant may create a project (and only as themselves).
CREATE FUNCTION trg_projects_create_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF app_is_system() THEN RETURN NEW; END IF;
  IF app_actor() IS NULL OR NEW.created_by <> app_actor() OR NOT has_platform_capability(app_actor(), 'CREATE_PROJECT') THEN
    RAISE EXCEPTION 'creating a project requires the CREATE_PROJECT platform grant' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_projects_create BEFORE INSERT ON projects FOR EACH ROW EXECUTE FUNCTION trg_projects_create_guard();

CREATE FUNCTION trg_projects_update_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM require_project_actor(NEW.project_id, ARRAY['PROJECT_MANAGER'], 'projects UPDATE');
  RETURN NEW;
END $$;
CREATE TRIGGER trg_projects_update BEFORE UPDATE ON projects FOR EACH ROW EXECUTE FUNCTION trg_projects_update_guard();

CREATE TRIGGER trg_settings_pm BEFORE UPDATE OR DELETE ON project_settings FOR EACH ROW EXECUTE FUNCTION trg_require_pm();
CREATE TRIGGER trg_invitations_pm BEFORE INSERT OR UPDATE OR DELETE ON project_invitations FOR EACH ROW EXECUTE FUNCTION trg_require_pm();

-- Membership changes: platform admin, or a PM touching only SE/SUPERVISOR rows, or the creator's own bootstrap PM row.
CREATE FUNCTION trg_memberships_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_actor UUID := app_actor(); v_project UUID; v_actor_role text; v_admin boolean; v_creator UUID;
  v_roles text[];
BEGIN
  IF app_is_system() THEN RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END; END IF;
  IF v_actor IS NULL THEN
    RAISE EXCEPTION 'project_memberships % requires an authenticated actor', TG_OP USING ERRCODE = '42501';
  END IF;
  v_project := CASE WHEN TG_OP = 'DELETE' THEN OLD.project_id ELSE NEW.project_id END;
  v_admin := has_platform_capability(v_actor, 'PLATFORM_ADMIN');
  v_actor_role := project_role_of(v_project, v_actor);
  v_roles := ARRAY[CASE WHEN TG_OP <> 'INSERT' THEN OLD.role END, CASE WHEN TG_OP <> 'DELETE' THEN NEW.role END];
  IF v_admin THEN RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END; END IF;
  IF TG_OP = 'INSERT' AND NEW.role = 'PROJECT_MANAGER' AND NEW.user_id = v_actor THEN
    SELECT created_by INTO v_creator FROM projects WHERE project_id = NEW.project_id;
    IF v_creator = v_actor AND NOT EXISTS (SELECT 1 FROM project_memberships WHERE project_id = NEW.project_id) THEN
      RETURN NEW;                                   -- creator bootstrap
    END IF;
  END IF;
  IF v_actor_role = 'PROJECT_MANAGER' AND NOT coalesce('PROJECT_MANAGER' = ANY (v_roles), FALSE) THEN   -- NULL slots (INSERT/DELETE) must not make this NULL
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
  END IF;
  RAISE EXCEPTION 'project_memberships %: only a platform admin may assign/remove PROJECT_MANAGER; a PM may manage SITE_ENGINEER/SUPERVISOR members of their own project', TG_OP
    USING ERRCODE = '42501';
END $$;
CREATE TRIGGER trg_memberships_guard BEFORE INSERT OR UPDATE OR DELETE ON project_memberships
  FOR EACH ROW EXECUTE FUNCTION trg_memberships_guard();

-- A project may never be left without an active PM.
CREATE FUNCTION trg_last_pm_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.role = 'PROJECT_MANAGER' AND OLD.status = 'ACTIVE'
     AND (TG_OP = 'DELETE' OR NEW.role <> 'PROJECT_MANAGER' OR NEW.status <> 'ACTIVE')
     AND NOT EXISTS (SELECT 1 FROM project_memberships m
                     WHERE m.project_id = OLD.project_id AND m.role = 'PROJECT_MANAGER' AND m.status = 'ACTIVE'
                       AND m.membership_id <> OLD.membership_id) THEN
    RAISE EXCEPTION 'cannot remove, suspend or demote the last active PROJECT_MANAGER of a project' USING ERRCODE = '23514';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER trg_last_pm BEFORE UPDATE OR DELETE ON project_memberships FOR EACH ROW EXECUTE FUNCTION trg_last_pm_guard();

-- Platform grants: only a platform admin (or a migration/seed) may grant or revoke.
CREATE FUNCTION trg_platform_grants_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF app_is_system() OR (app_actor() IS NOT NULL AND has_platform_capability(app_actor(), 'PLATFORM_ADMIN')) THEN
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
  END IF;
  RAISE EXCEPTION 'platform_grants % requires PLATFORM_ADMIN', TG_OP USING ERRCODE = '42501';
END $$;
CREATE TRIGGER trg_platform_grants BEFORE INSERT OR UPDATE OR DELETE ON platform_grants
  FOR EACH ROW EXECUTE FUNCTION trg_platform_grants_guard();
