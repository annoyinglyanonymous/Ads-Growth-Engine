-- ---------------------------------------------------------------------------
-- 003  The metrics layer.
--
-- The rule this file exists to enforce: NOBODY COMPUTES A RATE TWICE.
--
-- Not the dashboard, not the agent, not a future caller in Python. If a rate is
-- worth showing it is defined here, once, and every surface reads the same
-- number -- because the failure mode of two definitions is not an error, it is
-- a dashboard card and an agent's sentence that quietly disagree, and whichever
-- one somebody happens to be looking at is the one they act on.
--
-- THREE PROPERTIES EVERY FUNCTION BELOW HAS, AND WHY
--
-- 1. SECURITY DEFINER, with search_path pinned.
--    Every public.meta_* table has RLS enabled with zero policies (042:557).
--    That has been harmless because growth-engine connects as postgres, which
--    bypasses RLS. ads_reader does not. A SECURITY INVOKER function called by a
--    non-BYPASSRLS role returns ZERO ROWS -- not an error, not a permission
--    denial, just an empty result and a dashboard reading $0.00. It is the
--    single most confusing failure available here, so it is designed out.
--
--    The corollary is a hard rule: NO DYNAMIC SQL IN THIS FILE. A definer
--    function that builds SQL from an argument is a privilege escalation with
--    extra steps. That is why p_level is a static CASE over the grouping key
--    and never an interpolated column name.
--
-- 2. NO FUNCTION READS current_date.
--    Every window boundary is a parameter. growth-engine's store.overview_rows
--    reads current_date, which is the same family of bug as the UTC/account
--    timezone mismatch in pull.py -- a "day" that means different things in
--    different places. Parameters also make every function testable against a
--    fixture and make "as of last Friday" free.
--
-- 3. RATES ARE SUM-OVER-SUM, NEVER AVG-OF-DAILY.
--    See ads.rate.
-- ---------------------------------------------------------------------------

begin;

-- ---------------------------------------------------------------------------
-- Every rate in this schema, defined once.
--
-- Each is sum(numerator) / sum(denominator) FOR THE WINDOW -- never an average
-- of daily ratios. An average of ratios weights a day with one lead as heavily
-- as a day with fifty; growth-engine's store.overview_rows:432 documents the
-- same trap in the one place it aggregates today.
--
-- nullif() on every denominator: a rate with a zero denominator is UNDEFINED,
-- and NULL is how that is spelled. 042 argues this for cost_per_lead -- "NULL,
-- never 0, when leads = 0" -- because a zero sorts to the top of a
-- cheapest-CPA column and a null does not. It is true of all of them.
--
-- Returns jsonb rather than a composite type so that adding a rate is a change
-- to this function alone, not to the return signature of every function that
-- surfaces it.
-- ---------------------------------------------------------------------------
create function ads.rate(
    p_impressions        bigint,
    p_clicks             bigint,
    p_link_clicks        bigint,
    p_spend              numeric,
    p_conversions        bigint,
    p_landing_page_views bigint
) returns jsonb
language sql
immutable
parallel safe
as $fn$
    select jsonb_strip_nulls(jsonb_build_object(
        'ctr',                 round(100.0 * p_clicks      / nullif(p_impressions, 0), 4),
        'link_ctr',            round(100.0 * p_link_clicks / nullif(p_impressions, 0), 4),
        'cpm',                 round(1000.0 * p_spend      / nullif(p_impressions, 0), 4),
        'cpc',                 round(p_spend / nullif(p_clicks, 0), 4),
        'cost_per_link_click', round(p_spend / nullif(p_link_clicks, 0), 4),
        'cpa',                 round(p_spend / nullif(p_conversions, 0), 4),
        'conversion_rate',     round(100.0 * p_conversions / nullif(p_link_clicks, 0), 4),
        'lp_view_rate',        round(100.0 * p_landing_page_views / nullif(p_link_clicks, 0), 4)
    ));
$fn$;

comment on function ads.rate is
    'Every rate in this schema, defined once. Each is sum-over-sum for the '
    'window, never an average of daily ratios. nullif everywhere: an undefined '
    'rate is NULL, never 0, so it does not sort to the top of a "cheapest" '
    'column (042 makes the same argument for cost_per_lead).';


