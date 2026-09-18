-- ---------------------------------------------------------------------------
-- 009  The three RLS policies 006 did not know it needed -- and a guard so the
--      next missing one fails loudly instead of reading as an empty database.
--
-- WHAT HAPPENED. 006 grants ads_owner select on fourteen public tables and adds
-- a permissive policy to eleven of them. The other three -- brands, campaigns,
-- products -- were left out on the belief that they had no RLS. That belief
-- came from grepping growth-engine's migrations for `enable row level
-- security` and not finding them.
--
-- The database disagreed:
--
--     brands       rls=True  policies=0
--     campaigns    rls=True  policies=0
--     products     rls=True  policies=0
--
-- RLS is on. It was enabled outside the migration files -- Supabase's dashboard
-- has a toggle for it, and the linter recommends it -- so no amount of reading
-- growth-engine's SQL would have revealed it. THE CATALOGUE IS THE TRUTH; the
-- migration history is only a record of one way things get changed.
--
-- WHAT IT LOOKED LIKE. Not an error. `intel status --brand renegade` said:
--
--     no brand 'renegade'. Known brands: none
--
-- ...against a table with two rows in it. ads_owner is NOBYPASSRLS, ads.brand
-- executes with its owner's rights, and RLS with no policy returns zero rows.
-- The honest-looking message named a cause that was not the cause, and the
-- obvious next move -- check the spelling, check the importer -- leads nowhere.
-- This is the exact failure 006's own header describes at length. Writing the
-- warning down did not stop me shipping it.
-- ---------------------------------------------------------------------------

begin;

do $$
declare t text;
begin
    foreach t in array array['brands', 'campaigns', 'products'] loop
        execute format('drop policy if exists ads_owner_select on public.%I', t);
        execute format(
            'create policy ads_owner_select on public.%I '
            'for select to ads_owner using (true)', t);
    end loop;
end;
$$;


-- ---------------------------------------------------------------------------
-- The guard.
--
-- Everything above is a list I wrote by hand, and the bug being fixed was a
-- hand-written list that was wrong. So this asks the catalogue instead: is
-- there ANY table ads_owner may select from, with RLS on, and no policy that
-- admits ads_owner? If so, fail the migration and name it.
--
-- It fires for cases nobody can foresee from this repo: growth-engine adds a
-- table and 006's grant list grows, or somebody enables RLS in the dashboard on
-- a table that already works. Both are silent today. Both are one `raise` away
-- from being obvious.
--
-- `'public' = any(p.roles)` counts too -- a policy granted to PUBLIC covers
-- ads_owner, and demanding its name specifically would fail a table that is
-- already fine.
-- ---------------------------------------------------------------------------
do $$
declare
    uncovered text[];
begin
    select array_agg(distinct c.relname order by c.relname)
      into uncovered
      from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public'
       and c.relkind = 'r'
       and c.relrowsecurity
       and has_table_privilege('ads_owner', c.oid, 'select')
       and not exists (
           select 1
             from pg_policies p
            where p.schemaname = 'public'
              and p.tablename = c.relname
              and p.cmd in ('SELECT', 'ALL')
              and ('ads_owner' = any(p.roles) or 'public' = any(p.roles))
       );

    if uncovered is not null then
        raise exception
            'RLS is on with no ads_owner select policy: %. '
            'These return ZERO ROWS to ads_owner rather than an error, so every '
            'metric built on them reads as an empty database. Add a policy '
            'alongside the ones in 006 and 009.', uncovered;
    end if;
end;
$$;

commit;
