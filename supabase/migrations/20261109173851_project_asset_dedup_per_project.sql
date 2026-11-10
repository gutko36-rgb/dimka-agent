ALTER TABLE public.agent_project_assets
    DROP CONSTRAINT IF EXISTS agent_project_assets_telegram_id_telegram_file_unique_id_key;

ALTER TABLE public.agent_project_assets
    ADD CONSTRAINT agent_project_assets_owner_project_file_uidx
    UNIQUE (telegram_id, project_id, telegram_file_unique_id);
