-- Finance natural-language confirmation drafts.
-- Applied to Supabase project nqtdzgmyqhemxxxgicww.
-- No existing financial transaction rows are changed.
create table if not exists public.finance_pending_actions (
    id bigserial primary key,
    telegram_id bigint not null,
    source_update_id bigint not null,
    payload jsonb not null,
    status text not null default 'pending'
        check (status in ('pending', 'processing', 'confirmed', 'cancelled', 'expired')),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null default (now() + interval '30 minutes'),
    processed_at timestamptz,
    unique (telegram_id, source_update_id)
);

create index if not exists finance_pending_actions_owner_status_idx
    on public.finance_pending_actions (telegram_id, status, expires_at);

alter table public.finance_pending_actions enable row level security;
