DROP INDEX IF EXISTS public.finance_transactions_owner_source_update_uidx;

ALTER TABLE public.finance_transactions
    ADD COLUMN IF NOT EXISTS source_item_index integer NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS finance_transactions_owner_source_item_uidx
    ON public.finance_transactions (telegram_id, source_update_id, source_item_index)
    WHERE source_update_id IS NOT NULL;
