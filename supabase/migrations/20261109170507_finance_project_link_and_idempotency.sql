ALTER TABLE public.finance_transactions
    ADD COLUMN IF NOT EXISTS project_id bigint NULL
    REFERENCES public.agent_projects(id) ON DELETE SET NULL;

ALTER TABLE public.finance_transactions
    ADD COLUMN IF NOT EXISTS source_update_id bigint NULL;

CREATE INDEX IF NOT EXISTS finance_transactions_project_id_idx
    ON public.finance_transactions (project_id);

CREATE UNIQUE INDEX IF NOT EXISTS finance_transactions_owner_source_update_uidx
    ON public.finance_transactions (telegram_id, source_update_id)
    WHERE source_update_id IS NOT NULL;
