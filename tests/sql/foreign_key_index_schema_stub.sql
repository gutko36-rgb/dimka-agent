CREATE TABLE IF NOT EXISTS public.agent_project_assets (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL,
    telegram_id bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS public.analytics_reports (
    id bigserial PRIMARY KEY,
    telegram_id bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS public.bot_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_message_id uuid
);

CREATE TABLE IF NOT EXISTS public.daily_surveys (
    id bigserial PRIMARY KEY,
    telegram_id bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS public.finance_debt_payments (
    id bigserial PRIMARY KEY,
    debt_id bigint NOT NULL,
    telegram_id bigint NOT NULL,
    finance_transaction_id bigint
);

CREATE TABLE IF NOT EXISTS public.lifestyle_goal_updates (
    id bigserial PRIMARY KEY,
    goal_id bigint NOT NULL,
    telegram_id bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS public.lifestyle_habit_logs (
    id bigserial PRIMARY KEY,
    habit_id bigint NOT NULL,
    telegram_id bigint NOT NULL
);
