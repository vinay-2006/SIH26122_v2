-- 0012 profile lifecycle hardening (applies unchanged to Supabase; the triggers sit on auth.users like the 0001 trigger did).
--  * A signup must NEVER fail because of our trigger: no email (phone / anonymous sign-in) => no profile (such a user has no access,
--    because every request needs a profile); an email clash is logged as a warning instead of aborting the signup.
--  * An email change in auth.users is copied to profiles (invitation acceptance matches on profiles.email).
--  * Deleting an auth user DEACTIVATES the profile instead of being blocked by a foreign key: history (claims, decisions, audit)
--    keeps pointing at the person, who simply can no longer sign in or act.
--  * tokens_valid_after lets an administrator revoke every token issued before a moment (stateless JWTs cannot otherwise be revoked).

ALTER TABLE profiles DROP CONSTRAINT profiles_id_fkey;
ALTER TABLE profiles ADD COLUMN tokens_valid_after TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION handle_new_user() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF NEW.email IS NULL OR position('@' IN NEW.email) < 2 THEN
    RETURN NEW;                                             -- phone / anonymous / malformed: no profile, no access, signup unaffected
  END IF;
  BEGIN
    INSERT INTO profiles (id, full_name, email)
    VALUES (NEW.id, coalesce(nullif(btrim(NEW.raw_user_meta_data->>'full_name'), ''), split_part(NEW.email, '@', 1)), NEW.email)
    ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, is_active = TRUE;
  EXCEPTION WHEN unique_violation THEN
    RAISE WARNING 'profile for % not created: email already used by another profile', NEW.id;
  END;
  RETURN NEW;
END $$;

CREATE FUNCTION sync_user_email() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF NEW.email IS NOT NULL AND position('@' IN NEW.email) >= 2 AND NEW.email IS DISTINCT FROM OLD.email THEN
    BEGIN
      UPDATE profiles SET email = NEW.email WHERE id = NEW.id;
    EXCEPTION WHEN unique_violation THEN
      RAISE WARNING 'email of % not synchronised: already used by another profile', NEW.id;
    END;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_auth_user_email_sync AFTER UPDATE OF email ON auth.users FOR EACH ROW EXECUTE FUNCTION sync_user_email();

CREATE FUNCTION deactivate_deleted_user() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE profiles SET is_active = FALSE, tokens_valid_after = now() WHERE id = OLD.id;
  RETURN OLD;
END $$;
CREATE TRIGGER trg_auth_user_deleted AFTER DELETE ON auth.users FOR EACH ROW EXECUTE FUNCTION deactivate_deleted_user();

-- New functions must not be executable by database clients. The implicit PUBLIC grant is a GLOBAL default, so the schema-level
-- ALTER DEFAULT PRIVILEGES of 0008 / 0010 cannot remove it; the global form below does, for every function created from now on.
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION sync_user_email(), deactivate_deleted_user() FROM PUBLIC, anon, authenticated;
