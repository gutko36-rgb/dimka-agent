-- REVIEW DRAFT ONLY — NOT A SUPABASE MIGRATION.
-- This file is intentionally stored under docs/sql so it cannot be applied
-- automatically by the Supabase migration runner.
--
-- Before converting this draft into a migration:
-- 1. Provision a distinct Supabase Auth identity for each bot.
-- 2. Set app_metadata.bot_id through a trusted admin process (never user_metadata).
-- 3. Give each bot its own short-lived/refreshed authenticated JWT.
-- 4. Do NOT distribute the Supabase service_role key to individual bots.
-- 5. Review this SQL and test it on a disposable Supabase project.
--
-- The migration creates only additive objects. It does not alter existing
-- DIMKA tables, existing user data, Telegram polling, or bot runtime.

begin;

create table public.bot_registry (
    bot_id text primary key
        check (bot_id ~ '^[a-z][a-z0-9_-]{0,63}$'),
    role text not null
        check (length(btrim(role)) between 1 and 80),
    display_name text not null
        check (length(btrim(display_name)) between 1 and 100),
    is_active boolean not null default true,
    capabilities jsonb not null default '{}'::jsonb
        check (jsonb_typeof(capabilities) = 'object'),
    created_at timestamptz not null default now()
);

comment on table public.bot_registry is
    'Public, non-secret registry of specialized bot identities. Never store Telegram tokens here.';

create table public.bot_messages (
    id uuid primary key default gen_random_uuid(),
    task_id uuid not null,
    parent_message_id uuid null
        references public.bot_messages(id) on delete set null,
    from_bot_id text not null
        references public.bot_registry(bot_id) on delete restrict,
    to_bot_id text not null
        references public.bot_registry(bot_id) on delete restrict,
    message_type text not null
        check (message_type in ('task', 'result', 'status', 'event', 'approval_request')),
    payload jsonb not null default '{}'::jsonb
        check (jsonb_typeof(payload) = 'object'),
    schema_version smallint not null default 1
        check (schema_version > 0),
    status text not null default 'pending'
        check (status in ('pending', 'processing', 'completed', 'failed', 'cancelled')),
    idempotency_key text not null
        check (length(btrim(idempotency_key)) between 1 and 200),
    attempt_count smallint not null default 0
        check (attempt_count >= 0),
    max_attempts smallint not null default 5
        check (max_attempts between 1 and 10),
    hop_count smallint not null default 0
        check (hop_count between 0 and 8),
    created_at timestamptz not null default now(),
    available_at timestamptz not null default now(),
    expires_at timestamptz null,
    claimed_at timestamptz null,
    completed_at timestamptz null,
    last_error text null,
    constraint bot_messages_idempotency_unique
        unique (from_bot_id, idempotency_key),
    constraint bot_messages_expiry_after_creation
        check (expires_at is null or expires_at > created_at)
);

comment on table public.bot_messages is
    'Machine-to-machine task and event queue. Telegram coordination chat is for human-visible updates only.';
comment on column public.bot_messages.idempotency_key is
    'Sender-scoped key that prevents duplicate enqueue operations.';
comment on column public.bot_messages.hop_count is
    'Routing depth guard; application code must reject messages beyond the configured hop limit.';

create index bot_messages_inbox_idx
    on public.bot_messages (to_bot_id, status, available_at, created_at)
    where status in ('pending', 'processing');

create index bot_messages_task_idx
    on public.bot_messages (task_id, created_at);

create index bot_messages_expiry_idx
    on public.bot_messages (expires_at)
    where status in ('pending', 'processing') and expires_at is not null;

create index bot_messages_claim_lease_idx
    on public.bot_messages (to_bot_id, claimed_at)
    where status = 'processing';

-- Register the existing primary bot as a non-secret identity.
-- ON CONFLICT DO NOTHING preserves any deliberately configured registry row.
insert into public.bot_registry (bot_id, role, display_name)
values ('dimka', 'personal_assistant', 'Димка')
on conflict (bot_id) do nothing;

-- RLS is enabled on both public-schema tables. No anonymous access is granted.
alter table public.bot_registry enable row level security;
alter table public.bot_messages enable row level security;

revoke all on table public.bot_registry from public, anon, authenticated;
revoke all on table public.bot_messages from public, anon, authenticated;

-- Authenticated bot identities may inspect active, non-secret bot registrations.
grant select on table public.bot_registry to authenticated;
create policy bot_registry_read_active_or_self
    on public.bot_registry
    for select
    to authenticated
    using (
        is_active
        or bot_id = (select auth.jwt() -> 'app_metadata' ->> 'bot_id')
    );

