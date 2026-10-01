-- ---------------------------------------------------------------------------
-- 017  What changed, where, and through which stage of the funnel.
--
-- The campaign triage (scripts/suggest.py -> intel/creative.py) read ONE
-- 28-day window that ran into unsettled days, never asked what had changed,
-- and had no way to tell an auction getting dearer from a creative people stop
-- clicking from a landing page that stopped converting. Ten of thirteen reds on
-- 2026-09-28 came from "an ad carrying its spend is tiring" on one to three of
-- ads.fatigue's five symptoms. The model filled the gaps with arithmetic it was
-- not allowed to do. CLAUDE.md's first rule: "If a number you need is not in a
-- verb's output, that is a missing verb." These are the two missing verbs.
--
--   ads.analysis_windows   the one definition of the named window pairs
--   ads.funnel_change      every entity, at every level, across one pair
--
-- Everything in 003's header applies and is not recited: security definer with
-- search_path pinned, stable, no dynamic SQL, no read of current_date, nullif
-- on every denominator, NULL never 0, and every printed rate out of ads.rate.
--
-- WHY EXPLICIT WINDOWS ARE SAFE HERE WHEN ads.compare REFUSES THEM
--
-- ads.compare derives its prior window rather than accepting one, because a
-- 14-day window against a 7-day prior is a spend delta of about +100% that is
-- pure arithmetic artefact (003). That argument is about COUNTS. Rates are
-- intensive -- a CPM over 7 days and a CPM over 90 days are the same kind of
-- number -- and comparing last week with all earlier history is exactly the
-- question "is this week unusual" asks. So funnel_change takes four dates and
-- defends the hazard instead of forbidding the shape:
--
--   * overlapping or inverted windows RAISE. A day counted on both sides makes
--     every change smaller than it is, silently.
--   * percent changes in COUNTS (spend, conversions, impressions, link_clicks)
--     are NULL unless the two windows are the same length. Rate changes, and
--     spend_per_active_day, are always given.
--   * callers do not choose dates at all. They take them from
--     ads.analysis_windows, so "last 7 vs prior 7" means one thing everywhere.
--
-- THE CPA DECOMPOSITION, EXACTLY
--
--     CPA = spend / conversions
--         = (spend / impressions) * (impressions / link_clicks)
--                                 * (link_clicks / conversions)
--         = CPM/1000 * 1/link_CTR * 1/CVR
--
-- Take logs of current over prior and the identity becomes a sum:
--
--     ln(CPA1/CPA0) = ln(CPM1/CPM0) - ln(CTR1/CTR0) - ln(CVR1/CVR0)
--          l_cpa    =     l_cpm     +     l_ctr     +     l_cvr
--
-- Each leg's share is 100 * leg / l_cpa. The shares are SIGNED and sum to 100
-- (to rounding): a leg pushing against the move has a negative share, which is
-- the honest reading of "CPM fell but CVR fell further". Computed from the
-- UNROUNDED window sums, never from the 4-place rates ads.rate prints, so the
-- identity holds exactly rather than to rounding error. Shares are NULL when
-- any leg is undefined, when the currencies are mixed, or when |l_cpa| is
-- under ln(1 + p_cpa_flat_pct/100): splitting a 2% move into thirds is
-- attributing noise, and dividing by a near-zero l_cpa produces shares of
-- several thousand percent that mean nothing.
--
-- primary_driver is the leg with the largest signed share, named by the stage
-- it measures (auction_cost = CPM, click_through = link CTR, post_click = CVR),
-- or 'mixed' when no leg reaches p_driver_min_share_pct. The link-CPC split is
-- the same identity one stage shorter (link CPC = CPM/1000 / link CTR), for
-- entities with no conversions to decompose.
--
-- CVR IS CONVERSIONS PER LINK CLICK, INCLUDING VIEW-THROUGH. That is ads.rate's
-- conversion_rate, and it is used unchanged so the decomposition and the rate
-- printed beside it are one number. Meta attributes some conversions to people
-- who saw the ad and never clicked, so a CVR can move without anything after
-- the click changing. The header says so because "post_click" would otherwise
-- be read as proof about the landing page.
--
-- DAILY FREQUENCY, AND WHY person_days IS SUMMED BUT NEVER RETURNED
--
-- daily_frequency here is sum(impressions) / sum(reach_this_day) over the
-- days with reach: impressions per PERSON-DAY. It is the impression-weighted
-- version of ads.window_metrics' frequency_avg (an unweighted mean of daily
-- ratios, which weights a 40-impression day like a 40,000-impression one). It
-- is still DAILY frequency -- it sits near 1.0-1.3 -- and it is not cumulative
-- frequency, which this database does not have (002). Callers say "daily
-- frequency", never "frequency".
--
-- The sum of reach_this_day is a count of person-days, which is a real
-- quantity and the correct denominator. It is NOT a reach, and the one way it
-- becomes a lie is by being printed as one. So it is named persons_this_day
-- per row, summed only into c_pd / p_pd, used only as a `/ nullif(` divisor,
-- and never returned. tests/test_migrations.py holds that rule. Ad level only:
-- above the ad, two ads reaching the same person on the same day count that
-- person twice, so an ad-group "daily frequency" would be inflated by
-- construction.
--
-- FATIGUE IS A COMBINATION, NOT A SYMPTOM
--
-- Creative fatigue is one story told by four facts together: the same people
-- see it more (daily frequency up), fewer of them click (link CTR down), each
-- click therefore costs more (link CPC up), and those who do click convert as
-- before (CVR stable -- if CVR moved too, something after the click changed
-- and "fatigue" is the wrong word). Any ONE of these has a dozen other causes.
-- So fatigue_consistent requires all four, past a gate on spend, link clicks on
-- both sides and days active; fatigue_pattern names the partial cases rather
-- than hiding them. The CVR leg is NULL (untested) when conversions are too few
-- to call it stable, and 'consistent_cvr_untested' says that out loud.
-- ads.fatigue (003) is untouched: /creative still reads it.
--
-- EVIDENCE THRESHOLDS, AND WHERE THE NUMBERS COME FROM
--
-- Conversions are roughly Poisson, and the standard error of ln(CPA1/CPA0) is
-- about sqrt(1/n1 + 1/n0), i.e. sqrt(2/n) with n conversions a side. At n = 5
-- that is 0.63 -- only a near-doubling clears two standard errors, so 5 is
-- "directional". At 20 it is 0.32 (a ~35% move is distinguishable): moderate.
-- At 50 it is 0.20 (a ~20% move): strong. 50 is also Meta's own learning-phase
-- threshold, so below it the delivery system itself is still exploring. The
-- level uses the SMALLER side, because the weaker window bounds the comparison.
--
-- ZERO current conversions is not "no evidence". If the entity were still
-- converting at its own prior CPA, current spend of k prior CPAs implies an
-- expected k conversions, and P(0 | Poisson k) = e^-k: 13.5% at k = 2
-- (directional), 5.0% at k = 3 (moderate), 0.7% at k = 5 (strong).
-- spend_to_baseline_cpa is that k.
--
-- Any level is capped at 'directional' when the current window includes days
-- past p_settled_through: Meta restates attributed conversions for about three
-- days, and a strong verdict on a number that will still move is not strong.
-- click_evidence_level is the same ladder over link clicks (30 / 100 / 400),
-- uncapped, because restatement is about attributed conversions, not clicks.
--
-- NO TARGET CPA: EACH ENTITY IS JUDGED AGAINST ITS OWN BASELINE
--
-- The operator has not set a target CPL, so nothing here compares an entity to
-- a number someone chose. baseline_cpa is the entity's own prior-window CPA;
-- decision_spend_per_arm (baseline_cpa * p_test_cpa_multiple) is how much a
-- test arm must spend before a result means anything, computed here so that
-- Python never multiplies.
--
-- CONCENTRATION
--
-- Each row carries its share of its parent's spend in both windows, the change
-- in points, its share of the parent's conversions, its spend rank among its
-- siblings, and parent_cpa_effect: ads.cpa_bridge's total_effect (003) taken
-- one level up, spend_i(cur)/C_parent(cur) - spend_i(pri)/C_parent(pri), which
-- sums exactly across siblings to the parent's CPA change. All NULL when the
-- parent spans more than one currency, because a share of dollars-plus-pounds
-- is not a share of anything.
--
-- THE GOAL IS THE CURRENT STATE
--
-- optimization_goal comes from ads.ad_group, which is what the ad group is set
-- to NOW, not what it was set to on each day of the window. A goal changed mid-
-- window is invisible here; ads.structure_change (007) is where that edit
-- shows. NULL whenever the entity's spend ran under more than one goal or any
-- unknown one, for the reason 003 gives for campaign level.
-- ---------------------------------------------------------------------------

