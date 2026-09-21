-- ---------------------------------------------------------------------------
-- 010  The facet join, fixed -- and the join that was never collected.
--
-- Two problems, one cause: every consumer of ads.ad_facet joins it on ad_key
-- alone, and ad_key is only HALF of its primary key.
--
-- THE DOUBLE-COUNT
--
-- ads.ad_facet is keyed (ad_key, copy_hash), deliberately, and 004:256-259
-- says why: "rewrite the copy and the tag must be made again. A tag that
-- silently survives a rewrite describes an ad that no longer exists, and that
-- is precisely what poisons 'which angle wins'."
--
-- So the table ACCUMULATES one row per wording ever tagged. Every join written
--
--     join ads.ad_facet fa on fa.ad_key = f.ad_key
--
-- therefore multiplies that ad's spend by its number of historical tags. Two
-- places did this, and the second is the serious one:
--
--   004:399  angle_coverage.perf   -- sum(f.spend) inflated, so the angle
--                                     numbers are wrong in the one direction
--                                     the key exists to prevent
--   005:199  experiment_result     -- sum(m.spend) inflated, which corrupts
--                                     spend_sufficient AND metric_value. A
--                                     doubled spend can push an arm past its
--                                     PRE-REGISTERED minimum_spend_per_arm and
--                                     make an unreadable test look concluded.
--                                     That is worse than a wrong number; it is
--                                     a wrong number wearing the authority of
--                                     the mechanism designed to stop it.
--
-- intel/angles.py:101-104 (queue) already does it correctly, which is why the
-- untagged queue has been right while the reporting has been wrong.
--
-- THE JOIN THAT WAS NEVER COLLECTED
--
-- 004:318-320, about ads.inherited_facet: "This is what tracking.py has been
-- paying into since 025. Every utm_content of the form 'ad-a-v5' stamped at
-- approval was buying this join, and UNTIL NOW NOTHING COLLECTED."
--
-- Still true. inherited_facet is a view nothing reads. It resolves the angle
-- growth-engine actually wrote, without judgement, for every ad the engine
-- shipped. ads.facet_effective below collects it.
--
-- WHY A VIEW AND NOT A TRIGGER
--
-- A trigger cannot fire on a view, and a trigger on public.meta_ads would be
-- ads.* reaching into a public table's behaviour -- 001:22-25's one-way
-- invariant wearing a costume. A view consulted on the read path collects the
-- same join with no write path at all.
--
-- WHAT THIS FILE CANNOT FIX, AND NOBODY SHOULD TRY
--
-- ads.ad_copy groups by ad_id (002:249), so the warehouse holds exactly ONE
-- wording per ad -- the current one. public.meta_ad_texts is not effective-
-- dated. Spend that ran under a PREVIOUS wording is therefore attributed to
-- the current wording's angle, and no join can recover what it said at the
-- time because the text is gone.
--
-- That is a fact about the data, not a defect in these functions, and every
-- angle number in this schema sits on it. It is why stale_tag_ads is returned
-- below rather than quietly filtered: a reader is owed the size of the effect
-- even when nothing can be done about it.
-- ---------------------------------------------------------------------------

begin;

-- ---------------------------------------------------------------------------
-- One facet row per ad, by construction.
--
-- The join to ads.ad_copy is what enforces it: ad_copy holds one row per
-- ad_key, so requiring copy_hash to match leaves at most one facet standing --
-- the one describing the wording that is actually running.
-- ---------------------------------------------------------------------------
create view ads.ad_facet_current as
select fa.*
  from ads.ad_facet fa
  join ads.ad_copy c on c.ad_key = fa.ad_key
                    and c.copy_hash = fa.copy_hash;

comment on view ads.ad_facet_current is
    'ads.ad_facet restricted to the wording each ad is currently running. At '
    'most one row per ad_key, which is the property every aggregate needs and '
    'a join on ad_key alone does not give.';


