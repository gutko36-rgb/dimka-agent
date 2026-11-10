-- Cover all foreign keys with indexes to keep deletes, joins and owner-scoped lookups efficient.
CREATE INDEX IF NOT EXISTS agent_project_assets_project_id_idx
    ON public.agent_project_assets (project_id);

CREATE INDEX IF NOT EXISTS analytics_reports_telegram_id_idx
    ON public.analytics_reports (telegram_id);

CREATE INDEX IF NOT EXISTS bot_messages_parent_message_id_idx
    ON public.bot_messages (parent_message_id);

CREATE INDEX IF NOT EXISTS daily_surveys_telegram_id_idx
    ON public.daily_surveys (telegram_id);

CREATE INDEX IF NOT EXISTS finance_debt_payments_debt_owner_idx
    ON public.finance_debt_payments (debt_id, telegram_id);

CREATE INDEX IF NOT EXISTS finance_debt_payments_transaction_owner_idx
    ON public.finance_debt_payments (finance_transaction_id, telegram_id);

CREATE INDEX IF NOT EXISTS lifestyle_goal_updates_goal_owner_idx
    ON public.lifestyle_goal_updates (goal_id, telegram_id);

CREATE INDEX IF NOT EXISTS lifestyle_habit_logs_habit_owner_idx
    ON public.lifestyle_habit_logs (habit_id, telegram_id);
