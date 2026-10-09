-- ---------------------------------------------------------------------------
-- 020  ads.untagged_spend, the same answer in a third of a second.
--
-- 013's ads.untagged_spend took 9-13 seconds per call for renegade (255 ads in
-- a 90-day window) while every other query on the Angles and Suggestions pages
-- takes about 0.3s. Nothing about the answer was wrong. The shape was.
--
-- WHY IT WAS SLOW
--
-- 013 classifies each ad with correlated EXISTS tests -- one per ad, against
-- ads.facet_effective, ads.inherited_facet, ads.ad_facet_current (twice) and
-- ads.ad_facet_stale. Every one of those views joins ads.ad_copy, and
-- ads.ad_copy (002:235) is not a table: it is a GROUP BY over every row of
-- public.meta_ad_texts, keyed on md5('meta:ad:' || ad_id)::uuid. A key that is
-- computed cannot be looked up through an index, so each probe for one ad
-- seq-scans all of meta_ad_texts (~6,500 rows, an md5 per row), sorts and
-- re-aggregates it -- about 7-9 ms. EXPLAIN (ANALYZE) of 013's body:
--
--     SubPlan 1 (facet_effective)   loops=255   ~7.1 ms each
--     SubPlan 3, 5 (the same view, planned again per filter)  ~7.1 ms each
--     SubPlan 15, 19 (ad_facet_stale)            ~8.6 ms each
--     Execution Time: 9966 ms      shared hit=156643
--
-- So the cost was ads x probes x one full re-aggregation of the ad copy, and
-- it grows with every ad imported and every line of copy stored.
--
-- WHAT CHANGED
--
-- Each membership set is computed ONCE, as a distinct set of ad_keys, and the
-- window's ads are left-joined to it. Four scans of the copy instead of a
-- thousand-odd, and the cost no longer multiplies by the number of ads that
-- ran: 96 ms of execution, ~0.33s round trip from this machine, measured
-- 2026-10-06.
--
-- WHAT DID NOT CHANGE, and it is the whole point
--
--   * the signature, the eight columns, their types and their order
--   * every definition: attributed = facet_effective resolves an angle;
--     inheritable = inherited_facet resolves an angle AND no current filed
--     tag; stale = a stale filed tag AND no current filed tag
--   * per ad first, then classified, then summed -- 013's guard against the
--     double count. Each set is `select distinct ad_key`, so a left join to it
--     yields at most one row per ad and cannot multiply the spend. `ak.ad_key
--     is not null` is exactly `exists (...)`: true or false, never NULL.
--   * one row always, zeros when nothing ran; the inverted-window refusal
--   * stable, security definer, the pinned search_path, the owner, the grant
--
-- The sets are not filtered to the brand. They do not need to be: they are
-- only ever joined to per_ad, which is, and an unfiltered distinct over a few
-- hundred facet rows costs less than the filter would. They are MATERIALIZED
-- so the planner cannot fold them back into a per-row probe, which is the
-- plan this file exists to get rid of.
--
-- Checked before it was written, without applying anything: the new body run
-- as a plain SELECT through the read pool against ads.untagged_spend() for the
-- windows the callers use (intel/angles.py label_coverage, 90 and 28 days;
-- intel/creative.py creative_pack via the Suggestions page and
-- scripts/suggest.py, 28 days; plus 7 and 365) -- identical rows every time,
-- and the per-ad flags identical for all 255 / 224 ads. On today's data the
-- inheritable and stale counts are zero (public.campaign_assets is empty, one
-- stale tag exists outside the window), so those two columns were compared
-- at zero; their definitions are carried over term for term.
--
-- No index was added and none is needed. An index on an expression of
-- public.meta_ad_texts would help 013's shape, not this one, and that table's
-- DDL is not in this repo.
--
-- `create or replace` keeps the function's owner and its grants. Both are
-- restated anyway, guarded as 019 guards them, because relying on what
-- replace happens to preserve is relying on something no line here says.
-- ---------------------------------------------------------------------------