-- ---------------------------------------------------------------------------
-- The same, for tags that describe wording that no longer runs.
--
-- Not filtered away silently. An angle number that improved because stale tags
-- dropped out of the denominator has not improved.
-- ---------------------------------------------------------------------------
create view ads.ad_facet_stale as
select fa.*,
       c.copy_hash as current_copy_hash
  from ads.ad_facet fa
  join ads.ad_copy c on c.ad_key = fa.ad_key
 where c.copy_hash <> fa.copy_hash;

comment on view ads.ad_facet_stale is
    'Filed tags describing wording an ad no longer runs. Surfaced so a reader '
    'can see how much spend left the angle numbers when the copy changed, '
    'rather than watching a denominator shrink for no visible reason.';


-- ---------------------------------------------------------------------------
-- What we know about each ad's angle, however we came to know it.
--
-- Two sources, and the precedence between them is the governance:
--
--   a filed tag        somebody judged it -- including source='operator',
--                      which a tagging pass may never overwrite (004:280-282)
--   an inherited angle  nobody judged it; it was read off the campaign_asset
--                      chain that the stamped tracked_url already resolved
--
-- The `not exists` clause makes the filed tag win. Always. An inherited angle
-- is what we fall back to when no human or agent has said otherwise, never
-- something that overrides one.
--
-- source and confidence are literals on the inherited branch because that
-- branch involves no judgement: it is 'inherited' and it is 'stated', by
-- definition of how it was derived. hook, offer and audience are NULL there
-- for the same reason -- inherited_facet carries a concept hook, which is a
-- different thing from ads.hook's controlled vocabulary, and conflating the
-- two would put an unvalidated string where a foreign key is expected.
-- ---------------------------------------------------------------------------
create view ads.facet_effective as
select ad_key,
       copy_hash,
       brand_id,
       angle_id,
       hook,
       offer,
       audience,
       source,
       confidence
  from ads.ad_facet_current
union all
select i.ad_key,
       i.copy_hash,
       i.brand_id,
       i.angle_id,
       null::text  as hook,
       null::text  as offer,
       null::text  as audience,
       'inherited' as source,
       'stated'    as confidence
  from ads.inherited_facet i
 where i.angle_id is not null
   and not exists (select 1
                     from ads.ad_facet_current f
                    where f.ad_key = i.ad_key);

comment on view ads.facet_effective is
    'Each ad''s angle, from a filed tag where one exists and from the '
    'campaign_asset chain otherwise. At most one row per ad_key. A filed tag '
    'always wins. This is the view every angle aggregate should read: it is '
    'the one that finally collects the join tracking.py has been paying into '
    'since growth-engine 025.';


-- ---------------------------------------------------------------------------
-- Coverage, rebuilt on facet_effective.
--
-- Dropped and recreated rather than replaced because the return table gains
-- four columns. The caller (intel/angles.py) selects explicitly and moves in
-- the same commit.
--
-- WHAT IS NEW, AND WHY EACH ONE EARNED ITS PLACE
--
--   definition      .claude/skills/ads-tagging/SKILL.md:30-31 instructs "tag
--                   against the definition, not against the name" and names
--                   `intel angles` as where to read it. That verb has never
--                   returned it. The central tagging instruction could not be
--                   followed with the verb it names.
--
--   inherited_ads   how many of this angle's ads were never judged by anyone.
--   tagged_ads      how many were. An angle whose number is 90% inherited and
--                   one that is 90% judged read identically today, and they
--                   are not the same claim.
--
--   stale_tag_ads   how many ads carry only a tag for wording they no longer
--                   run, and are therefore absent from the numbers above.
--
-- Deliberately NOT added here: optimization_goal. Ranking angles on CPA across
-- different optimization goals is the comparison intel/angles.py:45-46 warns
-- about, and half-answering it with a single column invites exactly that. The
-- honest version is a separate function that partitions by goal and returns
-- NULL for a rank it cannot compute. It belongs with angle ranking, not here.
-- ---------------------------------------------------------------------------
drop function if exists ads.angle_coverage(uuid, date, date, text, numeric);

