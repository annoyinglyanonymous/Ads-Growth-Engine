-- ---------------------------------------------------------------------------
-- 019  What is normal for this kind of campaign in this account.
--
-- Every reading so far judges a campaign against its OWN earlier windows
-- (017, 018). That stays the primary judgement. What it cannot say is whether
-- the campaign's level is usual for its kind: "CPL $44.67" reads one way
-- beside an M&A quality-lead band of $28-$36 and another beside a band of
-- $40-$55. These are the missing verbs (CLAUDE.md: "If a number you need is
-- not in a verb's output, that is a missing verb"):
--
--   ads.campaign_family_rule   the rules that put a campaign in a family and
--                              an audience temperature -- a TABLE, edited by
--                              a person with SQL, not a CASE in a function
--   ads.campaign_family        every campaign, its family and its audience
--   ads.family_campaign_weeks  the baseline sample: one row per qualifying
--                              campaign-week and metric, for auditing a band
--   ads.family_baseline        p25 / p50 / p75 per family, goal (and audience)
--   ads.campaign_vs_family     each single-goal campaign's current window
--                              against its family's band
--
-- A band is CONTEXT, never a verdict and never a target: "typical range",
-- not "benchmark". Nothing here concludes anything.
--
-- Everything in 003's header applies and is not recited: security definer with
-- search_path pinned, stable, no dynamic SQL, no read of current_date, nullif
-- on every denominator, NULL never 0, and every printed rate out of ads.rate.
-- 017's and 018's conventions apply too: every RETURNS TABLE column is also a
-- plpgsql variable, so every column reference is qualified by an alias, every
-- body opens with `#variable_conflict use_column`, and every RETURN QUERY
-- column carries an explicit cast so a type drift fails at create time, not on
-- a caller's first read.
--
-- ---------------------------------------------------------------------------
-- FAMILIES ARE RULES IN A TABLE, AND A PERSON EDITS THEM
--
-- Live campaign names are pipe-separated tokens ("... | M&A | Open Audience
-- | ..."). Two dimensions are read off them:
--
--   family     hiring     "Hiring Ads"
--              b_roll     "B-Roll"
--              m_and_a    "M&A" or "Book Roll"
--              franchise  "Franchise"
--              (no rule matches)  -> 'other'
--
--   audience   retargeting  "Retarget", "Custom List", "Custom Audience"
--              prospecting  "Open Audience", "Core States"
--              (no rule matches)  -> NULL, unknown, never guessed
--
-- Each rule is a case-insensitive regular expression (`~*`) matched against
-- the campaign name. Per brand and dimension, the matching rule with the
-- HIGHEST priority wins, and priorities are unique within a brand and
-- dimension so a tie cannot exist. Seeded so that hiring > b_roll > m_and_a >
-- franchise (a "Franchise | Hiring Ads" campaign is a hiring campaign: it is
-- recruiting, whatever it recruits for) and retargeting > prospecting (a
-- campaign naming both a custom list and open targeting is warm: the list is
-- the narrower, more deliberate choice).
--
-- A table rather than a CASE because naming conventions drift, and the person
-- who notices that "Agency Acquisition" is an M&A campaign should not need a
-- migration to say so. 004's trade for hooks and offers, made for the same
-- reason: adding a rule is an insert. Run as a person, against the database:
--
--     insert into ads.campaign_family_rule
--         (brand_id, dimension, pattern, value, priority)
--     select b.id, 'family', 'agency acquisition', 'm_and_a', 220
--       from ads.brand b
--      where b.slug = 'renegade';
--
--     -- see what every campaign now reads as:
--     select * from ads.campaign_family(
--         (select id from ads.brand where slug = 'renegade'));
--
-- A pattern that is not a valid regular expression is refused when it is
-- written (campaign_family_rule_pattern_compiles), not on the next read of
-- every band. Brands with no rules (agency height, today) read every
-- campaign as 'other' with audience NULL: one family, which is the honest
-- reading of "nobody has said what kinds there are".
--
-- ---------------------------------------------------------------------------
-- WHY THE UNIT IS THE CAMPAIGN-WEEK
--
-- A band needs many observations of "a campaign of this kind, over a stretch
-- long enough to have a CPA". One campaign's whole history is one
-- observation, and an account has a handful of campaigns per family: a p25 of
-- four numbers is not a range. A campaign-DAY has a CPA on a few days a month
-- and is mostly noise about which day a lead landed (metrics.trend's reason
-- for buckets). A campaign-week is the shortest stretch that usually carries
-- enough conversions to have a rate, it matches the 7-day window the triage
-- reads first, and 26 of them per campaign make a band from a family of three
-- campaigns.
--
-- The weeks are non-overlapping 7-day blocks ending at p_before and walking
-- back p_weeks (default 26): block k covers p_before - 7k - 6 .. p_before - 7k.
-- Each block's rates are ads.rate over the block's sums (sum-over-sum, 003),
-- so every value in a band is a figure ads.rate would print for that week.
--
-- A campaign-week counts for a metric only when it spent at least
-- p_min_spend (100), and additionally:
--
--   cpa, conversion_rate              at least p_min_conversions (5)
--   link_ctr, cpm, cost_per_link_click  at least p_min_impressions (1000)
--
-- 5 conversions is 017's 'directional' rung: below it a weekly CPA is a coin
-- toss, and a band built from coin tosses is wide for no reason. The floors
-- decide what is IN the sample; nothing is weighted, so a $5,000 week and a
-- $150 week each count once -- the question is "what does a week of this kind
-- of campaign usually look like", not "what did the family's dollars buy".
--
-- WHY THE CURRENT WINDOW IS EXCLUDED
--
-- campaign_vs_family passes p_before = p_cur_since - 1, so the band ends the
-- day before the window being judged starts, whatever its length. A band that
-- contains the week it is judging is pulled toward that week: a campaign
-- having its worst week ever would sit less far above a band it is part of,
-- and the comparison would understate exactly the cases it exists to catch.
-- It also keeps the band settled: for the settled pairs the current window
-- ends at the settled edge, so every baseline day is final.
--
-- WHY THE CAMPAIGN'S OWN HISTORY IS INCLUDED
--
-- The question is "what is normal for this kind of campaign in this account",
-- and this campaign's past weeks are some of the best evidence of that there
-- is -- often most of it, in a family of two or three campaigns. Excluding
-- them would make a long-running campaign's band thinner than a new one's,
-- and would turn the family band into "what the OTHER campaigns did", which
-- is a different question. The two judgements stay separate anyway: own
-- history against own history is 017's ladder, which remains the primary
-- reading; this is context beside it.
--
-- WHY SINGLE-GOAL ONLY, AND NEVER ACROSS GOALS
--
-- CPL is not comparable across optimization goals (CLAUDE.md), so every band
-- is per (family, optimization_goal), and a campaign is only ever read
-- against the band of its own goal. A campaign-week enters the sample only
-- when all of its spend that week ran under ONE known goal (018's single-goal
-- rule, applied to the week the rate is computed over): a mixed week's CPA is
-- a blend with no goal to belong to. In the current window the same rule
-- applies to the campaign: a multi-goal campaign gets no row, as in 018's
-- goal_impact. The goal is the CURRENT ad-group setting (017's header).
-- Nothing here orders anything across goals; there is no window function in
-- this file at all.
--
-- PERCENTILES: percentile_cont, ROUNDED LIKE ads.rate
--
-- p25 / p50 / p75 are percentile_cont: linear interpolation between the two
-- nearest observations, so a band from 9 weeks does not jump when the 10th
-- arrives. percentile_disc would return an observed week, which is concrete
-- but makes small bands lurch from one week's figure to another's.
-- percentile_cont takes double precision, so each value goes in as double and
-- comes back numeric rounded to 4 places -- the precision ads.rate prints
-- every rate at (003), and the precision `cur` is compared at. The inputs
-- are themselves 4-place figures, and the cast back to numeric keeps 15
-- significant digits, so the interpolation is exact at 4 places.
--
-- percentile on campaign_vs_family is the share of the band's campaign-weeks
-- whose value is <= cur (2dp): 100 means at or above every week in the band.
-- position is 'below' p25, 'above' p75, else 'within' -- on the PRINTED p25
-- and p75, so a reader can check it from the row. Both are raw sides of the
-- range, not good or bad: a CPA above the band and a link CTR above the band
-- are opposite news, and saying which is the caller's.
--
-- BAND LEVEL
--
-- Rows exist for (family, goal) over all audiences (audience NULL) and for
-- (family, goal, audience) wherever the audience is known. A campaign is read
-- against its audience-specific band when that band has at least
-- p_min_band_weeks (8) campaign-weeks for the metric ('family_audience'),
-- else against the family band when that one does ('family'), else against
-- nothing (band_level NULL, p25..p75 NULL). campaign_weeks is the chosen
-- band's sample, or the family band's when none qualified, so "a band of 5
-- weeks was too thin" stays visible.
--
-- CURRENCY
--
-- A money metric (cpa, cpm, cost_per_link_click) is in the sample only from a
-- week with one currency, and a money band whose weeks span two currencies is
-- not returned. family_baseline's `currency` says which currency a band is
-- in, and campaign_vs_family reads a money metric against a band only in the
-- campaign's own current currency. Rates (link_ctr, conversion_rate) have no
-- currency.
--
-- EVIDENCE
--
-- evidence_level on campaign_vs_family is 017's ladder on the CURRENT
-- window's conversions: insufficient under 5, directional from 5, moderate
-- from 20, strong from 50, capped at 'directional' when the window runs past
-- the settled edge or the edge is unknown. There is no prior side, so 017's
-- zero-conversion branch (k prior CPAs of spend) does not apply: a band is a
-- range, not a rate to expect.
-- ---------------------------------------------------------------------------

begin;

-- ---------------------------------------------------------------------------
-- 1. The rules.
-- ---------------------------------------------------------------------------
create table ads.campaign_family_rule (
    id          uuid primary key default gen_random_uuid(),
    brand_id    uuid not null references public.brands (id) on delete cascade,
    dimension   text not null check (dimension in ('family', 'audience')),
    -- A case-insensitive regular expression, matched with `~*` against the
    -- campaign name.
    pattern     text not null check (length(btrim(pattern)) > 0),
    -- What a match means: a family slug (hiring, b_roll, m_and_a, franchise)
    -- or an audience temperature (retargeting, prospecting).
    value       text not null check (value ~ '^[a-z0-9]+(_[a-z0-9]+)*$'),
    -- The highest-priority matching rule wins. Unique per brand and
    -- dimension, so which rule wins is never a matter of row order.
    priority    integer not null,
    created_at  timestamptz not null default now(),

    unique (brand_id, dimension, priority),

    -- Compiling the pattern against the empty string raises on an invalid
    -- regular expression, so a bad edit fails when it is written rather than
    -- on the next read of every band. Valid patterns make this true.
    constraint campaign_family_rule_pattern_compiles
        check (('' ~* pattern) is not null)
);

comment on table ads.campaign_family_rule is
    'How a campaign name maps to a family (hiring, b_roll, m_and_a, '
    'franchise; no match is other) and an audience temperature (retargeting, '
    'prospecting; no match is NULL). Each pattern is a case-insensitive '
    'regular expression; per brand and dimension the matching rule with the '
    'highest priority wins. Edited by a person with SQL; read through '
    'ads.campaign_family.';

comment on column ads.campaign_family_rule.priority is
    'Higher wins. Unique per (brand_id, dimension). Seeded hiring 400 > '
    'b_roll 300 > m_and_a 210/200 > franchise 100, and retargeting 230-210 > '
    'prospecting 110-100.';

-- The seed. One row per name token, so each pattern is a plain literal a
-- person can read, and m_and_a's two spellings are two rows.
insert into ads.campaign_family_rule (brand_id, dimension, pattern, value, priority)
select b.id, v.dimension, v.pattern, v.value, v.priority
  from ads.brand b
  cross join (values
    ('family',   'hiring ads',      'hiring',      400),
    ('family',   'b-roll',          'b_roll',      300),
    ('family',   'm&a',             'm_and_a',     210),
    ('family',   'book roll',       'm_and_a',     200),
    ('family',   'franchise',       'franchise',   100),
    ('audience', 'retarget',        'retargeting', 230),
    ('audience', 'custom list',     'retargeting', 220),
    ('audience', 'custom audience', 'retargeting', 210),
    ('audience', 'open audience',   'prospecting', 110),
    ('audience', 'core states',     'prospecting', 100)
  ) as v(dimension, pattern, value, priority)
 where b.slug = 'renegade'
    on conflict do nothing;

-- A seed that matched no brand inserts nothing and says nothing; say it.
do $$
begin
    if not exists (select 1
                     from ads.campaign_family_rule r
                     join ads.brand b on b.id = r.brand_id
                    where b.slug = 'renegade') then
        raise notice 'no brand with slug renegade; campaign_family_rule is unseeded '
                     'and every campaign reads as family other';
    end if;
end;
$$;


-- ---------------------------------------------------------------------------
-- 2. Every campaign, its family and its audience.
--
-- One lateral lookup per dimension, highest priority first. A campaign with
-- no name matches nothing (NULL ~* pattern is NULL) and is 'other'.
-- ---------------------------------------------------------------------------
create function ads.campaign_family(
    p_brand_id  uuid
) returns table (
    campaign_key   uuid,
    campaign_name  text,
    family         text,
    audience       text
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
#variable_conflict use_column
-- `campaign_key` is an OUT variable here and a column of ads.campaign. Every
-- reference below is qualified; this pragma only decides what happens if one
-- is ever missed.
begin
    if p_brand_id is null then
        raise exception 'a brand id is required';
    end if;

    return query
    select c.campaign_key::uuid,
           c.name::text,
           coalesce(fr.v, 'other')::text,
           ar.v::text
      from ads.campaign c
      left join lateral (
          select r.value as v
            from ads.campaign_family_rule r
           where r.brand_id = p_brand_id
             and r.dimension = 'family'
             and c.name ~* r.pattern
           order by r.priority desc
           limit 1
      ) fr on true
      left join lateral (
          select r.value as v
            from ads.campaign_family_rule r
           where r.brand_id = p_brand_id
             and r.dimension = 'audience'
             and c.name ~* r.pattern
           order by r.priority desc
           limit 1
      ) ar on true
     where c.brand_id = p_brand_id
     order by c.name, c.campaign_key;
end;
$fn$;

comment on function ads.campaign_family is
    'Every campaign of a brand with its family (the highest-priority matching '
    'ads.campaign_family_rule, else other) and audience temperature (else '
    'NULL). Patterns are case-insensitive regular expressions on the campaign '
    'name.';


-- ---------------------------------------------------------------------------
-- 3. The sample: one row per qualifying campaign-week and metric.
--
-- The one definition of which weeks count, so the band (family_baseline) and
-- the percentile against it (campaign_vs_family) are computed over the same
-- rows. Also the audit trail: a person who doubts a band can list its weeks.
-- ---------------------------------------------------------------------------
create function ads.family_campaign_weeks(
    p_brand_id           uuid,
    p_before             date,
    p_weeks              integer default 26,
    -- A campaign-week below this spend is not in the sample for any metric.
    p_min_spend          numeric default 100,
    -- ...below this many conversions, not for cpa or conversion_rate.
    p_min_conversions    integer default 5,
    -- ...below this many impressions, not for link_ctr, cpm or
    -- cost_per_link_click.
    p_min_impressions    integer default 1000
) returns table (
    campaign_key       uuid,
    campaign_name      text,
    family             text,
    audience           text,
    optimization_goal  text,
    week_since         date,
    week_until         date,
    metric             text,
    value              numeric,
    currency           text,
    spend              numeric,
    impressions        bigint,
    conversions        bigint
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
#variable_conflict use_column
-- `campaign_key`, `optimization_goal`, `spend`, `impressions`, `conversions`,
-- `currency` and `family` are OUT variables here and columns of the views
-- read below. Every reference is qualified.
declare
    v_since date;
begin
    if p_brand_id is null then
        raise exception 'a brand id is required';
    end if;

    if p_before is null then
        raise exception 'p_before is required: the last day of the baseline, '
                        'the day before the window it will be compared with'
            using hint = 'campaign_vs_family passes the day before its current window.';
    end if;

    if p_weeks is null or p_weeks < 1 or p_weeks > 260 then
        raise exception 'p_weeks must be between 1 and 260, got %', p_weeks;
    end if;

    if p_min_spend is null or p_min_spend < 0
       or p_min_conversions is null or p_min_conversions < 0
       or p_min_impressions is null or p_min_impressions < 0 then
        raise exception 'the sample floors must be zero or more, got spend %, '
                        'conversions %, impressions %',
            p_min_spend, p_min_conversions, p_min_impressions;
    end if;

    v_since := p_before - 7 * p_weeks + 1;

    return query
    with scan as (
        select d.campaign_key                       as ckey,
               g.optimization_goal                  as goal,
               f.currency                           as ccy,
               f.spend                              as sp,
               f.impressions                        as imp,
               f.clicks                             as clk,
               f.link_clicks                        as lc,
               f.landing_page_views                 as lpv,
               f.conversions                        as cv,
               -- Block 0 ends at p_before; block k ends 7k days earlier.
               ((p_before - f.day) / 7)::integer    as wk
          from ads.fact_ad_day f
          join ads.ad d            on d.ad_key = f.ad_key
          left join ads.ad_group g on g.ad_group_key = d.ad_group_key
         where f.brand_id = p_brand_id
           and f.day between v_since and p_before
           and d.campaign_key is not null
    ),
    wkagg as (
        select s.ckey                                         as ckey,
               s.wk                                           as wk,
               count(distinct s.goal)::integer                as n_goal,
               bool_or(s.goal is null)                        as any_null_goal,
               min(s.goal)                                    as goal,
               count(distinct s.ccy)::integer                 as n_ccy,
               min(s.ccy)                                     as ccy,
               coalesce(sum(s.sp), 0)::numeric                as sp,
               coalesce(sum(s.imp), 0)::bigint                as imp,
               coalesce(sum(s.clk), 0)::bigint                as clk,
               coalesce(sum(s.lc), 0)::bigint                 as lc,
               coalesce(sum(s.lpv), 0)::bigint                as lpv,
               coalesce(sum(s.cv), 0)::bigint                 as cv
          from scan s
         group by s.ckey, s.wk
    ),
    -- One known goal across the week's spend, and the spend floor.
    ok as (
        select w.*,
               ads.rate(w.imp, w.clk, w.lc, w.sp, w.cv, w.lpv) as rt
          from wkagg w
         where w.n_goal = 1
           and not w.any_null_goal
           and w.sp >= p_min_spend
    ),
    vals as (
        select o.ckey                                         as ckey,
               o.wk                                           as wk,
               o.goal                                         as goal,
               (case when o.n_ccy = 1 then o.ccy end)         as ccy,
               o.sp                                           as sp,
               o.imp                                          as imp,
               o.cv                                           as cv,
               m.ord                                          as ord,
               m.met                                          as met,
               m.val                                          as val
          from ok o
          cross join lateral (values
              (1, 'cpa',
               case when o.n_ccy = 1 and o.cv >= p_min_conversions
                    then (o.rt ->> 'cpa')::numeric end),
              (2, 'cost_per_link_click',
               case when o.n_ccy = 1 and o.imp >= p_min_impressions
                    then (o.rt ->> 'cost_per_link_click')::numeric end),
              (3, 'cpm',
               case when o.n_ccy = 1 and o.imp >= p_min_impressions
                    then (o.rt ->> 'cpm')::numeric end),
              (4, 'link_ctr',
               case when o.imp >= p_min_impressions
                    then (o.rt ->> 'link_ctr')::numeric end),
              (5, 'conversion_rate',
               case when o.cv >= p_min_conversions
                    then (o.rt ->> 'conversion_rate')::numeric end)
          ) as m(ord, met, val)
         -- NULL is "undefined this week" (ads.rate's nullif) or "below the
         -- floor": either way not an observation.
         where m.val is not null
    ),
    fam as (
        select cf.campaign_key                                as ckey,
               cf.campaign_name                               as cname,
               cf.family                                      as fam,
               cf.audience                                    as aud
          from ads.campaign_family(p_brand_id) cf
    )
    select v.ckey::uuid,
           fm.cname::text,
           coalesce(fm.fam, 'other')::text,
           fm.aud::text,
           v.goal::text,
           (p_before - 7 * v.wk - 6)::date,
           (p_before - 7 * v.wk)::date,
           v.met::text,
           v.val::numeric,
           v.ccy::text,
           v.sp::numeric,
           v.imp::bigint,
           v.cv::bigint
      from vals v
      -- LEFT: an ad whose campaign row is missing is still spend of some
      -- campaign, and reads as 'other' rather than vanishing.
      left join fam fm on fm.ckey = v.ckey
     order by v.ckey, v.wk, v.ord;
end;
$fn$;

comment on function ads.family_campaign_weeks is
    'The baseline sample behind ads.family_baseline: one row per campaign, '
    'non-overlapping 7-day block ending at p_before (going back p_weeks) and '
    'metric (cpa, cost_per_link_click, cpm, link_ctr, conversion_rate), with '
    'the week''s ads.rate value. Only single-goal campaign-weeks at or above '
    'p_min_spend; cpa and conversion_rate also need p_min_conversions, the '
    'others p_min_impressions; money metrics need one currency.';


-- ---------------------------------------------------------------------------
-- 4. The bands.
--
-- ONE pass over the sample, rolled up with GROUPING SETS to (family, goal,
-- metric) and (family, goal, metric, audience) in the same pass, for 017's
-- reason: two passes are two chances to read different rows.
-- ---------------------------------------------------------------------------
create function ads.family_baseline(
    p_brand_id           uuid,
    p_before             date,
    p_weeks              integer default 26,
    p_min_spend          numeric default 100,
    p_min_conversions    integer default 5,
    p_min_impressions    integer default 1000
) returns table (
    family             text,
    audience           text,
    optimization_goal  text,
    metric             text,
    p25                numeric,
    p50                numeric,
    p75                numeric,
    campaign_weeks     integer,
    campaigns          integer,
    since              date,
    until              date,
    currency           text
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
#variable_conflict use_column
-- `family`, `audience`, `optimization_goal`, `metric` and `currency` are OUT
-- variables here and columns of ads.family_campaign_weeks. Every reference
-- below is qualified.
begin
    -- Argument checks are family_campaign_weeks' own, and raise from there.
    return query
    with wk as (
        select fw.campaign_key                                as ckey,
               fw.family                                      as fam,
               fw.audience                                    as aud,
               fw.optimization_goal                           as gl,
               fw.metric                                      as met,
               fw.value                                       as val,
               fw.currency                                    as ccy,
               fw.week_since                                  as w_since,
               fw.week_until                                  as w_until
          from ads.family_campaign_weeks(p_brand_id, p_before, p_weeks,
                                         p_min_spend, p_min_conversions,
                                         p_min_impressions) fw
    ),
    agg as (
        select (grouping(w.aud) = 1)                                          as all_aud,
               w.fam                                                          as fam,
               w.aud                                                          as aud,
               w.gl                                                           as gl,
               w.met                                                          as met,
               percentile_cont(0.25) within group (order by w.val::double precision) as q25,
               percentile_cont(0.50) within group (order by w.val::double precision) as q50,
               percentile_cont(0.75) within group (order by w.val::double precision) as q75,
               count(*)::integer                                              as n_weeks,
               count(distinct w.ckey)::integer                                as n_camps,
               count(distinct w.ccy)::integer                                 as n_ccy,
               (count(w.ccy) = count(*))                                      as all_ccy_known,
               min(w.ccy)                                                     as ccy,
               min(w.w_since)                                                 as first_since,
               max(w.w_until)                                                 as last_until
          from wk w
         group by grouping sets ((w.fam, w.gl, w.met),
                                 (w.fam, w.gl, w.met, w.aud))
    )
    select a.fam::text,
           (case when a.all_aud then null else a.aud end)::text,
           a.gl::text,
           a.met::text,
           (round(a.q25::numeric, 4))::numeric,
           (round(a.q50::numeric, 4))::numeric,
           (round(a.q75::numeric, 4))::numeric,
           a.n_weeks::integer,
           a.n_camps::integer,
           a.first_since::date,
           a.last_until::date,
           (case when a.n_ccy = 1 and a.all_ccy_known then a.ccy end)::text
      from agg a
     -- The audience set also groups the weeks whose audience is unknown; that
     -- row is not an audience, and the all-audience row already holds them.
     where (a.all_aud or a.aud is not null)
       -- A money band across two currencies is not a band of anything.
       and (a.met not in ('cpa', 'cost_per_link_click', 'cpm') or a.n_ccy = 1)
     order by a.fam,
              a.gl,
              (case a.met when 'cpa'                 then 1
                          when 'cost_per_link_click' then 2
                          when 'cpm'                 then 3
                          when 'link_ctr'            then 4
                          when 'conversion_rate'     then 5 end),
              a.all_aud desc,
              a.aud;
end;
$fn$;

comment on function ads.family_baseline is
    'Typical ranges per campaign family and optimization goal (audience '
    'NULL), and per family, goal and known audience: p25 / p50 / p75 '
    '(percentile_cont, 4dp like ads.rate) of the weekly cpa, '
    'cost_per_link_click, cpm, link_ctr and conversion_rate of single-goal '
    'campaign-weeks ending on or before p_before, with the sample size, the '
    'campaigns in it, the span it covers and its currency. Context, never a '
    'target; never across goals.';


-- ---------------------------------------------------------------------------
-- 5. Each single-goal campaign's current window against its family's band.
--
-- Two reads of the sample -- the bands through family_baseline, the
-- percentile through family_campaign_weeks -- so that each figure has one
-- definition. Both run inside this statement's snapshot (a stable function
-- takes no new one), so they read the same rows.
-- ---------------------------------------------------------------------------
create function ads.campaign_vs_family(
    p_brand_id           uuid,
    p_cur_since          date,
    p_cur_until          date,
    p_settled_through    date,
    p_weeks              integer default 26,
    p_min_spend          numeric default 100,
    p_min_conversions    integer default 5,
    p_min_impressions    integer default 1000,
    -- A band needs this many campaign-weeks for the metric to be read against.
    p_min_band_weeks     integer default 8,
    -- Conversions in the current window: 017's ladder.
    p_conv_directional   integer default 5,
    p_conv_moderate      integer default 20,
    p_conv_strong        integer default 50
) returns table (
    campaign_key       uuid,
    campaign_name      text,
    family             text,
    audience           text,
    optimization_goal  text,
    metric             text,
    cur                numeric,
    p25                numeric,
    p50                numeric,
    p75                numeric,
    "position"         text,
    percentile         numeric,
    campaign_weeks     integer,
    band_level         text,
    evidence_level     text
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
#variable_conflict use_column
-- Most OUT variables here (`campaign_key`, `family`, `audience`,
-- `optimization_goal`, `metric`, `p25`..`p75`, `campaign_weeks`) are also
-- columns of the functions and views read below. Every reference is
-- qualified.
declare
    v_before date;
    v_capped boolean;
begin
    if p_cur_since is null or p_cur_until is null then
        raise exception 'both current window dates are required; take them from ads.analysis_windows';
    end if;

    if p_cur_since > p_cur_until then
        raise exception 'current window is inverted: since % is after until %', p_cur_since, p_cur_until;
    end if;

    if p_min_band_weeks is null or p_min_band_weeks < 1 then
        raise exception 'p_min_band_weeks must be at least 1, got %', p_min_band_weeks;
    end if;

    -- The band ends the day before the window it judges: the current
    -- window's own weeks are never in its baseline (header).
    v_before := p_cur_since - 1;
    -- Unknown settledness caps too (017).
    v_capped := coalesce(p_cur_until > p_settled_through, true);

    return query
    with scan as (
        select d.campaign_key                       as ckey,
               g.optimization_goal                  as goal,
               f.currency                           as ccy,
               f.spend                              as sp,
               f.impressions                        as imp,
               f.clicks                             as clk,
               f.link_clicks                        as lc,
               f.landing_page_views                 as lpv,
               f.conversions                        as cv
          from ads.fact_ad_day f
          join ads.ad d            on d.ad_key = f.ad_key
          left join ads.ad_group g on g.ad_group_key = d.ad_group_key
         where f.brand_id = p_brand_id
           and f.day between p_cur_since and p_cur_until
           and d.campaign_key is not null
    ),
    cur_agg as (
        select s.ckey                                         as ckey,
               count(distinct s.goal)::integer                as n_goal,
               bool_or(s.goal is null)                        as any_null_goal,
               min(s.goal)                                    as goal,
               count(distinct s.ccy)::integer                 as n_ccy,
               min(s.ccy)                                     as ccy,
               coalesce(sum(s.sp), 0)::numeric                as sp,
               coalesce(sum(s.imp), 0)::bigint                as imp,
               coalesce(sum(s.clk), 0)::bigint                as clk,
               coalesce(sum(s.lc), 0)::bigint                 as lc,
               coalesce(sum(s.lpv), 0)::bigint                as lpv,
               coalesce(sum(s.cv), 0)::bigint                 as cv
          from scan s
         group by s.ckey
    ),
    -- Single-goal campaigns only: a multi-goal campaign has no goal to be
    -- read against, as in 018's goal_impact.
    cs as (
        select c.*,
               ads.rate(c.imp, c.clk, c.lc, c.sp, c.cv, c.lpv) as rt
          from cur_agg c
         where c.n_goal = 1
           and not c.any_null_goal
    ),
    cm as (
        select c.ckey                                         as ckey,
               c.goal                                         as goal,
               (case when c.n_ccy = 1 then c.ccy end)         as ccy,
               c.sp                                           as sp,
               c.cv                                           as cv,
               m.ord                                          as ord,
               m.met                                          as met,
               m.is_money                                     as is_money,
               m.val                                          as cur_v
          from cs c
          cross join lateral (values
              (1, 'cpa', true,
               case when c.n_ccy = 1 then (c.rt ->> 'cpa')::numeric end),
              (2, 'cost_per_link_click', true,
               case when c.n_ccy = 1 then (c.rt ->> 'cost_per_link_click')::numeric end),
              (3, 'cpm', true,
               case when c.n_ccy = 1 then (c.rt ->> 'cpm')::numeric end),
              (4, 'link_ctr', false,
               (c.rt ->> 'link_ctr')::numeric),
              (5, 'conversion_rate', false,
               (c.rt ->> 'conversion_rate')::numeric)
          ) as m(ord, met, is_money, val)
    ),
    fam as (
        select cf.campaign_key                                as ckey,
               cf.campaign_name                               as cname,
               cf.family                                      as fam,
               cf.audience                                    as aud
          from ads.campaign_family(p_brand_id) cf
    ),
    bands as (
        select fb.family                                      as fam,
               fb.audience                                    as aud,
               fb.optimization_goal                           as gl,
               fb.metric                                      as met,
               fb.p25                                         as q25,
               fb.p50                                         as q50,
               fb.p75                                         as q75,
               fb.campaign_weeks                              as n_weeks,
               fb.currency                                    as ccy
          from ads.family_baseline(p_brand_id, v_before, p_weeks,
                                   p_min_spend, p_min_conversions,
                                   p_min_impressions) fb
    ),
    pick as (
        select x.*,
               coalesce(fm.fam, 'other')                      as fam,
               fm.cname                                       as cname,
               fm.aud                                         as aud,
               ba.q25 as a_q25, ba.q50 as a_q50, ba.q75 as a_q75, ba.n_weeks as a_n,
               bf.q25 as f_q25, bf.q50 as f_q50, bf.q75 as f_q75, bf.n_weeks as f_n,
               case
                   -- CASE, not AND: a money band is only read in the
                   -- campaign's own current currency.
                   when ba.n_weeks >= p_min_band_weeks
                        and (case when x.is_money then ba.ccy = x.ccy else true end)
                        then 'family_audience'
                   when bf.n_weeks >= p_min_band_weeks
                        and (case when x.is_money then bf.ccy = x.ccy else true end)
                        then 'family'
               end                                            as lvl
          from cm x
          left join fam fm   on fm.ckey = x.ckey
          left join bands bf on bf.fam = coalesce(fm.fam, 'other')
                            and bf.gl  = x.goal
                            and bf.met = x.met
                            and bf.aud is null
          left join bands ba on ba.fam = coalesce(fm.fam, 'other')
                            and ba.gl  = x.goal
                            and ba.met = x.met
                            and ba.aud = fm.aud
    ),
    sel as (
        select p.*,
               (case p.lvl when 'family_audience' then p.a_q25 when 'family' then p.f_q25 end) as q25,
               (case p.lvl when 'family_audience' then p.a_q50 when 'family' then p.f_q50 end) as q50,
               (case p.lvl when 'family_audience' then p.a_q75 when 'family' then p.f_q75 end) as q75,
               (case p.lvl when 'family_audience' then p.a_n
                           when 'family'          then p.f_n
                           else p.f_n end)                                                  as n_weeks
          from pick p
    ),
    wk as (
        select fw.family                                      as fam,
               fw.audience                                    as aud,
               fw.optimization_goal                           as gl,
               fw.metric                                      as met,
               fw.value                                       as val
          from ads.family_campaign_weeks(p_brand_id, v_before, p_weeks,
                                         p_min_spend, p_min_conversions,
                                         p_min_impressions) fw
    ),
    -- The share of the CHOSEN band's weeks at or below cur: the same rows
    -- family_baseline grouped into that band.
    pc as (
        select z.ckey                                                  as ckey,
               z.met                                                   as met,
               count(*)::integer                                       as n_all,
               (count(*) filter (where w.val <= z.cur_v))::integer     as n_le
          from sel z
          join wk w on w.fam = z.fam
                   and w.gl  = z.goal
                   and w.met = z.met
                   and (z.lvl = 'family' or w.aud = z.aud)
         where z.lvl is not null
           and z.cur_v is not null
         group by z.ckey, z.met
    )
    select s.ckey::uuid,
           s.cname::text,
           s.fam::text,
           s.aud::text,
           s.goal::text,
           s.met::text,
           s.cur_v::numeric,
           s.q25::numeric,
           s.q50::numeric,
           s.q75::numeric,
           (case
                when s.lvl is null or s.cur_v is null then null
                when s.cur_v < s.q25                  then 'below'
                when s.cur_v > s.q75                  then 'above'
                else                                       'within'
            end)::text,
           (case when s.lvl is not null and s.cur_v is not null
                 then round(100.0 * pc.n_le / nullif(pc.n_all, 0), 2) end)::numeric,
           s.n_weeks::integer,
           s.lvl::text,
           (case
                when v_capped and s.cv >= p_conv_moderate          then 'directional'
                when s.cv >= p_conv_strong                         then 'strong'
                when s.cv >= p_conv_moderate                       then 'moderate'
                when s.cv >= p_conv_directional                    then 'directional'
                else                                                    'insufficient'
            end)::text
      from sel s
      left join pc on pc.ckey = s.ckey and pc.met = s.met
     -- Campaigns by their current spend (dollars, so an order across goals is
     -- honest), each campaign's metrics in a fixed order.
     order by s.sp desc,
              s.ckey,
              s.ord;
end;
$fn$;

comment on function ads.campaign_vs_family is
    'Each single-goal campaign''s current window (ads.rate cpa, '
    'cost_per_link_click, cpm, link_ctr, conversion_rate) against the typical '
    'range of its family and goal (ads.family_baseline, ending the day before '
    'the window): p25 / p50 / p75 of the chosen band, position below / within '
    '/ above p25..p75, the percentile of baseline campaign-weeks at or below '
    'the current value, the band''s sample size and level (family_audience, '
    'family, or NULL when no band has p_min_band_weeks), and 017''s evidence '
    'level on the current conversions. Context, never a verdict.';


-- Owner and grant, 016's to 018's pattern and for 013's reasons: ads_migrate
-- applies as postgres, so default privileges never fire, and a definer
-- function left owned by postgres reads AROUND RLS where every other one reads
-- THROUGH it. Full signatures, defaults included, because a function is
-- identified by its argument types. The rule table is owned by ads_owner like
-- every ads table, and the reader may select from it (006's taxonomy tables
-- set the precedent); nothing grants it a write.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_owner') then
        raise notice 'ads_owner does not exist; skipping ownership reassignment';
        return;
    end if;
    execute 'alter table ads.campaign_family_rule owner to ads_owner';
    execute 'alter function ads.campaign_family(uuid) '
            'owner to ads_owner';
    execute 'alter function ads.family_campaign_weeks(uuid, date, integer, '
            'numeric, integer, integer) '
            'owner to ads_owner';
    execute 'alter function ads.family_baseline(uuid, date, integer, '
            'numeric, integer, integer) '
            'owner to ads_owner';
    execute 'alter function ads.campaign_vs_family(uuid, date, date, date, integer, '
            'numeric, integer, integer, integer, integer, integer, integer) '
            'owner to ads_owner';
end;
$$;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_reader') then
        raise notice 'ads_reader does not exist; skipping grant';
        return;
    end if;
    execute 'grant select on ads.campaign_family_rule to ads_reader';
    execute 'grant execute on function ads.campaign_family(uuid) '
            'to ads_reader';
    execute 'grant execute on function ads.family_campaign_weeks(uuid, date, integer, '
            'numeric, integer, integer) '
            'to ads_reader';
    execute 'grant execute on function ads.family_baseline(uuid, date, integer, '
            'numeric, integer, integer) '
            'to ads_reader';
    execute 'grant execute on function ads.campaign_vs_family(uuid, date, date, date, integer, '
            'numeric, integer, integer, integer, integer, integer, integer) '
            'to ads_reader';
end;
$$;

commit;
