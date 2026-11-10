CREATE TABLE IF NOT EXISTS public.finance_pending_actions (
    id bigserial PRIMARY KEY,
    telegram_id bigint NOT NULL,
    source_update_id bigint NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'confirmed', 'cancelled', 'expired')),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '30 minutes'),
    processed_at timestamptz,
    UNIQUE (telegram_id, source_update_id)
);

CREATE INDEX IF NOT EXISTS finance_pending_actions_owner_status_idx
    ON public.finance_pending_actions (telegram_id, status, expires_at);

ALTER TABLE public.finance_pending_actions ENABLE ROW LEVEL SECURITY;