begin;

create or replace function ads.untagged_spend(
    p_brand_id uuid,
    p_since    date,
    p_until    date
) returns table (
    total_spend       numeric,
    tagged_spend      numeric,
    untagged_ads      integer,
    untagged_spend    numeric,
    inheritable_ads   integer,
    inheritable_spend numeric,
    stale_tag_ads     integer,
    stale_tag_spend   numeric
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
#variable_conflict use_column
begin
    if p_since > p_until then
        raise exception 'window is inverted: since % is after until %', p_since, p_until;
    end if;

    return query
    -- Per ad first, then classified, then summed -- 013's order, for 013's
    -- reason. Each *_keys set is distinct on ad_key, so joining to it can
    -- never multiply an ad's spend.
    with per_ad as (
        select f.ad_key, sum(f.spend) as spend
          from ads.fact_ad_day f
         where f.brand_id = p_brand_id
           and f.day between p_since and p_until
         group by f.ad_key
    ),
    attributed_keys as materialized (
        select distinct fe.ad_key
          from ads.facet_effective fe
         where fe.angle_id is not null
    ),
    -- "Inheritable" as intel/angles.py's queue defines it: the campaign_asset
    -- chain resolves an angle and no filed tag describes the wording
    -- currently running. Door one, drainable with no judgement at all.
    inherited_keys as materialized (
        select distinct i.ad_key
          from ads.inherited_facet i
         where i.angle_id is not null
    ),
    current_keys as materialized (
        select distinct c.ad_key
          from ads.ad_facet_current c
    ),
    stale_keys as materialized (
        select distinct st.ad_key
          from ads.ad_facet_stale st
    ),
    flagged as (
        select pa.ad_key,
               pa.spend,
               (ak.ad_key is not null)                       as attributed,
               (ik.ad_key is not null and ck.ad_key is null) as inheritable,
               (sk.ad_key is not null and ck.ad_key is null) as stale
          from per_ad pa
          left join attributed_keys ak on ak.ad_key = pa.ad_key
          left join inherited_keys  ik on ik.ad_key = pa.ad_key
          left join current_keys    ck on ck.ad_key = pa.ad_key
          left join stale_keys      sk on sk.ad_key = pa.ad_key
    )
    select coalesce(sum(fl.spend), 0)::numeric,
           coalesce(sum(fl.spend) filter (where fl.attributed), 0)::numeric,
           coalesce(count(*) filter (where not fl.attributed), 0)::integer,
           coalesce(sum(fl.spend) filter (where not fl.attributed), 0)::numeric,
           coalesce(count(*) filter (where fl.inheritable), 0)::integer,
           coalesce(sum(fl.spend) filter (where fl.inheritable), 0)::numeric,
           coalesce(count(*) filter (where fl.stale), 0)::integer,
           coalesce(sum(fl.spend) filter (where fl.stale), 0)::numeric
      from flagged fl;
end;
$fn$;

comment on function ads.untagged_spend is
    'How much of a window''s spend the angle numbers do not describe, over ALL '
    'ads rather than the page of them a caller fetched -- intel/angles.py:122 '
    'sums this in Python across at most 50 rows, so it falls as tagging gets '
    'worse. tagged and untagged partition the spend; inheritable is a SUBSET '
    'OF TAGGED (an angle read off the campaign_asset chain that nobody judged) '
    'and stale overlaps both. Adding the four together means nothing. '
    '020: each membership set is built once and joined, not probed per ad.';

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_owner') then
        raise notice 'ads_owner does not exist; skipping ownership reassignment';
        return;
    end if;
    execute 'alter function ads.untagged_spend(uuid, date, date) '
            'owner to ads_owner';
end;
$$;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_reader') then
        raise notice 'ads_reader does not exist; skipping grant';
        return;
    end if;
    execute 'grant execute on function ads.untagged_spend(uuid, date, date) '
            'to ads_reader';
end;
$$;

commit;
