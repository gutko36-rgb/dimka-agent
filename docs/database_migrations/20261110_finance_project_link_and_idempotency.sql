-- Applied to Supabase project nqtdzgmyqhemxxxgicww on 2026-11-10.
-- Safe additive migration: existing transaction rows remain untouched.
alter table public.finance_transactions
  add column if not exists project_id bigint null
  references public.agent_projects(id) on delete set null;

alter table public.finance_transactions
  add column if not exists source_update_id bigint null;

alter table public.finance_transactions
  add column if not exists source_item_index integer not null default 0;

create index if not exists finance_transactions_project_id_idx
  on public.finance_transactions(project_id);

create unique index if not exists finance_transactions_owner_source_item_uidx
  on public.finance_transactions(telegram_id, source_update_id, source_item_index)
  where source_update_id is not null;
