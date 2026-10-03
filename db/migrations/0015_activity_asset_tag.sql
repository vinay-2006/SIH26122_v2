-- 0015 Activity asset tag. The existing matching engine's EXACT_ASSET tier compares a claim's asset_tag with the schedule activity's asset_tag; v2 had no
-- such column, so that tier could never fire. Purely additive: one NULLABLE text column, no default, no backfill, no constraint that existing rows could
-- violate. Existing schedule versions keep NULL (= "no asset tag known"); an importer that has no asset column leaves it NULL. Versioning, rollback and
-- activity identity are untouched (the column is part of a version's immutable activity row like every other baseline field).
ALTER TABLE baseline_activities ADD COLUMN asset_tag TEXT CHECK (asset_tag IS NULL OR length(btrim(asset_tag)) > 0);
CREATE INDEX idx_ba_asset ON baseline_activities (version_id, asset_tag) WHERE asset_tag IS NOT NULL;
