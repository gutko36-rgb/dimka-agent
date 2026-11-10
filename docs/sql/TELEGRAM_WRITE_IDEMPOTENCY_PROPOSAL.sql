-- REVIEW DRAFT ONLY — NOT A SUPABASE MIGRATION.
-- Additive idempotency support for Telegram-originated writes.
-- Existing rows receive NULL source_update_id, so they remain valid.
-- Verify duplicate natural keys before applying to a real Supabase project.

begin;

alter table public.conversations
    add column if not exists source_update_id bigint,
    add column if not exists source_item_index smallint not null default 0;

create unique index if not exists conversations_owner_source_item_uidx
    on public.conversations (telegram_id, source_update_id, source_item_index);

alter table public.finance_payments
    add column if not exists source_update_id bigint;

create unique index if not exists finance_payments_owner_source_update_uidx
    on public.finance_payments (telegram_id, source_update_id);

alter table public.finance_purchases
    add column if not exists source_update_id bigint;

create unique index if not exists finance_purchases_owner_source_update_uidx
    on public.finance_purchases (telegram_id, source_update_id);

alter table public.agent_projects
    add column if not exists source_update_id bigint;

create unique index if not exists agent_projects_owner_source_update_uidx
    on public.agent_projects (telegram_id, source_update_id);

alter table public.agent_project_items
    add column if not exists source_update_id bigint,
    add column if not exists source_item_index smallint not null default 0;

create unique index if not exists agent_project_items_owner_source_item_uidx
    on public.agent_project_items (telegram_id, source_update_id, source_item_index);

alter table public.daily_surveys
    add column if not exists last_answer_update_id bigint;

create unique index if not exists daily_surveys_owner_date_kind_uidx
    on public.daily_surveys (telegram_id, survey_date, kind);

create unique index if not exists analytics_reports_owner_period_uidx
    on public.analytics_reports (telegram_id, kind, period_start, period_end);

create unique index if not exists daily_logs_owner_date_type_uidx
    on public.daily_logs (telegram_id, log_date, checkin_type);

-- These idempotency indexes already exist in the inspected project; IF NOT EXISTS
-- makes the proposal portable to a fresh copy of the current schema.
create unique index if not exists finance_transactions_owner_source_item_uidx
    on public.finance_transactions (telegram_id, source_update_id, source_item_index)
    where source_update_id is not null;

create unique index if not exists finance_pending_actions_telegram_id_source_update_id_key
    on public.finance_pending_actions (telegram_id, source_update_id);

create unique index if not exists agent_project_assets_owner_project_file_uidx
    on public.agent_project_assets (telegram_id, project_id, telegram_file_unique_id);

commit;