begin;

-- ---------------------------------------------------------------------------
-- The named window pairs. One definition, so that "last 7 vs prior 7" on the
-- page, in the pack and in an agent's sentence is the same fourteen days.
--
-- Anchored at ads.settled_through, S below, never at today:
--
--   unsettled_vs_last_7   S+1 .. latest data   vs  S-6  .. S
--   last_7_vs_prior_7     S-6 .. S             vs  S-13 .. S-7
--   last_14_vs_prior_14   S-13 .. S            vs  S-27 .. S-14
--   last_28_vs_prior_28   S-27 .. S            vs  S-55 .. S-28
--   last_7_vs_before      S-6 .. S             vs  first data .. S-7
--
-- The first pair exists so the unsettled tail can be SHOWN, marked as
-- provisional, rather than silently dropped or silently included. It is never
-- a basis for a verdict, and funnel_change caps its evidence to say so.
--
-- A pair is omitted when either side holds no day of data: an empty side
-- compares something with nothing, and returning it invites a caller to read
-- "appeared" on every entity. Ordered as listed, short end first.
-- ---------------------------------------------------------------------------
create function ads.analysis_windows(
    p_brand_id uuid,
    p_asof     timestamptz
) returns table (
    pair           text,
    cur_since      date,
    cur_until      date,
    pri_since      date,
    pri_until      date,
    cur_days       integer,
    pri_days       integer,
    settled_edge   date,
    unsettled_days integer
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
declare
    v_settled date;
    v_first   date;
    v_latest  date;
begin
    v_settled := ads.settled_through(p_brand_id, p_asof);
    if v_settled is null then
        raise exception 'brand % has no active ad account, so there is no settled edge to anchor windows on',
            p_brand_id;
    end if;

    select min(i.date), max(i.date)
      into v_first, v_latest
      from public.meta_ad_insights i
     where i.brand_id = p_brand_id;

    return query
    with w (ord, nm, cs, cu, ps, pu) as (
        values (1, 'unsettled_vs_last_7'::text, v_settled + 1,  v_latest,     v_settled - 6,  v_settled),
               (2, 'last_7_vs_prior_7'::text,   v_settled - 6,  v_settled,    v_settled - 13, v_settled - 7),
               (3, 'last_14_vs_prior_14'::text, v_settled - 13, v_settled,    v_settled - 27, v_settled - 14),
               (4, 'last_28_vs_prior_28'::text, v_settled - 27, v_settled,    v_settled - 55, v_settled - 28),
               (5, 'last_7_vs_before'::text,    v_settled - 6,  v_settled,    v_first,        v_settled - 7)
    )
    select w.nm,
           w.cs,
           w.cu,
           w.ps,
           w.pu,
           (w.cu - w.cs + 1)::integer,
           (w.pu - w.ps + 1)::integer,
           v_settled,
           greatest(0, w.cu - v_settled)::integer
      from w
     -- Each side must be a real range AND overlap the days that hold data.
     -- With no data at all v_first/v_latest are NULL, every comparison is
     -- NULL, and no pair is returned -- which is the honest answer.
     where w.cs <= w.cu
       and w.ps <= w.pu
       and w.cs <= v_latest and w.cu >= v_first
       and w.ps <= v_latest and w.pu >= v_first
     order by w.ord;
end;
$fn$;

comment on function ads.analysis_windows is
    'The named window pairs every funnel reading uses, anchored at '
    'ads.settled_through: unsettled_vs_last_7, last_7_vs_prior_7, '
    'last_14_vs_prior_14, last_28_vs_prior_28 and last_7_vs_before. A pair '
    'with an empty side is omitted. unsettled_days counts the current-window '
    'days past the settled edge, which only the first pair has.';


-- ---------------------------------------------------------------------------
-- Every entity, at every level, across one pair of windows.
--
-- ONE SCAN. ads.fact_ad_day is read once for both windows and rolled up with
-- GROUPING SETS to ad, ad_group, campaign and brand in the same pass, each
-- aggregate split by FILTER into its current and prior half. Four levels from
-- four calls to window_metrics would be eight scans of a view that expands a
-- jsonb array per row, and -- worse -- four chances for the levels to be
-- computed over slightly different rows.
--
-- p_level filters what is RETURNED, not what is computed: every row needs its
-- parent's totals for the concentration columns, so all levels are always
-- built. NULL returns all four.
--
-- Each row is joined to its parent row (ad -> ad_group -> campaign -> brand)
-- for the share columns. The keys are md5 uuids with a per-level prefix (002),
-- so an entity key cannot collide across levels; the level is matched anyway.
--
-- Every threshold is a parameter with a default, and every default is
-- explained in the file header. They are named so a caller overrides one by
-- name (p_conv_strong => 30) rather than by position among twenty-two.
-- ---------------------------------------------------------------------------
create function ads.funnel_change(
    p_brand_id                 uuid,
    p_level                    text,
    p_cur_since                date,
    p_cur_until                date,
    p_pri_since                date,
    p_pri_until                date,
    p_settled_through          date,
    -- |CPA move| under this percent is 'flat' and is not decomposed.
    p_cpa_flat_pct             numeric default 5,
    -- A leg must carry at least this share of the move to be THE driver.
    p_driver_min_share_pct     numeric default 50,
    -- Conversions on the smaller side: SE of ln ratio ~ sqrt(2/n).
    p_conv_directional         integer default 5,
    p_conv_moderate            integer default 20,
    p_conv_strong              integer default 50,
    -- Link clicks on the smaller side, for the click-stage reading.
    p_clicks_directional       integer default 30,
    p_clicks_moderate          integer default 100,
    p_clicks_strong            integer default 400,
    -- Fatigue gate: ads.fatigue's spend floor, and enough clicks a side
    -- that a CTR change is not a handful of clicks moving.
    p_fatigue_min_spend        numeric default 100,
    p_fatigue_min_link_clicks  integer default 50,
    -- Fatigue legs: daily frequency +10%, link CTR -15%, link CPC +10%.
    p_fatigue_freq_rise_pct    numeric default 10,
    p_fatigue_ctr_drop_pct     numeric default 15,
    p_fatigue_cpc_rise_pct     numeric default 10,
    -- CVR within +/-20% counts as stable (CVR is the noisiest leg).
    p_cvr_stable_band_pct      numeric default 20,
    -- A test arm must spend this many baseline CPAs to be read.
    p_test_cpa_multiple        numeric default 5
) returns table (
    level                          text,
    entity_key                     uuid,
    entity_name                    text,
    entity_status                  text,
    campaign_key                   uuid,
    campaign_name                  text,
    ad_group_key                   uuid,
    ad_group_name                  text,
    parent_key                     uuid,
    optimization_goal              text,
    optimization_goal_count        integer,
    equal_length                   boolean,
    cur_unsettled_days             integer,
    cur_m                          jsonb,
    pri_m                          jsonb,
    pct                            jsonb,
    cpa_direction                  text,
    decomposition                  text,
    cpa_share_cpm_pct              numeric,
    cpa_share_link_ctr_pct         numeric,
    cpa_share_conversion_rate_pct  numeric,
    primary_driver                 text,
    link_cpc_share_cpm_pct         numeric,
    link_cpc_share_link_ctr_pct    numeric,
    spend_to_baseline_cpa          numeric,
    baseline_cpa                   numeric,
    decision_spend_per_arm         numeric,
    evidence_level                 text,
    click_evidence_level           text,
    parent_spend_share_pct         numeric,
    parent_spend_share_pct_prior   numeric,
    parent_spend_share_change_pts  numeric,
    parent_conversion_share_pct    numeric,
    spend_rank_in_parent           integer,
    parent_cpa_effect              numeric,
    currencies                     integer,
    mixed_currency                 boolean,
    fatigue_gate_ok                boolean,
    fatigue_frequency_up           boolean,
    fatigue_link_ctr_down          boolean,
    fatigue_link_cpc_up            boolean,
    fatigue_cvr_stable             boolean,
    fatigue_legs_held              integer,
    fatigue_pattern                text,
    fatigue_consistent             boolean,
    appeared                       boolean,
    disappeared                    boolean
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
#variable_conflict use_column
-- Belt and braces. Every RETURNS TABLE column above is also a plpgsql variable
-- in this body, and `level`, `pct` or `currencies` written bare in the query
-- would be ambiguous. Every column reference below is qualified by an alias;
-- this pragma only decides what happens if one is ever missed.
declare
    v_cur_days      integer;
    v_pri_days      integer;
    v_equal         boolean;
    v_flat          numeric;
    v_cur_unsettled integer;
    v_capped        boolean;
begin
    if p_level is not null
       and p_level not in ('ad', 'ad_group', 'campaign', 'brand') then
        raise exception 'unknown level %, expected ad, ad_group, campaign, brand or NULL for all', p_level
            using hint = 'Meta''s "adset" is called ad_group here; the word adset '
                         'appears nowhere in the ads schema.';
    end if;

    if p_cur_since is null or p_cur_until is null
       or p_pri_since is null or p_pri_until is null then
        raise exception 'all four window dates are required; take them from ads.analysis_windows';
    end if;

    if p_cur_since > p_cur_until then
        raise exception 'current window is inverted: since % is after until %', p_cur_since, p_cur_until;
    end if;

    if p_pri_since > p_pri_until then
        raise exception 'prior window is inverted: since % is after until %', p_pri_since, p_pri_until;
    end if;

    if p_cur_since <= p_pri_until and p_pri_since <= p_cur_until then
        raise exception 'windows overlap: % .. % and % .. % share days, which shrinks every change',
            p_cur_since, p_cur_until, p_pri_since, p_pri_until
            using hint = 'Take both windows from ads.analysis_windows.';
    end if;

    if p_cpa_flat_pct is null or p_cpa_flat_pct < 0 then
        raise exception 'p_cpa_flat_pct must be zero or more, got %', p_cpa_flat_pct;
    end if;

    v_cur_days := (p_cur_until - p_cur_since) + 1;
    v_pri_days := (p_pri_until - p_pri_since) + 1;
    v_equal    := (v_cur_days = v_pri_days);
    v_flat     := ln(1 + p_cpa_flat_pct / 100.0);
    v_cur_unsettled := case when p_settled_through is not null
                            then greatest(0, p_cur_until - p_settled_through) end;
    -- Unknown settledness caps too: a verdict cannot be strong about numbers
    -- nobody can say have stopped moving.
    v_capped   := coalesce(p_cur_until > p_settled_through, true);

    return query
    with scan as (
        select f.ad_key,
               d.ad_group_key,
               d.campaign_key,
               g.optimization_goal                                  as goal,
               f.day,
               f.currency,
               f.spend,
               f.impressions,
               f.clicks,
               f.link_clicks,
               f.landing_page_views,
               f.conversions,
               -- The daily-frequency pair, over days that have a reach at all.
               -- A day with impressions but no reach recorded would put
               -- impressions over nobody.
               case when f.reach_this_day > 0 then f.impressions end    as freq_impressions,
               case when f.reach_this_day > 0 then f.reach_this_day end as persons_this_day,
               (f.day between p_cur_since and p_cur_until)          as in_cur,
               (f.day between p_pri_since and p_pri_until)          as in_pri
          from ads.fact_ad_day f
          join ads.ad d            on d.ad_key = f.ad_key
          left join ads.ad_group g on g.ad_group_key = d.ad_group_key
         where f.brand_id = p_brand_id
           and (f.day between p_cur_since and p_cur_until
                or f.day between p_pri_since and p_pri_until)
    ),
    agg as (
        select case grouping(s.campaign_key, s.ad_group_key, s.ad_key)
                   when 0 then 'ad'
                   when 1 then 'ad_group'
                   when 3 then 'campaign'
                   else 'brand'
               end                                                         as lvl,
               s.campaign_key                                              as camp_key,
               s.ad_group_key                                              as grp_key,
               s.ad_key                                                    as a_key,
               (count(*) filter (where s.in_cur) > 0)                      as has_cur,
               (count(*) filter (where s.in_pri) > 0)                      as has_pri,
               (sum(s.spend) filter (where s.in_cur))::numeric             as c_spend,
               (sum(s.spend) filter (where s.in_pri))::numeric             as p_spend,
               (sum(s.impressions) filter (where s.in_cur))::bigint        as c_imp,
               (sum(s.impressions) filter (where s.in_pri))::bigint        as p_imp,
               (sum(s.clicks) filter (where s.in_cur))::bigint             as c_clicks,
               (sum(s.clicks) filter (where s.in_pri))::bigint             as p_clicks,
               (sum(s.link_clicks) filter (where s.in_cur))::bigint        as c_lc,
               (sum(s.link_clicks) filter (where s.in_pri))::bigint        as p_lc,
               (sum(s.landing_page_views) filter (where s.in_cur))::bigint as c_lpv,
               (sum(s.landing_page_views) filter (where s.in_pri))::bigint as p_lpv,
               (sum(s.conversions) filter (where s.in_cur))::bigint        as c_conv,
               (sum(s.conversions) filter (where s.in_pri))::bigint        as p_conv,
               (sum(s.freq_impressions) filter (where s.in_cur))::numeric  as c_fnum,
               (sum(s.freq_impressions) filter (where s.in_pri))::numeric  as p_fnum,
               (sum(s.persons_this_day) filter (where s.in_cur))::numeric  as c_pd,
               (sum(s.persons_this_day) filter (where s.in_pri))::numeric  as p_pd,
               (count(distinct s.day) filter (where s.in_cur))::integer    as c_days_active,
               (count(distinct s.day) filter (where s.in_pri))::integer    as p_days_active,
               -- Across BOTH windows: a currency change between the windows
               -- invalidates the comparison as surely as two currencies in one.
               count(distinct s.currency)::integer                        as n_currencies,
               count(distinct s.goal)::integer                            as n_goals,
               coalesce(bool_or(s.goal is null), false)                   as goal_unknown,
               min(s.goal)                                                as any_goal
          from scan s
         group by grouping sets ((s.campaign_key, s.ad_group_key, s.ad_key),
                                 (s.campaign_key, s.ad_group_key),
                                 (s.campaign_key),
                                 ())
    ),
    base as (
        select a.*,
               case a.lvl when 'ad'       then a.a_key
                          when 'ad_group' then a.grp_key
                          when 'campaign' then a.camp_key
                          else p_brand_id end                              as ekey,
               case a.lvl when 'ad'       then a.grp_key
                          when 'ad_group' then a.camp_key
                          when 'campaign' then p_brand_id end              as pkey,
               case a.lvl when 'ad'       then 'ad_group'
                          when 'ad_group' then 'campaign'
                          when 'campaign' then 'brand' end                 as plvl,
               (a.n_currencies > 1)                                        as mixed,
               ads.rate(a.c_imp, a.c_clicks, a.c_lc, a.c_spend, a.c_conv, a.c_lpv) as c_rates,
               ads.rate(a.p_imp, a.p_clicks, a.p_lc, a.p_spend, a.p_conv, a.p_lpv) as p_rates,
               -- Impressions per person-day. Ad level only; see the header.
               case when a.lvl = 'ad'
                    then round(a.c_fnum / nullif(a.c_pd, 0), 4) end       as c_freq,
               case when a.lvl = 'ad'
                    then round(a.p_fnum / nullif(a.p_pd, 0), 4) end       as p_freq,
               round(a.c_spend / nullif(a.c_days_active, 0), 2)            as c_spad,
               round(a.p_spend / nullif(a.p_days_active, 0), 2)            as p_spad,
               -- UNROUNDED stage ratios for the decomposition. Scale factors
               -- (the 1000 in CPM, the 100 in a percent) cancel in a ratio of
               -- current to prior, so they are left out.
               a.c_spend / nullif(a.c_imp, 0)                              as c_cpm_u,
               a.p_spend / nullif(a.p_imp, 0)                              as p_cpm_u,
               a.c_lc::numeric / nullif(a.c_imp, 0)                        as c_ctr_u,
               a.p_lc::numeric / nullif(a.p_imp, 0)                        as p_ctr_u,
               a.c_conv::numeric / nullif(a.c_lc, 0)                       as c_cvr_u,
               a.p_conv::numeric / nullif(a.p_lc, 0)                       as p_cvr_u,
               a.c_spend / nullif(a.c_conv, 0)                             as c_cpa_u,
               a.p_spend / nullif(a.p_conv, 0)                             as p_cpa_u,
               -- least() ignores NULLs, so a missing window would otherwise
               -- read as the other window's count. A missing window is zero.
               least(coalesce(a.c_conv, 0), coalesce(a.p_conv, 0))         as conv_min,
               least(coalesce(a.c_lc, 0), coalesce(a.p_lc, 0))             as lc_min
          from agg a
         -- The grand-total row of an empty scan has neither window.
         where a.has_cur or a.has_pri
    ),
    legs as (
        select b.*,
               -- Each leg guarded so ln() never sees zero, a negative or NULL.
               case when b.c_cpm_u > 0 and b.p_cpm_u > 0
                    then ln(b.c_cpm_u / b.p_cpm_u) end                     as l_cpm,
               case when b.c_ctr_u > 0 and b.p_ctr_u > 0
                    then -ln(b.c_ctr_u / b.p_ctr_u) end                    as l_ctr,
               case when b.c_cvr_u > 0 and b.p_cvr_u > 0
                    then -ln(b.c_cvr_u / b.p_cvr_u) end                    as l_cvr,
               -- CPA's own log change, for its direction. Defined even when a
               -- leg is not (conversions with no link clicks: view-through).
               case when b.c_cpa_u > 0 and b.p_cpa_u > 0
                    then ln(b.c_cpa_u / b.p_cpa_u) end                     as l_cpa_own,
               (b.p_rates ->> 'cpa')::numeric                              as p_cpa,
               -- Percent changes, each from the ads.rate values printed beside
               -- them. Money is not compared across mixed currencies; counts
               -- only across equal-length windows (see the header).
               case when v_equal and not b.mixed
                    then round(100.0 * (b.c_spend - b.p_spend) / nullif(b.p_spend, 0), 2) end
                                                                           as pc_spend,
               case when v_equal
                    then round(100.0 * (b.c_conv - b.p_conv) / nullif(b.p_conv, 0), 2) end
                                                                           as pc_conv,
               case when v_equal
                    then round(100.0 * (b.c_imp - b.p_imp) / nullif(b.p_imp, 0), 2) end
                                                                           as pc_imp,
               case when v_equal
                    then round(100.0 * (b.c_lc - b.p_lc) / nullif(b.p_lc, 0), 2) end
                                                                           as pc_lc,
               case when not b.mixed
                    then round(100.0 * (b.c_spad - b.p_spad) / nullif(b.p_spad, 0), 2) end
                                                                           as pc_spad,
               case when not b.mixed
                    then round(100.0 * ((b.c_rates ->> 'cpm')::numeric - (b.p_rates ->> 'cpm')::numeric)
                               / nullif((b.p_rates ->> 'cpm')::numeric, 0), 2) end
                                                                           as pc_cpm,
               round(100.0 * ((b.c_rates ->> 'link_ctr')::numeric - (b.p_rates ->> 'link_ctr')::numeric)
                     / nullif((b.p_rates ->> 'link_ctr')::numeric, 0), 2)  as pc_ctr,
               case when not b.mixed
                    then round(100.0 * ((b.c_rates ->> 'cost_per_link_click')::numeric
                                        - (b.p_rates ->> 'cost_per_link_click')::numeric)
                               / nullif((b.p_rates ->> 'cost_per_link_click')::numeric, 0), 2) end
                                                                           as pc_cpc,
               round(100.0 * ((b.c_rates ->> 'conversion_rate')::numeric
                              - (b.p_rates ->> 'conversion_rate')::numeric)
                     / nullif((b.p_rates ->> 'conversion_rate')::numeric, 0), 2)
                                                                           as pc_cvr,
               case when not b.mixed
                    then round(100.0 * ((b.c_rates ->> 'cpa')::numeric - (b.p_rates ->> 'cpa')::numeric)
                               / nullif((b.p_rates ->> 'cpa')::numeric, 0), 2) end
                                                                           as pc_cpa,
               round(100.0 * (b.c_freq - b.p_freq) / nullif(b.p_freq, 0), 2) as pc_freq
          from base b
    ),
    dcp as (
        select l.*,
               (l.l_cpm + l.l_ctr + l.l_cvr)                               as l_cpa,
               case
                   when not l.has_pri                                     then 'appeared'
                   when not l.has_cur                                     then 'disappeared'
                   when l.mixed                                           then 'mixed_currency'
                   when coalesce(l.c_imp, 0) = 0
                        or coalesce(l.p_imp, 0) = 0                       then 'no_impressions'
                   when coalesce(l.c_spend, 0) = 0
                        or coalesce(l.p_spend, 0) = 0                     then 'no_spend'
                   when coalesce(l.c_lc, 0) = 0
                        or coalesce(l.p_lc, 0) = 0                        then 'no_link_clicks'
                   when coalesce(l.c_conv, 0) = 0
                        and coalesce(l.p_conv, 0) = 0                     then 'no_conversions_either_window'
                   when coalesce(l.p_conv, 0) = 0                         then 'no_conversions_prior'
                   when coalesce(l.c_conv, 0) = 0                         then 'no_conversions_current'
                   when abs(l.l_cpm + l.l_ctr + l.l_cvr) < v_flat         then 'cpa_flat'
                   else                                                        'complete'
               end                                                         as reason,
               case
                   when l.mixed or l.l_cpa_own is null                    then null
                   when abs(l.l_cpa_own) < v_flat                         then 'flat'
                   when l.l_cpa_own > 0                                   then 'up'
                   else                                                        'down'
               end                                                         as direction,
               case when not l.mixed
                    then round(l.c_spend / nullif(l.p_cpa, 0), 2) end      as stb
          from legs l
    ),
    sh as (
        select d.*,
               case when d.reason = 'complete'
                    then round(100 * d.l_cpm / nullif(d.l_cpa, 0), 2) end  as s_cpm,
               case when d.reason = 'complete'
                    then round(100 * d.l_ctr / nullif(d.l_cpa, 0), 2) end  as s_ctr,
               case when d.reason = 'complete'
                    then round(100 * d.l_cvr / nullif(d.l_cpa, 0), 2) end  as s_cvr,
               -- Link CPC = CPM/1000 / link CTR: the same identity one stage
               -- shorter, for entities with no conversions to decompose. Under
               -- the same flat threshold, for the same reason.
               case when not d.mixed and d.l_cpm is not null and d.l_ctr is not null
                         and abs(d.l_cpm + d.l_ctr) >= v_flat
                    then round(100 * d.l_cpm / nullif(d.l_cpm + d.l_ctr, 0), 2) end
                                                                           as lcpc_cpm,
               case when not d.mixed and d.l_cpm is not null and d.l_ctr is not null
                         and abs(d.l_cpm + d.l_ctr) >= v_flat
                    then round(100 * d.l_ctr / nullif(d.l_cpm + d.l_ctr, 0), 2) end
                                                                           as lcpc_ctr,
               case
                   when not d.has_pri or coalesce(d.p_conv, 0) = 0 or d.mixed then null
                   when d.conv_min >= p_conv_strong                       then 'strong'
                   when d.conv_min >= p_conv_moderate                     then 'moderate'
                   when d.conv_min >= p_conv_directional                  then 'directional'
                   -- Zero conversions on k baseline CPAs of spend: P(0) = e^-k.
                   when coalesce(d.c_conv, 0) = 0 and d.stb >= 5          then 'strong'
                   when coalesce(d.c_conv, 0) = 0 and d.stb >= 3          then 'moderate'
                   when coalesce(d.c_conv, 0) = 0 and d.stb >= 2          then 'directional'
                   else                                                        'insufficient'
               end                                                         as ev_raw,
               case
                   when not d.has_pri or coalesce(d.p_lc, 0) = 0          then null
                   when d.lc_min >= p_clicks_strong                       then 'strong'
                   when d.lc_min >= p_clicks_moderate                     then 'moderate'
                   when d.lc_min >= p_clicks_directional                  then 'directional'
                   else                                                        'insufficient'
               end                                                         as click_ev,
               -- Fatigue legs, ad level only. NULL elsewhere, and NULL where
               -- the leg cannot be evaluated -- never false by default.
               case when d.lvl = 'ad' then
                   (d.has_cur and d.has_pri
                    and coalesce(d.c_spend, 0) >= p_fatigue_min_spend
                    and coalesce(d.c_lc, 0) >= p_fatigue_min_link_clicks
                    and coalesce(d.p_lc, 0) >= p_fatigue_min_link_clicks
                    and d.c_days_active >= v_cur_days - 1) end             as f_gate,
               case when d.lvl = 'ad'
                    then d.c_freq >= d.p_freq * (1 + p_fatigue_freq_rise_pct / 100.0) end
                                                                           as f_freq,
               case when d.lvl = 'ad'
                    then d.pc_ctr <= (0 - p_fatigue_ctr_drop_pct) end      as f_ctr,
               case when d.lvl = 'ad'
                    then d.pc_cpc >= p_fatigue_cpc_rise_pct end            as f_cpc,
               -- Stable is only claimable with enough conversions to see a
               -- move; below that it is untested, not stable.
               case when d.lvl = 'ad' and d.conv_min >= p_conv_directional
                    then abs(d.pc_cvr) <= p_cvr_stable_band_pct end        as f_cvr
          from dcp d
    ),
    ev as (
        select x.*,
               case
                   when x.s_cpm is null or x.s_ctr is null or x.s_cvr is null then null
                   when greatest(x.s_cpm, x.s_ctr, x.s_cvr) < p_driver_min_share_pct then 'mixed'
                   when x.s_cpm >= x.s_ctr and x.s_cpm >= x.s_cvr         then 'auction_cost'
                   when x.s_ctr >= x.s_cvr                                then 'click_through'
                   else                                                        'post_click'
               end                                                         as driver,
               case when v_capped and x.ev_raw in ('strong', 'moderate')
                    then 'directional' else x.ev_raw end                   as evidence,
               case when x.lvl = 'ad'
                    then (coalesce(x.f_freq, false)::integer + coalesce(x.f_ctr, false)::integer
                          + coalesce(x.f_cpc, false)::integer + coalesce(x.f_cvr, false)::integer) end
                                                                           as legs_held,
               case
                   when x.lvl <> 'ad'                                     then null
                   when not x.f_gate                                      then 'insufficient'
                   when coalesce(x.f_freq, false) and coalesce(x.f_ctr, false)
                        and coalesce(x.f_cpc, false) and coalesce(x.f_cvr, false)
                                                                          then 'consistent'
                   when coalesce(x.f_freq, false) and coalesce(x.f_ctr, false)
                        and coalesce(x.f_cpc, false) and x.f_cvr is null  then 'consistent_cvr_untested'
                   when coalesce(x.f_ctr, false) and x.f_cvr is false     then 'cvr_moved'
                   when (coalesce(x.f_freq, false)::integer + coalesce(x.f_ctr, false)::integer
                         + coalesce(x.f_cpc, false)::integer) >= 2        then 'partial'
                   else                                                        'none'
               end                                                         as pattern
          from sh x
    ),
    fin as (
        select e.*,
               (par.n_currencies = 1)                                      as par_ok,
               par.c_spend                                                 as par_c_spend,
               par.p_spend                                                 as par_p_spend,
               par.c_conv                                                  as par_c_conv,
               par.p_conv                                                  as par_p_conv,
               case when e.c_spend > 0 and par.n_currencies = 1
                    then rank() over (partition by e.lvl, e.pkey, (e.c_spend > 0)
                                      order by e.c_spend desc) end         as spend_rank
          from ev e
          left join base par on par.lvl = e.plvl and par.ekey = e.pkey
    )
    select fn.lvl::text,
           fn.ekey::uuid,
           (case fn.lvl when 'ad'       then ad_.name
                        when 'ad_group' then grp.name
                        when 'campaign' then cmp.name
                        else br.name end)::text,
           (case fn.lvl when 'ad'       then ad_.effective_status
                        when 'ad_group' then grp.effective_status
                        when 'campaign' then cmp.effective_status
                        end)::text,
           fn.camp_key::uuid,
           cmp.name::text,
           fn.grp_key::uuid,
           grp.name::text,
           fn.pkey::uuid,
           (case when fn.n_goals = 1 and not fn.goal_unknown then fn.any_goal end)::text,
           -- An unknown goal counts as one more: it cannot be shown to be the
           -- same as any known one.
           (fn.n_goals + case when fn.goal_unknown then 1 else 0 end)::integer,
           v_equal,
           v_cur_unsettled,
           case when fn.has_cur then jsonb_strip_nulls(jsonb_build_object(
               'window_days',          v_cur_days,
               'days_active',          fn.c_days_active,
               'spend',                fn.c_spend,
               'impressions',          fn.c_imp,
               'clicks',               fn.c_clicks,
               'link_clicks',          fn.c_lc,
               'conversions',          fn.c_conv,
               'spend_per_active_day', fn.c_spad,
               'daily_frequency',      fn.c_freq,
               'rates',                fn.c_rates)) end,
           case when fn.has_pri then jsonb_strip_nulls(jsonb_build_object(
               'window_days',          v_pri_days,
               'days_active',          fn.p_days_active,
               'spend',                fn.p_spend,
               'impressions',          fn.p_imp,
               'clicks',               fn.p_clicks,
               'link_clicks',          fn.p_lc,
               'conversions',          fn.p_conv,
               'spend_per_active_day', fn.p_spad,
               'daily_frequency',      fn.p_freq,
               'rates',                fn.p_rates)) end,
           jsonb_strip_nulls(jsonb_build_object(
               'spend',                fn.pc_spend,
               'conversions',          fn.pc_conv,
               'impressions',          fn.pc_imp,
               'link_clicks',          fn.pc_lc,
               'spend_per_active_day', fn.pc_spad,
               'cpm',                  fn.pc_cpm,
               'link_ctr',             fn.pc_ctr,
               'cost_per_link_click',  fn.pc_cpc,
               'conversion_rate',      fn.pc_cvr,
               'cpa',                  fn.pc_cpa,
               'daily_frequency',      fn.pc_freq)),
           fn.direction::text,
           fn.reason::text,
           fn.s_cpm::numeric,
           fn.s_ctr::numeric,
           fn.s_cvr::numeric,
           fn.driver::text,
           fn.lcpc_cpm::numeric,
           fn.lcpc_ctr::numeric,
           fn.stb::numeric,
           fn.p_cpa::numeric,
           round(fn.p_cpa * p_test_cpa_multiple, 2)::numeric,
           fn.evidence::text,
           fn.click_ev::text,
           (case when fn.par_ok
                 then round(100.0 * coalesce(fn.c_spend, 0) / nullif(fn.par_c_spend, 0), 2) end)::numeric,
           (case when fn.par_ok
                 then round(100.0 * coalesce(fn.p_spend, 0) / nullif(fn.par_p_spend, 0), 2) end)::numeric,
           (case when fn.par_ok
                 then round(100.0 * coalesce(fn.c_spend, 0) / nullif(fn.par_c_spend, 0)
                            - 100.0 * coalesce(fn.p_spend, 0) / nullif(fn.par_p_spend, 0), 2) end)::numeric,
           (case when fn.par_ok
                 then round(100.0 * coalesce(fn.c_conv, 0) / nullif(fn.par_c_conv, 0), 2) end)::numeric,
           fn.spend_rank::integer,
           (case when fn.par_ok
                 then round(coalesce(fn.c_spend, 0) / nullif(fn.par_c_conv, 0)
                            - coalesce(fn.p_spend, 0) / nullif(fn.par_p_conv, 0), 4) end)::numeric,
           fn.n_currencies::integer,
           fn.mixed::boolean,
           fn.f_gate::boolean,
           fn.f_freq::boolean,
           fn.f_ctr::boolean,
           fn.f_cpc::boolean,
           fn.f_cvr::boolean,
           fn.legs_held::integer,
           fn.pattern::text,
           (case when fn.lvl = 'ad' then fn.pattern = 'consistent' end)::boolean,
           (not fn.has_pri)::boolean,
           (not fn.has_cur)::boolean
      from fin fn
      left join ads.ad ad_      on fn.lvl = 'ad' and ad_.ad_key = fn.a_key
      left join ads.ad_group grp on grp.ad_group_key = fn.grp_key
      left join ads.campaign cmp on cmp.campaign_key = fn.camp_key
      left join ads.brand br     on fn.lvl = 'brand' and br.id = p_brand_id
     where p_level is null or fn.lvl = p_level
     order by case fn.lvl when 'brand'    then 0
                          when 'campaign' then 1
                          when 'ad_group' then 2
                          else 3 end,
              fn.c_spend desc nulls last,
              fn.ekey;
end;
$fn$;

comment on function ads.funnel_change is
    'Every entity at ad, ad_group, campaign and brand level across two '
    'non-overlapping windows (take them from ads.analysis_windows): both '
    'windows'' counts and ads.rate rates, percent changes (counts only when '
    'the windows are equal length), the exact log decomposition of the CPA '
    'change into CPM, link CTR and CVR shares, an evidence level from the '
    'smaller side''s conversions (capped at directional over unsettled days), '
    'shares of the parent''s spend and conversions, and at ad level a fatigue '
    'reading that requires daily frequency up, link CTR down, link CPC up and '
    'CVR stable together. daily_frequency is impressions per person-day, not '
    'cumulative frequency.';


-- Owner and grant, 016's pattern and for 013's reasons: ads_migrate applies as
-- postgres, so default privileges never fire, and a definer function left
-- owned by postgres reads AROUND RLS where every other one reads THROUGH it.
-- Full signatures, because a function is identified by its argument types.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_owner') then
        raise notice 'ads_owner does not exist; skipping ownership reassignment';
        return;
    end if;
    execute 'alter function ads.analysis_windows(uuid, timestamptz) '
            'owner to ads_owner';
    execute 'alter function ads.funnel_change(uuid, text, date, date, date, date, date, '
            'numeric, numeric, integer, integer, integer, integer, integer, integer, '
            'numeric, integer, numeric, numeric, numeric, numeric, numeric) '
            'owner to ads_owner';
end;
$$;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_reader') then
        raise notice 'ads_reader does not exist; skipping grant';
        return;
    end if;
    execute 'grant execute on function ads.analysis_windows(uuid, timestamptz) '
            'to ads_reader';
    execute 'grant execute on function ads.funnel_change(uuid, text, date, date, date, date, date, '
            'numeric, numeric, integer, integer, integer, integer, integer, integer, '
            'numeric, integer, numeric, numeric, numeric, numeric, numeric) '
            'to ads_reader';
end;
$$;

commit;
