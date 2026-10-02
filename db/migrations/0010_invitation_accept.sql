-- 0010 invitation acceptance + function privilege hygiene.
-- Accepting an invitation is the one place where the *invitee* (not a PM/admin) causes a membership row to appear. The check is done
-- inside this SECURITY DEFINER function (valid, unexpired, unused token whose email matches the accepting user's profile); the guard
-- triggers stay fully in force for every other path.

CREATE FUNCTION accept_project_invitation(p_token_hash TEXT, p_user UUID) RETURNS UUID
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v project_invitations%ROWTYPE; v_email TEXT; v_prev TEXT := coalesce(current_setting('app.system', true), ''); v_mid UUID;
BEGIN
  SELECT * INTO v FROM project_invitations WHERE token_hash = p_token_hash FOR UPDATE;
  IF NOT FOUND OR v.revoked_at IS NOT NULL OR v.accepted_at IS NOT NULL OR v.expires_at < now() THEN
    RAISE EXCEPTION 'invitation is invalid, expired or already used' USING ERRCODE = '22023';
  END IF;
  SELECT email INTO v_email FROM profiles WHERE id = p_user AND is_active;
  IF v_email IS NULL OR lower(v_email) <> lower(v.email) THEN
    RAISE EXCEPTION 'invitation was issued to a different email address' USING ERRCODE = '42501';
  END IF;
  IF EXISTS (SELECT 1 FROM project_memberships WHERE project_id = v.project_id AND user_id = p_user AND status = 'ACTIVE') THEN
    RAISE EXCEPTION 'already an active member of this project' USING ERRCODE = '23505';
  END IF;
  PERFORM set_config('app.system', 'on', true);
  INSERT INTO project_memberships (project_id, user_id, role, status, added_by)
  VALUES (v.project_id, p_user, v.role, 'ACTIVE', v.invited_by)
  ON CONFLICT (project_id, user_id) DO UPDATE SET role = EXCLUDED.role, status = 'ACTIVE', added_by = EXCLUDED.added_by
  RETURNING membership_id INTO v_mid;
  UPDATE project_invitations SET accepted_by = p_user, accepted_at = now() WHERE invitation_id = v.invitation_id;
  PERFORM set_config('app.system', v_prev, true);
  RETURN v_mid;
END $$;

-- Functions created after 0008 were again executable by PUBLIC (the default). Close that, then re-grant only the RLS helpers.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION is_project_member(uuid), project_role_of(uuid, uuid), can_read_claim(uuid, uuid),
  can_read_claim_by_id(uuid, uuid), is_pm(uuid), is_supervisor_or_pm(uuid), is_member_of_any_project(), shares_project_with(uuid)
  TO authenticated;
