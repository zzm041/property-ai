-- PA-167 one-off repair: bring work_order_analyses.required_skill onto the same
-- vocabulary dispatch compares against (ELECTRICAL, not "electrician").
--
-- Scope: rows written before the fix. They hold the model's trade label because
-- `_fill_analysis` used to persist `mapped.raw_required_skill`. Re-dispatch was
-- never wrong about them (`_mapped_from_stored_analysis` re-mapped the row), so
-- this repair is about the row's own vocabulary: the supervisor console, the
-- analysis API response and any ops/reporting SQL read `required_skill` directly
-- and used to see a different word than the dispatcher.
--
-- Safe to re-run: it only touches rows whose value differs from the mapped code.
-- Rollback: reports/pa167/normalize-analysis-skill.rollback.sql
-- Run the whole file in ONE mysql session (step 2 uses a helper table).
--
-- Do NOT wrap this file in START TRANSACTION: MySQL commits implicitly around the
-- CREATE TABLE in step 2, so a trailing ROLLBACK would silently keep step 4.
-- Step 3 is the read-only preview; step 3b is the same preview with a real undo:
--
--   START TRANSACTION;
--   UPDATE work_order_analyses AS a
--   JOIN (SELECT 'PLUMBER' AS raw_key, 'PLUMBING' AS code UNION ALL
--         SELECT 'ELECTRICIAN', 'ELECTRICAL' UNION ALL
--         SELECT 'MAINTENANCE', 'MAINTENANCE') AS m
--     ON UPPER(TRIM(a.required_skill)) = m.raw_key
--   SET a.required_skill = m.code
--   WHERE BINARY a.required_skill <> m.code;
--   SELECT ROW_COUNT(), (SELECT COUNT(*) FROM work_order_analyses
--                        WHERE BINARY required_skill = UPPER(TRIM(required_skill)));
--   ROLLBACK;   -- no DDL above, so this really restores the table
--
-- Keep this file's alias list in sync with backend/app/domain/ai.py:_SKILL_ALIASES.

-- 0. What is dirty right now? (record this before touching anything)
SELECT required_skill, COUNT(*) AS rows_count
FROM work_order_analyses
GROUP BY required_skill
ORDER BY rows_count DESC;

-- 1. Backup: the rollback source. Nothing below is reversible without it.
CREATE TABLE IF NOT EXISTS pa167_analysis_skill_backup (
    analysis_id BIGINT NOT NULL PRIMARY KEY,
    required_skill VARCHAR(32) NULL,
    backed_up_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE = InnoDB;

INSERT IGNORE INTO pa167_analysis_skill_backup (analysis_id, required_skill)
SELECT id, required_skill FROM work_order_analyses;

SELECT COUNT(*) AS backed_up_rows FROM pa167_analysis_skill_backup;

-- 2. Alias table mirroring _SKILL_ALIASES (keys are upper-cased labels).
DROP TABLE IF EXISTS pa167_skill_map;
CREATE TABLE pa167_skill_map (
    raw_key VARCHAR(32) NOT NULL PRIMARY KEY,
    code VARCHAR(32) NOT NULL
) ENGINE = InnoDB;

INSERT INTO pa167_skill_map (raw_key, code) VALUES
    ('PLUMBER', 'PLUMBING'),
    ('PLUMBING', 'PLUMBING'),
    ('ELECTRICIAN', 'ELECTRICAL'),
    ('ELECTRICAL', 'ELECTRICAL'),
    ('HVAC', 'HVAC'),
    ('CLEANING', 'CLEANING'),
    ('SECURITY', 'SECURITY'),
    ('MAINTENANCE', 'MAINTENANCE');

-- 3. Dry run: how many rows would change? (must be shown before step 4)
SELECT COUNT(*) AS rows_to_update
FROM work_order_analyses AS a
JOIN pa167_skill_map AS m ON UPPER(TRIM(a.required_skill)) = m.raw_key
WHERE BINARY a.required_skill <> m.code;

-- 4. Apply.
UPDATE work_order_analyses AS a
JOIN pa167_skill_map AS m ON UPPER(TRIM(a.required_skill)) = m.raw_key
SET a.required_skill = m.code
WHERE BINARY a.required_skill <> m.code;

SELECT ROW_COUNT() AS updated_rows;

-- 5. Verify: no known label may survive, and unknown labels stay untouched.
SELECT required_skill, COUNT(*) AS rows_count
FROM work_order_analyses
GROUP BY required_skill
ORDER BY rows_count DESC;

SELECT COUNT(*) AS known_labels_left
FROM work_order_analyses AS a
JOIN pa167_skill_map AS m ON UPPER(TRIM(a.required_skill)) = m.raw_key
WHERE BINARY a.required_skill <> m.code;

SELECT COUNT(*) AS canonical_rows
FROM work_order_analyses
WHERE required_skill IS NOT NULL AND BINARY required_skill = UPPER(TRIM(required_skill));

-- 6. Helper table is disposable; the backup table is not (rollback needs it).
DROP TABLE pa167_skill_map;