-- ---------------------------------------------------------------------------
-- How far back the numbers have stopped moving.
--
-- Two separate reasons a recent day is not final, and this function folds both:
--
--   1. Meta restates attributed conversions for several days after the fact.
--      meta_ads/pull.py re-reads the last 3 days for exactly this reason
--      (INSIGHTS_RESTATEMENT_DAYS), and the (ad_id, date) primary key makes
--      that a rewrite rather than a duplicate.
--
--   2. An insights date is a day in the AD ACCOUNT's timezone, not UTC
--      (042 says so on meta_ad_accounts.timezone_name). pull.py currently
--      computes its window end from datetime.now(timezone.utc), so for a US
--      account it routinely stores a partial final day.
--
-- Every read verb reports unsettled_days against this, and the agent is told to
-- say so. "CPA rose this week" computed across a partial day is the number-one
-- false alarm this layer exists to prevent -- and by taking the account's own
-- timezone rather than trusting the importer, this stays correct whether or not
-- pull.py has been fixed.
--
-- min() across accounts, not max(): if one account is behind, the brand is
-- behind. Settled means settled everywhere.
-- ---------------------------------------------------------------------------
create function ads.settled_through(p_brand_id uuid, p_asof timestamptz)
returns date
language sql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
    select min(((p_asof at time zone coalesce(acc.timezone_name, 'UTC'))::date) - 3)
      from public.meta_ad_accounts acc
     where acc.brand_id = p_brand_id
       and acc.active;
$fn$;

comment on function ads.settled_through is
    'The last day whose numbers have stopped moving, in the account''s own '
    'timezone, minus Meta''s 3-day restatement horizon. NULL when the brand has '
    'no active ad account. Correct whether or not pull.py''s UTC window bug is '
    'fixed, because it reads the account timezone rather than trusting the '
    'importer.';


