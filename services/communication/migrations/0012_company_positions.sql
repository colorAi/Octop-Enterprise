CREATE TABLE job_titles (
    id text PRIMARY KEY,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 40),
    builtin boolean NOT NULL DEFAULT false,
    sort_order integer NOT NULL DEFAULT 100,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX job_titles_name_unique ON job_titles (lower(name));
INSERT INTO job_titles(id,name,builtin,sort_order) VALUES
 ('job_employee','职员',true,10), ('job_specialist','专员',true,20),
 ('job_senior_specialist','高级专员',true,30), ('job_team_lead','组长',true,40),
 ('job_supervisor','主管',true,50), ('job_manager','经理',true,60),
 ('job_senior_manager','高级经理',true,70), ('job_director','总监',true,80),
 ('job_deputy_general_manager','副总经理',true,90), ('job_general_manager','总经理',true,100);
ALTER TABLE users ADD COLUMN job_title_id text REFERENCES job_titles(id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN is_admin boolean NOT NULL DEFAULT false;
