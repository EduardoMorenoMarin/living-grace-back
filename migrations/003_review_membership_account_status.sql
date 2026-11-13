-- OPTIONAL existing-data review/reconciliation; NOT an automatically applied migration.
-- Requires 002_add_pending_user_status.sql committed first. Run with ON_ERROR_STOP.
-- Audit ACTIVE accounts lacking an active membership in an available ministry.
SELECT u.id, u.status, u.deleted_at
FROM users u
WHERE u.status = 'ACTIVE' AND u.deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM ministry_members mm JOIN ministries m ON m.id = mm.ministry_id
    WHERE mm.user_id = u.id AND mm.status = 'ACTIVE' AND mm.deleted_at IS NULL
      AND m.status = 'ACTIVE' AND m.deleted_at IS NULL
  )
ORDER BY u.id;

-- Review every affected ID and administrator bootstrap before opting into changes.
-- No suspended/deleted account is activated, and no historical notification is edited.
BEGIN;
LOCK TABLE users, ministry_members, ministries IN SHARE ROW EXCLUSIVE MODE;
UPDATE users u SET status = 'PENDING', updated_at = now()
WHERE u.status = 'ACTIVE' AND u.deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM ministry_members mm JOIN ministries m ON m.id = mm.ministry_id
    WHERE mm.user_id = u.id AND mm.status = 'ACTIVE' AND mm.deleted_at IS NULL
      AND m.status = 'ACTIVE' AND m.deleted_at IS NULL
  )
RETURNING u.id, u.status;
ROLLBACK;
-- Intentionally defaults to ROLLBACK. After reviewing output, an operator may
-- explicitly replace ROLLBACK with COMMIT in a separate, approved maintenance run.