-- A bot may read messages it sent or messages addressed to it.
grant select, insert on table public.bot_messages to authenticated;
create policy bot_messages_read_sender_or_recipient
    on public.bot_messages
    for select
    to authenticated
    using (
        from_bot_id = (select auth.jwt() -> 'app_metadata' ->> 'bot_id')
        or to_bot_id = (select auth.jwt() -> 'app_metadata' ->> 'bot_id')
    );

-- A bot may enqueue only as itself, and only to an active registered recipient.
create policy bot_messages_insert_as_sender
    on public.bot_messages
    for insert
    to authenticated
    with check (
        from_bot_id = (select auth.jwt() -> 'app_metadata' ->> 'bot_id')
        and exists (
            select 1
            from public.bot_registry sender
            where sender.bot_id = bot_messages.from_bot_id
              and sender.is_active
        )
        and exists (
            select 1
            from public.bot_registry recipient
            where recipient.bot_id = bot_messages.to_bot_id
              and recipient.is_active
        )
    );

-- Only the addressed bot may update processing metadata.
-- Column-level grants prevent it from rewriting sender, recipient, payload or task identity.
grant update (status, claimed_at, completed_at, attempt_count, last_error)
    on table public.bot_messages to authenticated;

create policy bot_messages_update_recipient
    on public.bot_messages
    for update
    to authenticated
    using (
        to_bot_id = (select auth.jwt() -> 'app_metadata' ->> 'bot_id')
    )
    with check (
        to_bot_id = (select auth.jwt() -> 'app_metadata' ->> 'bot_id')
    );

-- Atomically claim work for the authenticated bot. This is SECURITY INVOKER:
-- RLS remains active, and the caller can only claim rows addressed to its
-- trusted app_metadata.bot_id. Expired leases are retried; exhausted messages
-- are marked failed instead of remaining stuck forever.
create or replace function public.claim_bot_messages(
    p_limit integer default 20,
    p_lease_seconds integer default 300
)
returns setof public.bot_messages
language plpgsql
volatile
security invoker
set search_path = ''
as $function$
declare
    v_bot_id text;
begin
    v_bot_id := (select auth.jwt() -> 'app_metadata' ->> 'bot_id');

    if v_bot_id is null or btrim(v_bot_id) = '' then
        raise exception 'Missing app_metadata.bot_id claim'
            using errcode = '42501';
    end if;
    if p_limit is null or p_limit < 1 or p_limit > 100 then
        raise exception 'p_limit must be between 1 and 100'
            using errcode = '22023';
    end if;
    if p_lease_seconds is null or p_lease_seconds < 30 or p_lease_seconds > 3600 then
        raise exception 'p_lease_seconds must be between 30 and 3600'
            using errcode = '22023';
    end if;

    update public.bot_messages as m
       set status = 'failed',
           completed_at = now(),
           last_error = 'Claim lease expired; maximum attempts reached'
     where m.to_bot_id = v_bot_id
       and m.status = 'processing'
       and m.claimed_at < now() - pg_catalog.make_interval(secs => p_lease_seconds)
       and m.attempt_count >= m.max_attempts;

    return query
    with candidates as (
        select m.id
          from public.bot_messages as m
         where m.to_bot_id = v_bot_id
           and (m.expires_at is null or m.expires_at > now())
           and m.attempt_count < m.max_attempts
           and (
                m.status = 'pending'
                or (
                    m.status = 'processing'
                    and m.claimed_at is not null
                    and m.claimed_at < now() - pg_catalog.make_interval(secs => p_lease_seconds)
                )
           )
         order by m.available_at, m.created_at
         limit p_limit
         for update of m skip locked
    )
    update public.bot_messages as m
       set status = 'processing',
           claimed_at = now(),
           attempt_count = m.attempt_count + 1,
           completed_at = null,
           last_error = null
      from candidates as c
     where m.id = c.id
    returning m.*;
end;
$function$;

revoke all on function public.claim_bot_messages(integer, integer) from public, anon;
grant execute on function public.claim_bot_messages(integer, integer) to authenticated;

-- Server-side administration is reserved for trusted backend operations.
-- Never place this key in a bot's environment if that bot is not the trusted
-- central backend; use per-bot authenticated JWTs instead.
grant all on table public.bot_registry to service_role;
grant all on table public.bot_messages to service_role;

commit;
