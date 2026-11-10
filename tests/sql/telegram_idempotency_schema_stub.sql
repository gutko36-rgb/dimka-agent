-- Disposable schema for the applied Telegram idempotency migrations.
create table public.conversations (
    id bigserial primary key,
    telegram_id bigint not null,
    role text not null,
    content text not null,
    created_at timestamptz not null default now()
);
create table public.finance_payments (
    id bigserial primary key,
    telegram_id bigint not null,
    title text not null,
    amount numeric not null,
    day_of_month integer not null
);
create table public.finance_purchases (
    id bigserial primary key,
    telegram_id bigint not null,
    title text not null,
    price numeric not null,
    status text not null default 'planned'
);
create table public.agent_projects (
    id bigserial primary key,
    telegram_id bigint not null,
    title text not null,
    status text not null default 'active',
    created_at timestamptz not null default now()
);
create table public.agent_project_items (
    id bigserial primary key,
    telegram_id bigint not null,
    project_id bigint not null,
    kind text not null,
    content text not null,
    due_date date,
    done boolean not null default false,
    created_at timestamptz not null default now()
);
create table public.daily_surveys (
    id bigserial primary key,
    telegram_id bigint not null,
    survey_date date not null,
    kind text not null,
    question text not null,
    answer text,
    summary text,
    answered_at timestamptz,
    created_at timestamptz not null default now()
);
create table public.analytics_reports (
    id bigserial primary key,
    telegram_id bigint not null,
    kind text not null,
    period_start date not null,
    period_end date not null,
    content text not null,
    created_at timestamptz not null default now()
);
create table public.daily_logs (
    id bigserial primary key,
    telegram_id bigint not null,
    log_date date not null,
    checkin_type text not null,
    content text
);
create table if not exists public.finance_transactions (
    id bigserial primary key,
    telegram_id bigint not null,
    source_update_id bigint,
    source_item_index smallint not null default 0,
    kind text not null,
    amount numeric not null,
    category text not null,
    note text,
    date date not null
);
create table public.finance_pending_actions (
    id bigserial primary key,
    telegram_id bigint not null,
    source_update_id bigint not null,
    payload jsonb not null,
    status text not null default 'pending'
);
create table public.agent_project_assets (
    id bigserial primary key,
    telegram_id bigint not null,
    project_id bigint not null,
    telegram_file_unique_id text not null
);
