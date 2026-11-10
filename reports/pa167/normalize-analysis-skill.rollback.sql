-- PA-167 rollback: restore work_order_analyses.required_skill from the backup the
-- repair took in reports/pa167/normalize-analysis-skill.sql (step 1).
--
-- The original label cannot be recomputed from the repaired value (the row no
-- longer distinguishes "electrician" from "Electrician"), which is exactly why the
-- repair refuses to run without the backup row for every analysis it touches.

-- 0. Preconditions: the backup must be at least as complete as the table.
SELECT
    (SELECT COUNT(*) FROM work_order_analyses) AS analysis_rows,
    (SELECT COUNT(*) FROM pa167_analysis_skill_backup) AS backup_rows;

-- 1. Restore.
UPDATE work_order_analyses AS a
JOIN pa167_analysis_skill_backup AS b ON b.analysis_id = a.id
SET a.required_skill = b.required_skill
WHERE NOT (a.required_skill <=> b.required_skill);

SELECT ROW_COUNT() AS restored_rows;

-- 2. Verify the vocabulary is back to the pre-repair shape.
SELECT required_skill, COUNT(*) AS rows_count
FROM work_order_analyses
GROUP BY required_skill
ORDER BY rows_count DESC;

-- 3. Drop the backup only after the above matches the step-0 output of the repair.
-- DROP TABLE pa167_analysis_skill_backup;
