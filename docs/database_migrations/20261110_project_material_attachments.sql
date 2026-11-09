-- Project photos and document references.
-- Applied to Supabase project nqtdzgmyqhemxxxgicww.
-- Telegram stores the binary file; Supabase stores only owner-scoped file references and metadata.
create table if not exists public.agent_project_assets (
    id bigserial primary key,
    telegram_id bigint not null,
    project_id bigint not null references public.agent_projects(id) on delete cascade,
    telegram_file_id text not null,
    telegram_file_unique_id text not null,
    media_type text not null check (media_type in ('photo', 'document')),
    file_name text,
    mime_type text,
    caption text,
    source_message_id bigint,
    created_at timestamptz not null default now(),
    unique (telegram_id, telegram_file_unique_id)
);

create index if not exists agent_project_assets_owner_project_idx
    on public.agent_project_assets (telegram_id, project_id, created_at desc);

alter table public.agent_project_assets enable row level security;