create function ads.angle_coverage(
    p_brand_id     uuid,
    p_since        date,
    p_until        date,
    p_product_slug text default null,
    p_min_spend    numeric default 250
) returns table (
    angle_id      uuid,
    family        text,
    angle_slug    text,
    angle_name    text,
    definition    text,
    ads_run       integer,
    tagged_ads    integer,
    inherited_ads integer,
    stale_tag_ads integer,
    spend         numeric,
    conversions   bigint,
    cpa           numeric,
    first_run     date,
    last_run      date,
    state         text
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
begin
    return query
    with perf as (
        select fe.angle_id,
               count(distinct f.ad_key)::integer as ads_run,
               count(distinct f.ad_key) filter (
                   where fe.source <> 'inherited')::integer as tagged_ads,
               count(distinct f.ad_key) filter (
                   where fe.source =  'inherited')::integer as inherited_ads,
               sum(f.spend)                      as spend,
               sum(f.conversions)::bigint        as conversions,
               min(f.day)                        as first_run,
               max(f.day)                        as last_run
          from ads.fact_ad_day f
          join ads.facet_effective fe on fe.ad_key = f.ad_key
         where f.brand_id = p_brand_id
           and f.day between p_since and p_until
           and fe.angle_id is not null
         group by fe.angle_id
    ),
    -- Ads excluded from perf because every tag they carry describes wording
    -- that no longer runs. Counted against the angle those stale tags name,
    -- which is the only angle we can honestly attribute them to.
    stale as (
        select st.angle_id,
               count(distinct f.ad_key)::integer as stale_tag_ads
          from ads.fact_ad_day f
          join ads.ad_facet_stale st on st.ad_key = f.ad_key
         where f.brand_id = p_brand_id
           and f.day between p_since and p_until
           and st.angle_id is not null
           and not exists (select 1
                             from ads.facet_effective fe
                            where fe.ad_key = f.ad_key)
         group by st.angle_id
    )
    select a.id, a.family, a.slug, a.name, a.definition,
           coalesce(p.ads_run, 0),
           coalesce(p.tagged_ads, 0),
           coalesce(p.inherited_ads, 0),
           coalesce(s.stale_tag_ads, 0),
           coalesce(p.spend, 0),
           coalesce(p.conversions, 0),
           round(p.spend / nullif(p.conversions, 0), 4),
           p.first_run, p.last_run,
           case
               when p.angle_id is null                  then 'never_run'
               when coalesce(p.spend, 0) < p_min_spend  then 'under_spent'
               else                                          'tested'
           end
      from ads.angle a
      left join perf  p on p.angle_id = a.id
      left join stale s on s.angle_id = a.id
     where a.brand_id = p_brand_id
       and a.status = 'active'
       and (p_product_slug is null or a.product_slug = p_product_slug)
     order by coalesce(p.spend, 0) desc, a.name;
end;
$fn$;

comment on function ads.angle_coverage is
    'Per active angle: what ran, what it cost, and whether it has been tested '
    'at all. Reads ads.facet_effective, so an ad the engine shipped counts '
    'even when nobody filed a tag for it. never_run is the set difference this '
    'schema exists for; under_spent is the honest version of it. Returns '
    'inherited_ads and stale_tag_ads so a reader can tell how much of a number '
    'came from a judgement and how much spend is missing from it.';


-- ---------------------------------------------------------------------------
-- The experiment reader, on the same footing.
--
-- Only one line changes -- ads.ad_facet becomes ads.facet_effective in the
-- member CTE -- but it is the line that decides spend_sufficient, so the whole
-- function is restated rather than patched by hand at the far end of a file.
--
-- The angle-armed branch now also resolves for ads the engine shipped but
-- nobody tagged, which is the majority of them. An angle arm that silently
-- matched only hand-tagged ads was measuring the tagging pass, not the angle.
-- ---------------------------------------------------------------------------
create or replace function ads.experiment_result(p_experiment_id uuid)
returns table (
    label                  text,
    is_baseline            boolean,
    ads_in_arm             integer,
    spend                  numeric,
    impressions            bigint,
    link_clicks            bigint,
    conversions            bigint,
    rates                  jsonb,
    metric_value           numeric,
    spend_sufficient       boolean,
    effect_pct             numeric,
    improvement            boolean,
    effect_exceeds_minimum boolean,
    window_since           date,
    window_until           date,
    unsettled_days         integer
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
declare
    e         ads.experiment%rowtype;
    v_since   date;
    v_until   date;
    v_settled date;
begin
    select * into e from ads.experiment where id = p_experiment_id;
    if not found then
        raise exception 'no experiment %', p_experiment_id;
    end if;

    -- An experiment nobody has launched has no window and therefore no result.
    -- Returning zeros would read as "we tested it and nothing happened".
    if e.started_on is null then
        raise exception 'experiment % has no started_on, so it has no window to '
                        'measure', e.name
            using hint = 'started_on is set in the UI when the test actually '
                         'goes live; it is not part of the agent write shape.';
    end if;

    v_since := e.started_on;
    v_settled := ads.settled_through(e.brand_id, now());
    v_until := least(coalesce(e.ended_on, v_settled), coalesce(v_settled, e.ended_on));

    return query
    with member as (
        select arm.label,
               f.ad_key, f.day, f.spend, f.impressions, f.clicks,
               f.link_clicks, f.landing_page_views, f.conversions
          from ads.experiment_arm arm
          join ads.fact_ad_day f
            on f.brand_id = e.brand_id
           and f.day between v_since and v_until
          left join ads.facet_effective fe on fe.ad_key = f.ad_key
          left join ads.ad d               on d.ad_key  = f.ad_key
         where arm.experiment_id = p_experiment_id
           and (
                (arm.angle_id    is not null and fe.angle_id = arm.angle_id)
             or (arm.utm_content is not null and d.utm_content = arm.utm_content)
             or (arm.ad_keys     is not null and f.ad_key = any (arm.ad_keys))
           )
    ),
    agg as (
        select m.label,
               count(distinct m.ad_key)::integer as ads_in_arm,
               sum(m.spend)                      as spend,
               sum(m.impressions)::bigint        as impressions,
               sum(m.clicks)::bigint             as clicks,
               sum(m.link_clicks)::bigint        as link_clicks,
               sum(m.landing_page_views)::bigint as lpv,
               sum(m.conversions)::bigint        as conversions
          from member m
         group by m.label
    ),
    rated as (
        select a.*,
               ads.rate(a.impressions, a.clicks, a.link_clicks,
                        a.spend, a.conversions, a.lpv) as rates
          from agg a
    ),
    withmetric as (
        select r.*,
               (r.rates ->> e.primary_metric)::numeric as metric_value,
               row_number() over (order by r.label)    as rn
          from rated r
    ),
    base as (select metric_value as bv from withmetric where rn = 1)
    select w.label,
           (w.rn = 1),
           w.ads_in_arm,
           w.spend,
           w.impressions,
           w.link_clicks,
           w.conversions,
           w.rates,
           w.metric_value,
           (w.spend >= e.minimum_spend_per_arm),
           round(100.0 * (w.metric_value - b.bv) / nullif(b.bv, 0), 2),
           case when w.rn = 1 then null
                when e.primary_metric in ('ctr', 'link_ctr', 'conversion_rate')
                     then w.metric_value > b.bv
                else w.metric_value < b.bv end,
           case when w.rn = 1 then null
                else abs(100.0 * (w.metric_value - b.bv) / nullif(b.bv, 0))
                     >= e.minimum_effect_pct end,
           v_since,
           v_until,
           greatest(0, coalesce(v_until - v_settled, 0))::integer
      from withmetric w cross join base b
     order by w.rn;
end;
$fn$;

comment on function ads.experiment_result is
    'Per-arm totals, rates, spend sufficiency and effect against the first arm '
    'by label. Reports whether the PRE-REGISTERED minimum effect was cleared; '
    'does not name a winner. Raises rather than returning zeros for an '
    'experiment nobody launched. Reads ads.facet_effective, so an angle arm '
    'measures the angle rather than the tagging backlog.';


-- Grants: 006 already carries `alter default privileges in schema ads grant
-- select on tables to ads_reader` and the same for execute on functions, so
-- the three views and the two functions above are covered without a further
-- statement. Stated rather than assumed, because a missing grant here presents
-- as zero rows and not as an error.

commit;
