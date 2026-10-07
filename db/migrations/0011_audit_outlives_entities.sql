-- 0011 An audit record must outlive the entity it describes. audit_logs is append-only, so a foreign key to schedule_versions both
-- blocks discarding an unlocked (audited) draft and cannot be satisfied with ON DELETE SET NULL (that would be an UPDATE of the log).
-- The version id stays in the row as plain data (and entity_id holds it as text); nothing about the log's integrity is weakened.
ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_schedule_version_id_fkey;
