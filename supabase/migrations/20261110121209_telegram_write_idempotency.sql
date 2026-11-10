ALTER TABLE public.conversations
    ADD COLUMN IF NOT EXISTS source_update_id bigint,
    ADD COLUMN IF NOT EXISTS source_item_index smallint NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS conversations_owner_source_item_uidx
    ON public.conversations (telegram_id, source_update_id, source_item_index);

ALTER TABLE public.finance_payments
    ADD COLUMN IF NOT EXISTS source_update_id bigint;

CREATE UNIQUE INDEX IF NOT EXISTS finance_payments_owner_source_update_uidx
    ON public.finance_payments (telegram_id, source_update_id);

ALTER TABLE public.finance_purchases
    ADD COLUMN IF NOT EXISTS source_update_id bigint;

CREATE UNIQUE INDEX IF NOT EXISTS finance_purchases_owner_source_update_uidx
    ON public.finance_purchases (telegram_id, source_update_id);

ALTER TABLE public.agent_projects
    ADD COLUMN IF NOT EXISTS source_update_id bigint;

CREATE UNIQUE INDEX IF NOT EXISTS agent_projects_owner_source_update_uidx
    ON public.agent_projects (telegram_id, source_update_id);

ALTER TABLE public.agent_project_items
    ADD COLUMN IF NOT EXISTS source_update_id bigint,
    ADD COLUMN IF NOT EXISTS source_item_index smallint NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS agent_project_items_owner_source_item_uidx
    ON public.agent_project_items (telegram_id, source_update_id, source_item_index);

ALTER TABLE public.daily_surveys
    ADD COLUMN IF NOT EXISTS last_answer_update_id bigint;

CREATE UNIQUE INDEX IF NOT EXISTS daily_surveys_owner_date_kind_uidx
    ON public.daily_surveys (telegram_id, survey_date, kind);

CREATE UNIQUE INDEX IF NOT EXISTS analytics_reports_owner_period_uidx
    ON public.analytics_reports (telegram_id, kind, period_start, period_end);

CREATE UNIQUE INDEX IF NOT EXISTS daily_logs_owner_date_type_uidx
    ON public.daily_logs (telegram_id, log_date, checkin_type);

CREATE UNIQUE INDEX IF NOT EXISTS finance_transactions_owner_source_item_uidx
    ON public.finance_transactions (telegram_id, source_update_id, source_item_index)
    WHERE source_update_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS finance_pending_actions_telegram_id_source_update_id_key
    ON public.finance_pending_actions (telegram_id, source_update_id);

CREATE UNIQUE INDEX IF NOT EXISTS agent_project_assets_owner_project_file_uidx
    ON public.agent_project_assets (telegram_id, project_id, telegram_file_unique_id);
