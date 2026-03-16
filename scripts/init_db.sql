-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Useful monitoring view: latest run per source
CREATE OR REPLACE VIEW v_latest_scrape_runs AS
SELECT DISTINCT ON (source)
    id, source, started_at, finished_at, status,
    vacancies_found, new_count, changed_count, deactivated_count,
    error_message
FROM scrape_runs
ORDER BY source, started_at DESC;

-- View: active vacancies with latest change timestamp
CREATE OR REPLACE VIEW v_active_vacancies AS
SELECT
    v.id,
    v.source,
    v.title,
    v.company,
    v.location,
    v.salary_min,
    v.salary_max,
    v.salary_currency,
    v.url,
    v.first_seen_at,
    v.last_seen_at,
    COUNT(vc.id) AS change_count
FROM vacancies v
LEFT JOIN vacancy_changes vc ON vc.vacancy_id = v.id
WHERE v.is_active = true
GROUP BY v.id;
