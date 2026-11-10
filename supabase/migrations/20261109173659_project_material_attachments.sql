CREATE TABLE IF NOT EXISTS public.agent_project_assets (
    id bigserial PRIMARY KEY,
    telegram_id bigint NOT NULL,
    project_id bigint NOT NULL REFERENCES public.agent_projects(id) ON DELETE CASCADE,
    telegram_file_id text NOT NULL,
    telegram_file_unique_id text NOT NULL,
    media_type text NOT NULL CHECK (media_type IN ('photo', 'document')),
    file_name text,
    mime_type text,
    caption text,
    source_message_id bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (telegram_id, telegram_file_unique_id)
);

CREATE INDEX IF NOT EXISTS agent_project_assets_owner_project_idx
    ON public.agent_project_assets (telegram_id, project_id, created_at DESC);

ALTER TABLE public.agent_project_assets ENABLE ROW LEVEL SECURITY;
