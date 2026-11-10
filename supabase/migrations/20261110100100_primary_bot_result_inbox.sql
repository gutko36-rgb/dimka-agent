-- Primary Telegram bot result inbox.
-- Only the trusted service_role backend may claim results addressed to dimka.
-- Specialized workers continue using claim_bot_messages() with their own JWT.

begin;

create or replace function public.claim_bot_results_for_primary(
    p_limit integer default 10,
    p_lease_seconds integer default 300
)
returns setof public.bot_messages
language plpgsql
volatile
security invoker
set search_path = ''
as $function$
begin
    if coalesce((select auth.jwt() ->> 'role'), '') <> 'service_role' then
        raise exception 'Only the trusted primary backend may claim primary results'
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
           last_error = 'Message expired before primary delivery'
     where m.to_bot_id = 'dimka'
       and m.message_type = 'result'
       and m.status in ('pending', 'processing')
       and m.expires_at is not null
       and m.expires_at <= now();

    update public.bot_messages as m
       set status = 'failed',
           completed_at = now(),
           last_error = 'Primary delivery lease expired; maximum attempts reached'
     where m.to_bot_id = 'dimka'
       and m.message_type = 'result'
       and m.status = 'processing'
       and m.claimed_at < now() - pg_catalog.make_interval(secs => p_lease_seconds)
       and m.attempt_count >= m.max_attempts;

    return query
    with candidates as (
        select m.id
          from public.bot_messages as m
         where m.to_bot_id = 'dimka'
           and m.message_type = 'result'
           and m.available_at <= now()
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

revoke all on function public.claim_bot_results_for_primary(integer, integer)
    from public, anon, authenticated;
grant execute on function public.claim_bot_results_for_primary(integer, integer)
    to service_role;

commit;