-- ---------------------------------------------------------------------------
-- The windowed rollup. Everything else in this schema is built on it.
--
-- p_level is validated and then used in a static CASE -- never interpolated.
-- See the SECURITY DEFINER note in the file header for why that matters.
--
-- currencies is returned as a COUNT rather than assumed. 042 warns an account's
-- currency can change, and two accounts under one brand can differ. A caller
-- that prints a total without checking this is adding dollars to pounds; the
-- count is what lets it refuse instead.
-- ---------------------------------------------------------------------------
create function ads.window_metrics(
    p_brand_id uuid,
    p_since    date,
    p_until    date,
    p_level    text default 'ad'
) returns table (
    level              text,
    entity_key         uuid,
    platform_id        text,
    entity_name        text,
    optimization_goal  text,
    days               integer,
    first_day          date,
    last_day           date,
    impressions        bigint,
    clicks             bigint,
    link_clicks        bigint,
    landing_page_views bigint,
    spend              numeric,
    conversions        bigint,
    reach_best_day     bigint,
    frequency_avg      numeric,
    currency           text,
    currencies         integer,
    rates              jsonb
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
begin
    if p_level not in ('ad', 'ad_group', 'campaign') then
        raise exception 'unknown level %, expected ad, ad_group or campaign', p_level
            using hint = 'Meta''s "adset" is called ad_group here; the word adset '
                         'appears nowhere in the ads schema.';
    end if;

    if p_since > p_until then
        raise exception 'window is inverted: since % is after until %', p_since, p_until;
    end if;

    return query
    with scoped as (
        select case p_level
                   when 'campaign' then d.campaign_key
                   when 'ad_group' then d.ad_group_key
                   else d.ad_key
               end as entity_key,
               case p_level
                   when 'campaign' then c.platform_campaign_id
                   when 'ad_group' then g.platform_ad_group_id
                   else d.platform_ad_id
               end as platform_id,
               case p_level
                   when 'campaign' then c.name
                   when 'ad_group' then g.name
                   else d.name
               end as entity_name,
               -- Only meaningful at ad and ad_group level. At campaign level a
               -- single campaign can hold ad groups with different goals, and
               -- min() would silently pick one -- so it is NULL there, which is
               -- the honest answer and the one that stops a caller comparing
               -- CPAs that are not comparable.
               case p_level when 'campaign' then null else g.optimization_goal end
                   as optimization_goal,
               f.*
          from ads.fact_ad_day f
          join ads.ad d        on d.ad_key       = f.ad_key
          left join ads.ad_group g on g.ad_group_key = d.ad_group_key
          left join ads.campaign c on c.campaign_key = d.campaign_key
         where f.brand_id = p_brand_id
           and f.day between p_since and p_until
    )
    select p_level,
           s.entity_key,
           min(s.platform_id),
           min(s.entity_name),
           min(s.optimization_goal),
           count(distinct s.day)::integer,
           min(s.day),
           max(s.day),
           sum(s.impressions)::bigint,
           sum(s.clicks)::bigint,
           sum(s.link_clicks)::bigint,
           sum(s.landing_page_views)::bigint,
           sum(s.spend),
           sum(s.conversions)::bigint,
           -- NOT sum(). Daily reach is deduplicated within the day, so a sum
           -- counts the same person once per day. The best single day is a
           -- number that is actually true; a window reach is not derivable
           -- from these rows at all. See 002.
           max(s.reach_this_day)::bigint,
           round(avg(s.frequency_this_day), 4),
           min(s.currency),
           count(distinct s.currency)::integer,
           ads.rate(sum(s.impressions)::bigint,
                    sum(s.clicks)::bigint,
                    sum(s.link_clicks)::bigint,
                    sum(s.spend),
                    sum(s.conversions)::bigint,
                    sum(s.landing_page_views)::bigint)
      from scoped s
     group by s.entity_key;
end;
$fn$;

comment on function ads.window_metrics is
    'Totals and rates for one window at ad, ad_group or campaign level. '
    'reach_best_day is max() and not sum() -- daily reach cannot be summed. '
    'currencies is a count so a caller can refuse to print a mixed-currency '
    'total rather than adding dollars to pounds.';


-- ---------------------------------------------------------------------------
-- Period over period.
--
-- The prior window is CONTIGUOUS and EQUAL-LENGTH, computed from the current
-- one rather than passed in. A 14-day window compared against a 7-day prior
-- produces a spend delta of about +100% that is pure arithmetic artefact, and
-- it is an easy mistake to make from a calling convention that accepts four
-- dates.
--
-- appeared / disappeared are first-class results, not a missing row. An ad that
-- launched this week has no prior CPA, and "CPA went up 0% -> infinity" is a
-- worse answer than "this is new".
-- ---------------------------------------------------------------------------
create function ads.compare(
    p_brand_id uuid,
    p_since    date,
    p_until    date,
    p_level    text default 'ad'
) returns table (
    level        text,
    entity_key   uuid,
    entity_name  text,
    prior_since  date,
    prior_until  date,
    current_m    jsonb,
    prior_m      jsonb,
    delta        jsonb,
    pct          jsonb,
    appeared     boolean,
    disappeared  boolean
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
declare
    v_len   integer := (p_until - p_since) + 1;
    v_p_until date  := p_since - 1;
    v_p_since date  := p_since - v_len;
begin
    return query
    with cur as (select * from ads.window_metrics(p_brand_id, p_since, p_until, p_level)),
         pri as (select * from ads.window_metrics(p_brand_id, v_p_since, v_p_until, p_level)),
         j as (
            select coalesce(c.entity_key, p.entity_key)   as entity_key,
                   coalesce(c.entity_name, p.entity_name) as entity_name,
                   c.spend as c_spend, p.spend as p_spend,
                   c.impressions as c_imp, p.impressions as p_imp,
                   c.clicks as c_clicks, p.clicks as p_clicks,
                   c.link_clicks as c_lc, p.link_clicks as p_lc,
                   c.conversions as c_conv, p.conversions as p_conv,
                   c.rates as c_rates, p.rates as p_rates,
                   (p.entity_key is null) as appeared,
                   (c.entity_key is null) as disappeared
              from cur c
              full outer join pri p on p.entity_key = c.entity_key
         )
    select p_level,
           j.entity_key,
           j.entity_name,
           v_p_since,
           v_p_until,
           jsonb_build_object('spend', j.c_spend, 'impressions', j.c_imp,
                              'clicks', j.c_clicks, 'link_clicks', j.c_lc,
                              'conversions', j.c_conv, 'rates', j.c_rates),
           jsonb_build_object('spend', j.p_spend, 'impressions', j.p_imp,
                              'clicks', j.p_clicks, 'link_clicks', j.p_lc,
                              'conversions', j.p_conv, 'rates', j.p_rates),
           jsonb_strip_nulls(jsonb_build_object(
               'spend',       coalesce(j.c_spend, 0) - coalesce(j.p_spend, 0),
               'impressions', coalesce(j.c_imp, 0)   - coalesce(j.p_imp, 0),
               'clicks',      coalesce(j.c_clicks, 0)- coalesce(j.p_clicks, 0),
               'link_clicks', coalesce(j.c_lc, 0)    - coalesce(j.p_lc, 0),
               'conversions', coalesce(j.c_conv, 0)  - coalesce(j.p_conv, 0),
               'cpa',    (j.c_rates ->> 'cpa')::numeric      - (j.p_rates ->> 'cpa')::numeric,
               'cpm',    (j.c_rates ->> 'cpm')::numeric      - (j.p_rates ->> 'cpm')::numeric,
               'link_ctr', (j.c_rates ->> 'link_ctr')::numeric - (j.p_rates ->> 'link_ctr')::numeric
           )),
           -- Percent is NULL where the prior is zero or absent, rather than
           -- infinity or an arbitrary 100%. A caller that wants to say "new"
           -- has the appeared flag for it.
           jsonb_strip_nulls(jsonb_build_object(
               'spend',       round(100.0 * (j.c_spend - j.p_spend) / nullif(j.p_spend, 0), 2),
               'conversions', round(100.0 * (j.c_conv  - j.p_conv)  / nullif(j.p_conv, 0), 2),
               'cpa',         round(100.0 * ((j.c_rates ->> 'cpa')::numeric - (j.p_rates ->> 'cpa')::numeric)
                                    / nullif((j.p_rates ->> 'cpa')::numeric, 0), 2),
               'link_ctr',    round(100.0 * ((j.c_rates ->> 'link_ctr')::numeric - (j.p_rates ->> 'link_ctr')::numeric)
                                    / nullif((j.p_rates ->> 'link_ctr')::numeric, 0), 2)
           )),
           j.appeared,
           j.disappeared
      from j;
end;
$fn$;

comment on function ads.compare is
    'Current window against the contiguous, equal-length window before it. The '
    'prior window is derived, not passed, because a 14-day window against a '
    '7-day prior is a delta that is pure arithmetic artefact. appeared and '
    'disappeared are results, not missing rows.';


-- ---------------------------------------------------------------------------
-- Why the brand's CPA moved.
--
-- "Why did CPA go up?" is almost never one thing. It is some mixture of
--   (a) a creative got more expensive, and
--   (b) budget moved toward a creative that was already more expensive,
-- and telling them apart is the difference between "pause this ad" and
-- "rebalance the budget". They are different actions, so the number has to
-- separate them.
--
-- THE DECOMPOSITION, and why total_effect always sums but the split does not.
--
-- Brand CPA = S / C. Each ad's contribution to it is spend_i / C, so
--
--     total_effect_i = spend_i(current)/C(current) - spend_i(prior)/C(prior)
--
-- and those sum EXACTLY to the brand CPA change, for every ad, including ads
-- that converted nothing. That identity is what makes this worth printing.
--
-- Where an ad converted in BOTH windows, spend_i/C = cpa_i * w_i with
-- w_i = conv_i / C, and the contribution splits cleanly:
--
--     rate_effect_i = w_i(prior)   * (cpa_i(current) - cpa_i(prior))
--     mix_effect_i  = (w_i(current) - w_i(prior)) * cpa_i(current)
--     rate + mix    = total_effect_i
--
-- Where an ad converted in only one window, or neither, cpa_i is undefined on
-- one side and the split is not defined either. Those rows return total_effect
-- with rate_effect and mix_effect NULL and a `reason` naming which case it is.
-- That is deliberate: inventing a zero there would let the two columns silently
-- stop summing to the total, which is the one property this function has.
-- ---------------------------------------------------------------------------
create function ads.cpa_bridge(
    p_brand_id uuid,
    p_since    date,
    p_until    date
) returns table (
    ad_key        uuid,
    entity_name   text,
    spend_current numeric,
    spend_prior   numeric,
    conv_current  bigint,
    conv_prior    bigint,
    cpa_current   numeric,
    cpa_prior     numeric,
    rate_effect   numeric,
    mix_effect    numeric,
    total_effect  numeric,
    reason        text
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
declare
    v_len     integer := (p_until - p_since) + 1;
    v_p_until date    := p_since - 1;
    v_p_since date    := p_since - v_len;
begin
    return query
    with cur as (select * from ads.window_metrics(p_brand_id, p_since, p_until, 'ad')),
         pri as (select * from ads.window_metrics(p_brand_id, v_p_since, v_p_until, 'ad')),
         tot as (
            select (select coalesce(sum(conversions), 0) from cur) as c_conv,
                   (select coalesce(sum(conversions), 0) from pri) as p_conv
         ),
         j as (
            select coalesce(c.entity_key, p.entity_key)   as k,
                   coalesce(c.entity_name, p.entity_name) as nm,
                   coalesce(c.spend, 0)       as cs,
                   coalesce(p.spend, 0)       as ps,
                   coalesce(c.conversions, 0) as cc,
                   coalesce(p.conversions, 0) as pc
              from cur c
              full outer join pri p on p.entity_key = c.entity_key
         )
    select j.k,
           j.nm,
           j.cs,
           j.ps,
           j.cc,
           j.pc,
           round(j.cs / nullif(j.cc, 0), 4),
           round(j.ps / nullif(j.pc, 0), 4),
           case when j.cc > 0 and j.pc > 0
                then round((j.pc::numeric / nullif(t.p_conv, 0))
                           * ((j.cs / j.cc) - (j.ps / j.pc)), 4) end,
           case when j.cc > 0 and j.pc > 0
                then round(((j.cc::numeric / nullif(t.c_conv, 0))
                            - (j.pc::numeric / nullif(t.p_conv, 0)))
                           * (j.cs / j.cc), 4) end,
           round(j.cs / nullif(t.c_conv, 0) - j.ps / nullif(t.p_conv, 0), 4),
           case when j.cc > 0 and j.pc > 0            then 'attributable'
                when j.cc = 0 and j.pc = 0            then 'no_conversions_either_window'
                when j.pc = 0                         then 'no_conversions_prior'
                else                                       'no_conversions_current' end
      from j cross join tot t
     order by abs(coalesce(j.cs / nullif(t.c_conv, 0) - j.ps / nullif(t.p_conv, 0), 0)) desc;
end;
$fn$;

comment on function ads.cpa_bridge is
    'Splits a brand CPA change into per-ad rate effect (this creative got more '
    'expensive) and mix effect (budget moved toward an expensive creative). '
    'total_effect sums exactly to the brand delta across all rows; rate and mix '
    'are NULL where an ad converted in only one window, because the split is '
    'undefined there and a fabricated zero would break the sum.';


-- ---------------------------------------------------------------------------
-- Fatigue.
--
-- Five named booleans and a count, not a weighted float. A weighted score
-- invites an argument about the weights and hides which fact fired; a count of
-- five things you can read out loud does not. The caller shows the components.
--
-- ON FREQUENCY, because this is the part most likely to be misread:
-- meta_ad_insights.frequency is impressions / THAT DAY's reach, so it sits near
-- 1.0-1.3 for almost everything. It is not cumulative frequency -- the number
-- people mean by "frequency is up to 4" -- and cumulative frequency is not in
-- this database, because daily reach cannot be summed (see 002). So the test
-- here is for ACCELERATION, with a small threshold, and the caller must say
-- "daily frequency", never "frequency".
--
-- `confident` matters more than `score`. Reporting "3/5" for an ad with $30 of
-- spend is 042's zero-cost-per-lead failure in a new costume: a number that is
-- arithmetically correct and completely meaningless. Unconfident rows are still
-- returned -- suppressing them entirely would hide a real problem on a small
-- budget -- but the flag is there so the default answer can collapse them into
-- a count.
-- ---------------------------------------------------------------------------
create function ads.fatigue(
    p_brand_id  uuid,
    p_asof      date,
    p_window    integer default 7,
    p_min_spend numeric default 100
) returns table (
    ad_key             uuid,
    entity_name        text,
    optimization_goal  text,
    recent_since       date,
    recent_until       date,
    days_observed      integer,
    spend_recent       numeric,
    impressions_recent bigint,
    confident          boolean,
    link_ctr_decline   boolean,
    cpm_rise           boolean,
    cpa_rise           boolean,
    frequency_rise     boolean,
    ranking_drop       boolean,
    score              integer,
    recent             jsonb,
    prior              jsonb
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
declare
    v_r_until date := p_asof;
    v_r_since date := p_asof - (p_window - 1);
    v_p_until date := p_asof - p_window;
    v_p_since date := p_asof - (2 * p_window) + 1;
begin
    if p_window < 2 then
        raise exception 'p_window must be at least 2 days, got %', p_window;
    end if;

    return query
    with r as (select * from ads.window_metrics(p_brand_id, v_r_since, v_r_until, 'ad')),
         p as (select * from ads.window_metrics(p_brand_id, v_p_since, v_p_until, 'ad')),
         -- Meta's three relative rankings, worst-to-best as an ordinal so a
         -- band move is comparable. NULL means "not enough impressions to
         -- grade" and is NEVER read as a drop -- that is why this maps to NULL
         -- rather than to 0.
         rk as (
            select f.ad_key,
                   max(case f.quality_ranking
                       when 'BELOW_AVERAGE_10' then 1 when 'BELOW_AVERAGE_20' then 2
                       when 'BELOW_AVERAGE_35' then 3 when 'AVERAGE' then 4
                       when 'ABOVE_AVERAGE' then 5 end)
                     filter (where f.day between v_r_since and v_r_until) as q_recent,
                   max(case f.quality_ranking
                       when 'BELOW_AVERAGE_10' then 1 when 'BELOW_AVERAGE_20' then 2
                       when 'BELOW_AVERAGE_35' then 3 when 'AVERAGE' then 4
                       when 'ABOVE_AVERAGE' then 5 end)
                     filter (where f.day between v_p_since and v_p_until) as q_prior
              from ads.fact_ad_day f
             where f.brand_id = p_brand_id
               and f.day between v_p_since and v_r_until
             group by f.ad_key
         ),
         j as (
            select r.entity_key as k, r.entity_name as nm, r.optimization_goal as og,
                   r.days, r.spend as r_spend, r.impressions as r_imp,
                   r.frequency_avg as r_freq, p.frequency_avg as p_freq,
                   r.rates as r_rates, p.rates as p_rates,
                   r.conversions as r_conv, p.conversions as p_conv,
                   rk.q_recent, rk.q_prior
              from r
              join p on p.entity_key = r.entity_key
              left join rk on rk.ad_key = r.entity_key
         ),
         flagged as (
            select j.*,
                   ((j.r_rates ->> 'link_ctr')::numeric
                        < (j.p_rates ->> 'link_ctr')::numeric * 0.80) as f_ctr,
                   ((j.r_rates ->> 'cpm')::numeric
                        > (j.p_rates ->> 'cpm')::numeric * 1.20)      as f_cpm,
                   -- Defined in BOTH windows, or every ad that got its first
                   -- conversion this week reads as recovering.
                   (j.r_conv > 0 and j.p_conv > 0
                        and (j.r_rates ->> 'cpa')::numeric
                            > (j.p_rates ->> 'cpa')::numeric * 1.25)  as f_cpa,
                   (j.r_freq > j.p_freq + 0.15)                       as f_freq,
                   (j.q_recent is not null and j.q_prior is not null
                        and j.q_recent < j.q_prior)                   as f_rank
              from j
         )
    select f.k, f.nm, f.og,
           v_r_since, v_r_until,
           f.days,
           f.r_spend,
           f.r_imp,
           (f.r_spend >= p_min_spend
                and f.r_imp >= 1000
                and f.days >= p_window - 1)                       as confident,
           coalesce(f.f_ctr, false),
           coalesce(f.f_cpm, false),
           coalesce(f.f_cpa, false),
           coalesce(f.f_freq, false),
           coalesce(f.f_rank, false),
           (coalesce(f.f_ctr, false)::int + coalesce(f.f_cpm, false)::int
            + coalesce(f.f_cpa, false)::int + coalesce(f.f_freq, false)::int
            + coalesce(f.f_rank, false)::int)                     as score,
           jsonb_build_object('spend', f.r_spend, 'impressions', f.r_imp,
                              'conversions', f.r_conv,
                              'daily_frequency_avg', f.r_freq,
                              'rates', f.r_rates),
           jsonb_build_object('conversions', f.p_conv,
                              'daily_frequency_avg', f.p_freq,
                              'rates', f.p_rates)
      from flagged f
     order by (coalesce(f.f_ctr, false)::int + coalesce(f.f_cpm, false)::int
               + coalesce(f.f_cpa, false)::int + coalesce(f.f_freq, false)::int
               + coalesce(f.f_rank, false)::int) desc,
              f.r_spend desc;
end;
$fn$;

comment on function ads.fatigue is
    'Five named symptoms and their count, for each ad that ran in both the '
    'recent window and the one before it. frequency_rise tests DAILY frequency '
    'acceleration, not cumulative frequency, which this database does not have '
    '-- callers must say so. confident is the guard: a score on $30 of spend is '
    'arithmetically correct and meaningless.';

commit;
