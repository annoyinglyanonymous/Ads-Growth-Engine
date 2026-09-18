-- ---------------------------------------------------------------------------
-- 007  Structure changes -- the other half of "why did CPA go up".
--
-- DEPENDS ON growth-engine/migrations/046_meta_change_log.sql. Apply that
-- first. This file is written so that applying it early is harmless rather
-- than fatal: if public.meta_structure_changes is not there, it creates
-- nothing and says so, and ads.changes reports "not available" instead of
-- returning an empty list.
--
-- That distinction is the entire point of the guard. An empty list reads as
-- "nothing changed", which is the most misleading answer this data can give --
-- the budget probably did change, and the reason CPA moved is sitting in a
-- table nobody created yet.
--
-- WHY THIS MATTERS MORE THAN FATIGUE
--
-- Most of the time the answer to "why did CPA go up" is not creative fatigue.
-- It is: the budget tripled, the ad group was paused, the optimisation goal
-- changed, the creative was swapped. None of that was knowable before 046,
-- because store.upsert_campaigns / upsert_adsets / upsert_ad are all
-- `on conflict do update` -- the old value is overwritten with no trace.
--
-- Which is also why 046 is urgent rather than nice-to-have: every pull that
-- runs before it exists destroys a day of history that cannot be recovered.
-- Meta's API will not tell you what an ad group's budget was last Tuesday.
-- ---------------------------------------------------------------------------

begin;

do $$
begin
    if to_regclass('public.meta_structure_changes') is null then
        raise notice
            'SKIPPED: public.meta_structure_changes does not exist. Apply '
            'growth-engine/migrations/046_meta_change_log.sql, then re-run '
            'this file (delete its row from ads.schema_migrations first, or '
            'add it as 008). ads.changes will report "not available" until '
            'then, which is correct: nobody knows whether anything changed.';
        return;
    end if;

    -- Names, resolved once here rather than in every caller. A change row
    -- carries an entity id; a person reading it needs the ad group's name.
    execute $v$
        create or replace view ads.structure_change as
        select ch.id,
               ch.entity_type,
               ch.entity_id,
               coalesce(c.name, g.name, a.name) as entity_name,
               ch.brand_id,
               ch.account_id as platform_account_id,
               ch.field,
               ch.old_value,
               ch.new_value,
               ch.changed_at,
               case ch.entity_type
                   when 'campaign' then md5('meta:campaign:' || ch.entity_id)::uuid
                   when 'adset'    then md5('meta:ad_group:' || ch.entity_id)::uuid
                   else                 md5('meta:ad:'       || ch.entity_id)::uuid
               end as entity_key
          from public.meta_structure_changes ch
          left join public.meta_campaigns c
                 on ch.entity_type = 'campaign' and c.id = ch.entity_id
          left join public.meta_adsets g
                 on ch.entity_type = 'adset'    and g.id = ch.entity_id
          left join public.meta_ads a
                 on ch.entity_type = 'ad'       and a.id = ch.entity_id
    $v$;

    execute 'alter view ads.structure_change owner to ads_owner';
    execute 'grant select on ads.structure_change to ads_reader';

    execute 'drop policy if exists ads_owner_select on public.meta_structure_changes';
    execute 'create policy ads_owner_select on public.meta_structure_changes '
            'for select to ads_owner using (true)';

    execute 'grant select on public.meta_structure_changes to ads_owner';
end;
$$;

commit;
