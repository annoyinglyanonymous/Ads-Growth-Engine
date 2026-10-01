-- ---------------------------------------------------------------------------
-- 018  Which moves matter in dollars, which facets earn their place within a
--      goal, and what happened after a recommendation was carried out.
--
-- The campaign triage (scripts/suggest.py -> intel/creative.py) can now say
-- WHAT changed and through which funnel stage (017). It cannot say which of
-- twenty campaigns matters most, it judges a hook against hooks run under
-- other optimization goals, and it has no way to read back whether a
-- recommendation that was carried out did anything. Each of those would be
-- arithmetic in Python or in a model's head, and CLAUDE.md's first rule says
-- what that means: "If a number you need is not in a verb's output, that is a
-- missing verb." These are the four missing verbs.
--
--   ads.goal_impact      dollars of excess spend per goal and per campaign,
--                        and each campaign's slice of its goal's CPA change
--   ads.facet_by_goal    hook / offer / audience / format per goal, each
--                        against that goal's own CPA
--   ads.entity_outcome   one entity's 017 funnel row, before vs after a date
--   ads.arm_compare      two ads over one window, challenger against control
--
-- Everything in 003's header applies and is not recited: security definer with
-- search_path pinned, stable, no dynamic SQL, no read of current_date, nullif
-- on every denominator, NULL never 0, and every printed rate out of ads.rate.
-- 017's conventions apply too: every RETURNS TABLE column is also a plpgsql
-- variable, so every column reference is qualified by an alias, every body
-- opens with `#variable_conflict use_column`, and every RETURN QUERY column
-- carries an explicit cast so a type drift fails at create time, not on a
-- caller's first read.
--
-- EVIDENCE: 017's LADDER, UNCHANGED
--
-- Conversions on the SMALLER side: insufficient under 5, directional from 5,
-- moderate from 20, strong from 50 (017's header derives them: the standard
-- error of ln(CPA1/CPA0) is about sqrt(2/n)). Zero current conversions on k
-- prior CPAs of spend is evidence too, P(0 | Poisson k) = e^-k: directional at
-- k >= 2, moderate at 3, strong at 5. Capped at 'directional' whenever the
-- current window runs past the settled edge, or the edge is unknown. The
-- thresholds are parameters with 017's defaults, named the same way.
--
-- ---------------------------------------------------------------------------
-- ads.goal_impact, EXACTLY
--
-- Per goal (every ad group whose CURRENT optimization_goal is that goal) and
-- per single-goal campaign, across a current and a prior window:
--
--   excess_cost   = cur_spend - cur_conversions * pri_cpa          (2dp)
--
--       The dollars this window spent beyond what its conversions would have
--       cost at last window's CPA. Positive is overspend, negative is saving.
--       pri_cpa is the ads.rate figure printed beside it, so a reader can redo
--       the line by hand. NULL when pri_cpa is (no prior conversions), and on
--       mixed currencies. Zero current conversions makes all current spend
--       excess, which is the honest reading of "nothing came back".
--
--   impact_score  = excess_cost * weight(evidence_level)            (2dp)
--
--       weight: insufficient 0, directional 0.5, moderate 0.8, strong 1. A
--       large excess on three conversions is mostly noise, and the weight
--       says how much of it to believe. NULL when excess_cost is.
--
--   goal_cpa_contribution  = c_spend / goal_c_conv - p_spend / goal_p_conv
--                                                                    (4dp)
--
--       ads.cpa_bridge's total_effect (003) with the GOAL as the population
--       instead of the brand -- the same identity 017's parent_cpa_effect
--       uses one level up. Summed over every slice of the goal's spend it
--       equals the goal's CPA change exactly. On a goal row it IS the goal's
--       CPA change (from the unrounded sums), so the identity can be checked
--       against the rows beneath it.
--
--   contribution_share_pct = 100 * contribution / goal CPA change   (2dp)
--
--       Signed: a campaign pushing against the goal's move has a negative
--       share. NULL when the goal's CPA is flat -- |ln(CPA1/CPA0)| under
--       ln(1 + p_cpa_flat_pct/100), 017's flat rule -- because dividing by a
--       near-zero change produces shares of thousands of percent that mean
--       nothing. Also NULL when the goal's CPA is undefined on either side.
--
--   contribution_rank, cumulative_share_pct
--
--       Within the goal, ordered by contribution IN THE DIRECTION OF THE
--       GOAL'S MOVE: descending when the goal's CPA rose, ascending when it
--       fell (so the campaign that did most of a fall ranks first, not the one
--       that resisted it hardest). That is the order of contribution_share_pct
--       descending wherever the share is defined. cumulative_share_pct is the
--       running sum of the unrounded shares in that order, over an explicit
--       ROWS frame so ties do not collapse into one step -- "3 campaigns
--       account for 68% of the rise" is read straight off it.
--
-- WHY RANKING DOLLARS ACROSS GOALS IS ALLOWED AND RANKING CPA IS NOT
--
-- 016's argument: a dollar is a dollar under any optimization goal. $900 spent
-- beyond what a LEAD_GENERATION campaign's leads were worth last week and $900
-- beyond what a LINK_CLICKS campaign's clicks were worth are the same $900 out
-- of the same budget, so excess_cost and impact_score may be sorted across the
-- whole account, and the triage does exactly that. A cost per result is not a
-- dollar: a $40 lead and a $0.40 click are different animals (CLAUDE.md), so
-- nothing in this file orders, ranks or compares a CPA -- or a contribution,
-- which is in CPA units -- across goals. Every window function below
-- partitions by the goal, and tests/test_migrations.py holds that.
--
-- WHICH ROWS EXIST
--
--   goal rows       one per KNOWN goal, over ALL ad groups running it --
--                   including the ad groups of multi-goal campaigns. The goal
--                   total is the goal's real CPA, whoever owns the ad group.
--                   Spend whose ad group has no known goal is in no row: it
--                   cannot be shown to belong to any goal, and a NULL "goal"
--                   row would be a cross-goal mixture with a CPA.
--
--   campaign rows   only for campaigns whose ad groups ALL ran ONE known goal
--                   across both windows (017's optimization_goal rule at
--                   campaign level). A multi-goal campaign gets no row: its
--                   CPA would be a mixture, and it has no single goal to rank
--                   inside. Its ad groups' spend still counts in each goal's
--                   total, so its slice of the goal's change is the part of
--                   cumulative_share_pct that never reaches 100.
--                   multi_goal_campaigns on the goal row says how many such
--                   campaigns that is, so the gap is never silent.
--
-- The goal is the CURRENT ad-group setting (017's header: a goal changed
-- mid-window is invisible here; ads.structure_change shows the edit).
-- Windows are explicit and must not overlap, as in ads.funnel_change; take
-- them from ads.analysis_windows. Every figure here is either a sum or
-- intensive (a CPA, an excess at a rate), so unequal lengths are safe.
--
-- ---------------------------------------------------------------------------
-- ads.facet_by_goal
--
-- 013's facet_performance, partitioned by goal instead of refusing to rank
-- across it. Values come from ads.facet_effective exactly as there (hook,
-- offer, audience from the tag; family and angle through angle_id; format from
-- ads.ad), and the untagged bucket is returned as value NULL, never filtered.
--
--   goal_cpa      ads.rate's cpa over ALL the goal's spend in the window,
--                 tagged and untagged: the goal's own CPA is the yardstick.
--   relative_pct  100 * (value_cpa - goal_cpa) / goal_cpa  (2dp; the printed
--                 rates, so the figure is reproducible from the row)
--   relative      'baseline'  the largest-spend TAGGED value in the goal
--                 'better'    relative_pct <= -p_relative_band_pct (10)
--                 'worse'     relative_pct >= +p_relative_band_pct
--                 'similar'   between
--                 NULL        no CPA to compare, the untagged bucket ("no hook
--                             tag" is not a hook), the unknown goal, or mixed
--                             currencies
--   evidence_level  the value's OWN conversions on 017's ladder, uncapped: the
--                 caller chooses the window, and there is no prior side.
--   spend_share_pct  of the goal's window spend.
--
-- Rows whose ad group has no known goal are returned with optimization_goal
-- NULL and no comparison at all, for 013's reason: spend that vanishes out of
-- a denominator is the failure this schema is least able to see.
--
-- ---------------------------------------------------------------------------
-- ads.entity_outcome and ads.arm_compare
--
-- entity_outcome is ads.funnel_change for ONE entity with current = after and
-- prior = before, plus after_settled_days: how many days of the after window
-- are at or before the settled edge. It adds no arithmetic; a before/after
-- reading is 017's comparison with the dates chosen by an implementation day.
--
-- arm_compare reads two ads over ONE window -- a test's two arms, running at
-- the same time, so a CPA between them is a like-for-like comparison:
--
--   effect_pct.<r> = 100 * (challenger_r - control_r) / control_r  (2dp)
--
-- for cpa, link_ctr, conversion_rate and cost_per_link_click, from ads.rate.
-- Money effects are NULL across mixed currencies. evidence_level is the ladder
-- on the SMALLER arm's conversions, capped over unsettled days.
-- spend_sufficient is both arms at or above p_decision_spend (017's
-- decision_spend_per_arm, passed in); NULL when none is given.
--
-- None of this concludes anything. An outcome is a measured reading for a
-- person to weigh, and ads.experiment.conclusion is written by a person only.
-- ---------------------------------------------------------------------------

begin;

-- ---------------------------------------------------------------------------
-- 1. Excess dollars and each campaign's slice of its goal's CPA change.
--
-- ONE SCAN, rolled up with GROUPING SETS to (goal) and (goal, campaign) in the
-- same pass, each aggregate split by FILTER into its current and prior half,
-- for 017's reason: two passes are two chances to read different rows.
-- ---------------------------------------------------------------------------
create function ads.goal_impact(
    p_brand_id          uuid,
    p_cur_since         date,
    p_cur_until         date,
    p_pri_since         date,
    p_pri_until         date,
    p_settled_through   date,
    -- A goal CPA move under this percent is flat and is not split into shares.
    p_cpa_flat_pct      numeric default 5,
    -- Conversions on the smaller side: 017's ladder.
    p_conv_directional  integer default 5,
    p_conv_moderate     integer default 20,
    p_conv_strong       integer default 50
) returns table (
    level                   text,
    optimization_goal       text,
    campaign_key            uuid,
    campaign_name           text,
    cur_spend               numeric,
    pri_spend               numeric,
    cur_conversions         bigint,
    pri_conversions         bigint,
    cur_cpa                 numeric,
    pri_cpa                 numeric,
    cpa_pct                 numeric,
    excess_cost             numeric,
    evidence_level          text,
    impact_score            numeric,
    goal_cpa_contribution   numeric,
    contribution_share_pct  numeric,
    cumulative_share_pct    numeric,
    contribution_rank       integer,
    campaigns_in_goal       integer,
    multi_goal_campaigns    integer,
    mixed_currency          boolean
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
#variable_conflict use_column
-- `level`, `optimization_goal` and `campaign_key` are OUT variables here and
-- column names in ads.ad_group / ads.campaign. Every reference below is
-- qualified; this pragma only decides what happens if one is ever missed.
declare
    v_flat   numeric;
    v_capped boolean;
begin
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

    v_flat   := ln(1 + p_cpa_flat_pct / 100.0);
    -- Unknown settledness caps too (017).
    v_capped := coalesce(p_cur_until > p_settled_through, true);

    return query
    with scan as (
        select d.campaign_key                                  as camp_key,
               g.optimization_goal                             as goal,
               f.currency                                      as ccy,
               f.spend                                         as spend,
               f.impressions                                   as impressions,
               f.clicks                                        as clicks,
               f.link_clicks                                   as link_clicks,
               f.landing_page_views                            as landing_page_views,
               f.conversions                                   as conversions,
               (f.day between p_cur_since and p_cur_until)     as in_cur,
               (f.day between p_pri_since and p_pri_until)     as in_pri
          from ads.fact_ad_day f
          join ads.ad d            on d.ad_key = f.ad_key
          left join ads.ad_group g on g.ad_group_key = d.ad_group_key
         where f.brand_id = p_brand_id
           and (f.day between p_cur_since and p_cur_until
                or f.day between p_pri_since and p_pri_until)
    ),
    -- Whether each campaign ran ONE known goal across both windows. An
    -- unknown goal counts against it: it cannot be shown to be the same goal.
    camp as (
        select s.camp_key                                       as camp_key,
               (count(distinct s.goal) = 1
                and not coalesce(bool_or(s.goal is null), false)) as single_goal
          from scan s
         group by s.camp_key
    ),
    agg as (
        select (grouping(s.camp_key) = 1)                                   as is_goal,
               s.goal                                                       as gl,
               s.camp_key                                                   as ckey,
               coalesce(bool_and(c.single_goal), false)                     as single_goal,
               (count(*) filter (where s.in_cur) > 0)                       as has_cur,
               (count(*) filter (where s.in_pri) > 0)                       as has_pri,
               coalesce(sum(s.spend) filter (where s.in_cur), 0)::numeric   as c_spend,
               coalesce(sum(s.spend) filter (where s.in_pri), 0)::numeric   as p_spend,
               coalesce(sum(s.impressions) filter (where s.in_cur), 0)::bigint        as c_imp,
               coalesce(sum(s.impressions) filter (where s.in_pri), 0)::bigint        as p_imp,
               coalesce(sum(s.clicks) filter (where s.in_cur), 0)::bigint             as c_clicks,
               coalesce(sum(s.clicks) filter (where s.in_pri), 0)::bigint             as p_clicks,
               coalesce(sum(s.link_clicks) filter (where s.in_cur), 0)::bigint        as c_lc,
               coalesce(sum(s.link_clicks) filter (where s.in_pri), 0)::bigint        as p_lc,
               coalesce(sum(s.landing_page_views) filter (where s.in_cur), 0)::bigint as c_lpv,
               coalesce(sum(s.landing_page_views) filter (where s.in_pri), 0)::bigint as p_lpv,
               coalesce(sum(s.conversions) filter (where s.in_cur), 0)::bigint        as c_conv,
               coalesce(sum(s.conversions) filter (where s.in_pri), 0)::bigint        as p_conv,
               -- Across BOTH windows, as in 017.
               count(distinct s.ccy)::integer                               as n_ccy,
               (count(distinct s.camp_key)
                    filter (where not coalesce(c.single_goal, false)))::integer as n_multi
          from scan s
          -- `is not distinct from`: an ad whose campaign key is NULL must not
          -- fall out of its goal's total by failing to join.
          left join camp c on c.camp_key is not distinct from s.camp_key
         where s.goal is not null
         group by grouping sets ((s.goal), (s.goal, s.camp_key))
    ),
    base as (
        select a.*,
               (a.n_ccy > 1)                                                as mixed,
               ads.rate(a.c_imp, a.c_clicks, a.c_lc, a.c_spend, a.c_conv, a.c_lpv) as c_rates,
               ads.rate(a.p_imp, a.p_clicks, a.p_lc, a.p_spend, a.p_conv, a.p_lpv) as p_rates,
               -- UNROUNDED, for the contribution identity only.
               a.c_spend / nullif(a.c_conv, 0)                              as c_cpa_u,
               a.p_spend / nullif(a.p_conv, 0)                              as p_cpa_u,
               least(a.c_conv, a.p_conv)                                    as conv_min
          from agg a
         -- Goal rows always; campaign rows only for single-goal campaigns.
         where a.is_goal
            or (a.single_goal and a.ckey is not null)
    ),
    m as (
        select b.*,
               case when not b.mixed then (b.c_rates ->> 'cpa')::numeric end as c_cpa,
               case when not b.mixed then (b.p_rates ->> 'cpa')::numeric end as p_cpa
          from base b
    ),
    x as (
        select mm.*,
               round(100.0 * (mm.c_cpa - mm.p_cpa) / nullif(mm.p_cpa, 0), 2)   as cpa_chg,
               round(mm.c_spend - mm.c_conv * mm.p_cpa, 2)                    as excess,
               case
                   when not mm.has_pri or mm.p_conv = 0 or mm.mixed           then null
                   when mm.conv_min >= p_conv_strong                          then 'strong'
                   when mm.conv_min >= p_conv_moderate                        then 'moderate'
                   when mm.conv_min >= p_conv_directional                     then 'directional'
                   -- Zero conversions on k prior CPAs of spend: P(0) = e^-k.
                   when mm.c_conv = 0
                        and round(mm.c_spend / nullif(mm.p_cpa, 0), 2) >= 5   then 'strong'
                   when mm.c_conv = 0
                        and round(mm.c_spend / nullif(mm.p_cpa, 0), 2) >= 3   then 'moderate'
                   when mm.c_conv = 0
                        and round(mm.c_spend / nullif(mm.p_cpa, 0), 2) >= 2   then 'directional'
                   else                                                            'insufficient'
               end                                                            as ev_raw
          from m mm
    ),
    lv as (
        select xx.*,
               case when v_capped and xx.ev_raw in ('strong', 'moderate')
                    then 'directional' else xx.ev_raw end                      as evidence
          from x xx
    ),
    -- Every row beside its goal's totals. A goal row joins itself, so its
    -- contribution is the goal's own CPA change.
    par as (
        select l.*,
               gr.mixed     as g_mixed,
               gr.c_spend   as g_c_spend,
               gr.c_conv    as g_c_conv,
               gr.p_conv    as g_p_conv,
               gr.c_cpa_u   as g_c_cpa_u,
               gr.p_cpa_u   as g_p_cpa_u
          from lv l
          left join lv gr on gr.is_goal and gr.gl = l.gl
    ),
    ctb as (
        select p.*,
               case when not p.g_mixed
                    then p.c_spend / nullif(p.g_c_conv, 0)
                       - p.p_spend / nullif(p.g_p_conv, 0) end                 as contrib_u,
               (p.g_c_cpa_u - p.g_p_cpa_u)                                     as g_change_u,
               -- CASE, not AND: AND does not promise to evaluate left to
               -- right, and ln() must never see a zero or a NULL ratio.
               case when not p.g_mixed and p.g_c_cpa_u > 0 and p.g_p_cpa_u > 0
                    then abs(ln(p.g_c_cpa_u / p.g_p_cpa_u)) >= v_flat
                    else false end                                             as g_moved
          from par p
    ),
    sh as (
        select c.*,
               case when not c.is_goal and c.g_moved
                    then 100 * c.contrib_u / nullif(c.g_change_u, 0) end        as share_u,
               -- Contribution in the direction of the goal's move.
               case when c.g_change_u < 0 then 0 - c.contrib_u
                    else c.contrib_u end                                       as dir_key
          from ctb c
    ),
    rk as (
        select s.*,
               case when not s.is_goal
                    then rank() over (partition by s.is_goal, s.gl
                                      order by s.dir_key desc nulls last) end  as c_rank,
               case when not s.is_goal
                    then sum(s.share_u) over (partition by s.is_goal, s.gl
                                              order by s.dir_key desc nulls last, s.ckey
                                              rows between unbounded preceding
                                                       and current row) end    as cum_u,
               sum(case when s.is_goal then 0 else 1 end)
                   over (partition by s.gl)                                    as n_camps
          from sh s
    )
    select (case when r.is_goal then 'goal' else 'campaign' end)::text,
           r.gl::text,
           (case when not r.is_goal then r.ckey end)::uuid,
           (case when not r.is_goal then cmp.name end)::text,
           r.c_spend::numeric,
           r.p_spend::numeric,
           r.c_conv::bigint,
           r.p_conv::bigint,
           r.c_cpa::numeric,
           r.p_cpa::numeric,
           r.cpa_chg::numeric,
           r.excess::numeric,
           r.evidence::text,
           (round(r.excess * (case r.evidence when 'strong'       then 1.0
                                              when 'moderate'     then 0.8
                                              when 'directional'  then 0.5
                                              when 'insufficient' then 0.0
                                              end), 2))::numeric,
           (round(r.contrib_u, 4))::numeric,
           (round(r.share_u, 2))::numeric,
           (round(r.cum_u, 2))::numeric,
           r.c_rank::integer,
           r.n_camps::integer,
           (case when r.is_goal then r.n_multi end)::integer,
           r.mixed::boolean
      from rk r
      left join ads.campaign cmp on not r.is_goal and cmp.campaign_key = r.ckey
     -- Goals by their spend (dollars, so an order across goals is honest),
     -- the goal row first, then its campaigns in contribution order.
     order by r.g_c_spend desc nulls last,
              r.gl,
              r.is_goal desc,
              r.c_rank nulls last,
              r.c_spend desc,
              r.ckey;
end;
$fn$;

comment on function ads.goal_impact is
    'Per optimization goal and per single-goal campaign across two '
    'non-overlapping windows: spend, conversions, ads.rate CPA, excess_cost '
    '(current spend beyond current conversions at the prior CPA, in dollars), '
    'impact_score (excess weighted by 017''s evidence level), and each '
    'campaign''s contribution to its goal''s CPA change -- cpa_bridge''s '
    'identity within the goal -- with its share, running share and rank inside '
    'the goal. Dollars may be ranked across goals; CPA and contribution never '
    'are. Multi-goal campaigns get no row and are counted on the goal row.';


-- ---------------------------------------------------------------------------
-- 2. A facet's values within each goal, against that goal's own CPA.
--
-- p_dimension is a STATIC CASE with every branch named, as in 013: it is the
-- argument most obviously shaped like a column name inside a definer
-- function. The same six dimensions as ads.facet_performance, so the two
-- verbs cannot disagree about what a dimension is.
-- ---------------------------------------------------------------------------
create function ads.facet_by_goal(
    p_brand_id           uuid,
    p_since              date,
    p_until              date,
    p_dimension          text,
    -- Within +/- this percent of the goal's CPA is 'similar'.
    p_relative_band_pct  numeric default 10,
    p_conv_directional   integer default 5,
    p_conv_moderate      integer default 20,
    p_conv_strong        integer default 50
) returns table (
    optimization_goal  text,
    value              text,
    ads_run            integer,
    spend              numeric,
    conversions        bigint,
    rates              jsonb,
    goal_cpa           numeric,
    relative_pct       numeric,
    relative           text,
    evidence_level     text,
    spend_share_pct    numeric,
    mixed_currency     boolean
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
#variable_conflict use_column
begin
    if p_dimension is null
       or p_dimension not in ('family', 'hook', 'offer', 'audience', 'format', 'angle') then
        raise exception 'unknown dimension %, expected family, hook, offer, '
                        'audience, format or angle', p_dimension
            using hint = 'hook, offer and audience are ads.ad_facet columns; '
                         'format is derived on ads.ad and is not a facet at all.';
    end if;

    if p_since is null or p_until is null then
        raise exception 'since and until are both required';
    end if;

    if p_since > p_until then
        raise exception 'window is inverted: since % is after until %', p_since, p_until;
    end if;

    if p_relative_band_pct is null or p_relative_band_pct < 0 then
        raise exception 'p_relative_band_pct must be zero or more, got %', p_relative_band_pct;
    end if;

    return query
    with scoped as (
        select case p_dimension
                   when 'family'   then ang.family
                   when 'hook'     then fe.hook
                   when 'offer'    then fe.offer
                   when 'audience' then fe.audience
                   when 'format'   then d.format
                   when 'angle'    then ang.slug
                   else                 null
               end                       as dim_value,
               g.optimization_goal       as goal,
               f.ad_key                  as ad_key,
               f.currency                as ccy,
               f.spend                   as spend,
               f.impressions             as impressions,
               f.clicks                  as clicks,
               f.link_clicks             as link_clicks,
               f.landing_page_views      as landing_page_views,
               f.conversions             as conversions
          from ads.fact_ad_day f
          -- Every join LEFT, for 013's reason: the population is spend in the
          -- window, and the untagged bucket must not vanish with its rows.
          left join ads.facet_effective fe on fe.ad_key = f.ad_key
          left join ads.angle ang          on ang.id = fe.angle_id
          left join ads.ad d               on d.ad_key = f.ad_key
          left join ads.ad_group g         on g.ad_group_key = d.ad_group_key
         where f.brand_id = p_brand_id
           and f.day between p_since and p_until
    ),
    agg as (
        select (grouping(s.dim_value) = 1)                     as is_total,
               s.goal                                          as gl,
               s.dim_value                                     as dv,
               count(distinct s.ad_key)::integer               as n_ads,
               coalesce(sum(s.spend), 0)::numeric              as sp,
               coalesce(sum(s.conversions), 0)::bigint         as cv,
               count(distinct s.ccy)::integer                  as n_ccy,
               ads.rate(sum(s.impressions)::bigint,
                        sum(s.clicks)::bigint,
                        sum(s.link_clicks)::bigint,
                        sum(s.spend)::numeric,
                        sum(s.conversions)::bigint,
                        sum(s.landing_page_views)::bigint)     as rt
          from scoped s
         group by grouping sets ((s.goal, s.dim_value), (s.goal))
    ),
    tot as (
        select t.gl                                            as gl,
               t.sp                                            as g_sp,
               t.n_ccy                                         as g_ccy,
               -- The goal's own CPA, read out of ads.rate's object. Not
               -- defined for the unknown goal, which is a mixture.
               case when t.gl is not null and t.n_ccy = 1
                    then (t.rt ->> 'cpa')::numeric end         as g_cpa
          from agg t
         where t.is_total
    ),
    vals as (
        select a.*,
               tt.g_sp                                         as g_sp,
               tt.g_ccy                                        as g_ccy,
               tt.g_cpa                                        as g_cpa,
               (a.rt ->> 'cpa')::numeric                       as v_cpa,
               row_number() over (partition by a.gl, (a.dv is not null)
                                  order by a.sp desc, a.dv)    as sp_rn
          from agg a
          join tot tt on tt.gl is not distinct from a.gl
         where not a.is_total
    ),
    rel as (
        select v.*,
               round(100.0 * (v.v_cpa - v.g_cpa) / nullif(v.g_cpa, 0), 2) as rel_pct
          from vals v
    )
    select r.gl::text,
           r.dv::text,
           r.n_ads::integer,
           r.sp::numeric,
           r.cv::bigint,
           r.rt::jsonb,
           r.g_cpa::numeric,
           r.rel_pct::numeric,
           (case
                when r.dv is null or r.gl is null or r.g_ccy <> 1     then null
                when r.sp_rn = 1                                      then 'baseline'
                when r.rel_pct is null                                then null
                when r.rel_pct <= 0 - p_relative_band_pct             then 'better'
                when r.rel_pct >= p_relative_band_pct                 then 'worse'
                else                                                       'similar'
            end)::text,
           (case
                when r.cv >= p_conv_strong                            then 'strong'
                when r.cv >= p_conv_moderate                          then 'moderate'
                when r.cv >= p_conv_directional                       then 'directional'
                else                                                       'insufficient'
            end)::text,
           (case when r.g_ccy = 1
                 then round(100.0 * r.sp / nullif(r.g_sp, 0), 2) end)::numeric,
           (r.g_ccy > 1)::boolean
      from rel r
     -- Goals by their spend, then values by theirs, untagged last in a tie.
     order by r.g_sp desc nulls last,
              r.gl nulls last,
              r.sp desc,
              r.dv nulls last;
end;
$fn$;

comment on function ads.facet_by_goal is
    'One facet dimension (hook, offer, audience, format, family or angle) per '
    'optimization goal: spend, conversions, ads.rate rates, the goal''s own '
    'CPA, the value''s CPA relative to it (baseline = largest-spend tagged '
    'value; better / similar / worse at +/- p_relative_band_pct), an evidence '
    'level from its conversions and its share of the goal''s spend. Nothing '
    'is compared across goals. The untagged bucket is value NULL and is never '
    'labelled.';


-- ---------------------------------------------------------------------------
-- 3. One entity, before and after a date.
--
-- funnel_change with current = after and prior = before, narrowed to one
-- entity. Every column is listed and cast rather than `fc.*`, so a change to
-- funnel_change's result type fails here at create time instead of on the
-- first read.
-- ---------------------------------------------------------------------------
create function ads.entity_outcome(
    p_brand_id          uuid,
    p_level             text,
    p_entity_key        uuid,
    p_before_since      date,
    p_before_until      date,
    p_after_since       date,
    p_after_until       date,
    p_settled_through   date
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
    disappeared                    boolean,
    after_settled_days             integer
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
#variable_conflict use_column
declare
    v_settled_days integer;
begin
    if p_level is null or p_entity_key is null then
        raise exception 'a level and an entity key are both required';
    end if;

    if p_after_since is null or p_after_until is null then
        raise exception 'the after window needs both dates';
    end if;

    -- Days of the after window whose numbers have stopped moving. NULL when
    -- the edge is unknown: "none are settled" is a different statement.
    v_settled_days := case when p_settled_through is not null
                           then greatest(0, least(p_after_until, p_settled_through)
                                            - p_after_since + 1) end;

    -- Level, window and overlap checks are funnel_change's own, and raise
    -- from there with its messages.
    return query
    select fc.level::text,
           fc.entity_key::uuid,
           fc.entity_name::text,
           fc.entity_status::text,
           fc.campaign_key::uuid,
           fc.campaign_name::text,
           fc.ad_group_key::uuid,
           fc.ad_group_name::text,
           fc.parent_key::uuid,
           fc.optimization_goal::text,
           fc.optimization_goal_count::integer,
           fc.equal_length::boolean,
           fc.cur_unsettled_days::integer,
           fc.cur_m::jsonb,
           fc.pri_m::jsonb,
           fc.pct::jsonb,
           fc.cpa_direction::text,
           fc.decomposition::text,
           fc.cpa_share_cpm_pct::numeric,
           fc.cpa_share_link_ctr_pct::numeric,
           fc.cpa_share_conversion_rate_pct::numeric,
           fc.primary_driver::text,
           fc.link_cpc_share_cpm_pct::numeric,
           fc.link_cpc_share_link_ctr_pct::numeric,
           fc.spend_to_baseline_cpa::numeric,
           fc.baseline_cpa::numeric,
           fc.decision_spend_per_arm::numeric,
           fc.evidence_level::text,
           fc.click_evidence_level::text,
           fc.parent_spend_share_pct::numeric,
           fc.parent_spend_share_pct_prior::numeric,
           fc.parent_spend_share_change_pts::numeric,
           fc.parent_conversion_share_pct::numeric,
           fc.spend_rank_in_parent::integer,
           fc.parent_cpa_effect::numeric,
           fc.currencies::integer,
           fc.mixed_currency::boolean,
           fc.fatigue_gate_ok::boolean,
           fc.fatigue_frequency_up::boolean,
           fc.fatigue_link_ctr_down::boolean,
           fc.fatigue_link_cpc_up::boolean,
           fc.fatigue_cvr_stable::boolean,
           fc.fatigue_legs_held::integer,
           fc.fatigue_pattern::text,
           fc.fatigue_consistent::boolean,
           fc.appeared::boolean,
           fc.disappeared::boolean,
           v_settled_days::integer
      from ads.funnel_change(p_brand_id, p_level,
                             p_after_since, p_after_until,
                             p_before_since, p_before_until,
                             p_settled_through) fc
     where fc.entity_key = p_entity_key;
end;
$fn$;

comment on function ads.entity_outcome is
    'One entity''s ads.funnel_change row with current = after and prior = '
    'before a date (an implementation day), plus after_settled_days: the days '
    'of the after window at or before the settled edge. No row when the '
    'entity ran in neither window. A reading, never a conclusion.';


-- ---------------------------------------------------------------------------
-- 4. Two ads over one window: a test's challenger against its control.
--
-- Always one row: an arm with no delivery reads as zeros and '{}' rates,
-- which is the fact ("it has not spent"), rather than no row, which reads as
-- a failed query.
-- ---------------------------------------------------------------------------
create function ads.arm_compare(
    p_brand_id           uuid,
    p_control_ad_key     uuid,
    p_challenger_ad_key  uuid,
    p_since              date,
    p_until              date,
    p_settled_through    date,
    p_decision_spend     numeric,
    p_conv_directional   integer default 5,
    p_conv_moderate      integer default 20,
    p_conv_strong        integer default 50
) returns table (
    control_m          jsonb,
    challenger_m       jsonb,
    effect_pct         jsonb,
    evidence_level     text,
    spend_sufficient   boolean,
    settled_days       integer
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
#variable_conflict use_column
declare
    v_capped       boolean;
    v_settled_days integer;
begin
    if p_control_ad_key is null or p_challenger_ad_key is null then
        raise exception 'both a control and a challenger ad key are required';
    end if;

    if p_control_ad_key = p_challenger_ad_key then
        raise exception 'control and challenger are the same ad %', p_control_ad_key;
    end if;

    if p_since is null or p_until is null then
        raise exception 'since and until are both required';
    end if;

    if p_since > p_until then
        raise exception 'window is inverted: since % is after until %', p_since, p_until;
    end if;

    v_capped       := coalesce(p_until > p_settled_through, true);
    v_settled_days := case when p_settled_through is not null
                           then greatest(0, least(p_until, p_settled_through)
                                            - p_since + 1) end;

    return query
    with arms (arm, akey) as (
        values ('control'::text,    p_control_ad_key),
               ('challenger'::text, p_challenger_ad_key)
    ),
    per as (
        select a.arm                                                  as arm,
               coalesce(sum(f.spend), 0)::numeric                     as sp,
               coalesce(sum(f.impressions), 0)::bigint                as imp,
               coalesce(sum(f.clicks), 0)::bigint                     as clk,
               coalesce(sum(f.link_clicks), 0)::bigint                as lc,
               coalesce(sum(f.landing_page_views), 0)::bigint         as lpv,
               coalesce(sum(f.conversions), 0)::bigint                as cv,
               count(distinct f.day)::integer                         as days_active
          from arms a
          left join ads.fact_ad_day f
                 on f.ad_key = a.akey
                and f.brand_id = p_brand_id
                and f.day between p_since and p_until
         group by a.arm
    ),
    pr as (
        select q.*,
               ads.rate(q.imp, q.clk, q.lc, q.sp, q.cv, q.lpv)        as rt
          from per q
    ),
    -- Across both arms: money is not compared across currencies.
    ccy as (
        select count(distinct f.currency)::integer                    as n
          from ads.fact_ad_day f
         where f.brand_id = p_brand_id
           and f.ad_key in (p_control_ad_key, p_challenger_ad_key)
           and f.day between p_since and p_until
    ),
    two as (
        select co.sp          as co_sp,
               co.imp         as co_imp,
               co.lc          as co_lc,
               co.cv          as co_cv,
               co.days_active as co_days,
               co.rt          as co_rt,
               ch.sp          as ch_sp,
               ch.imp         as ch_imp,
               ch.lc          as ch_lc,
               ch.cv          as ch_cv,
               ch.days_active as ch_days,
               ch.rt          as ch_rt,
               (cc.n > 1)     as mixed,
               least(co.cv, ch.cv) as conv_min
          from pr co
          join pr ch on ch.arm = 'challenger'
          cross join ccy cc
         where co.arm = 'control'
    ),
    ev as (
        select t.*,
               case
                   when t.conv_min >= p_conv_strong      then 'strong'
                   when t.conv_min >= p_conv_moderate    then 'moderate'
                   when t.conv_min >= p_conv_directional then 'directional'
                   else                                       'insufficient'
               end                                            as ev_raw
          from two t
    )
    select jsonb_build_object(
               'spend',       e.co_sp,
               'impressions', e.co_imp,
               'link_clicks', e.co_lc,
               'conversions', e.co_cv,
               'days_active', e.co_days,
               'rates',       e.co_rt)::jsonb,
           jsonb_build_object(
               'spend',       e.ch_sp,
               'impressions', e.ch_imp,
               'link_clicks', e.ch_lc,
               'conversions', e.ch_cv,
               'days_active', e.ch_days,
               'rates',       e.ch_rt)::jsonb,
           jsonb_strip_nulls(jsonb_build_object(
               'cpa',
               case when not e.mixed
                    then round(100.0 * ((e.ch_rt ->> 'cpa')::numeric - (e.co_rt ->> 'cpa')::numeric)
                               / nullif((e.co_rt ->> 'cpa')::numeric, 0), 2) end,
               'link_ctr',
               round(100.0 * ((e.ch_rt ->> 'link_ctr')::numeric - (e.co_rt ->> 'link_ctr')::numeric)
                     / nullif((e.co_rt ->> 'link_ctr')::numeric, 0), 2),
               'conversion_rate',
               round(100.0 * ((e.ch_rt ->> 'conversion_rate')::numeric
                              - (e.co_rt ->> 'conversion_rate')::numeric)
                     / nullif((e.co_rt ->> 'conversion_rate')::numeric, 0), 2),
               'cost_per_link_click',
               case when not e.mixed
                    then round(100.0 * ((e.ch_rt ->> 'cost_per_link_click')::numeric
                                        - (e.co_rt ->> 'cost_per_link_click')::numeric)
                               / nullif((e.co_rt ->> 'cost_per_link_click')::numeric, 0), 2) end
           ))::jsonb,
           (case when v_capped and e.ev_raw in ('strong', 'moderate')
                 then 'directional' else e.ev_raw end)::text,
           (case when p_decision_spend is not null
                 then e.co_sp >= p_decision_spend and e.ch_sp >= p_decision_spend end)::boolean,
           v_settled_days::integer
      from ev e;
end;
$fn$;

comment on function ads.arm_compare is
    'Two ads over one window -- a test''s control and challenger running side '
    'by side: each arm''s spend, impressions, link clicks, conversions and '
    'ads.rate rates, the challenger''s percent effect against the control on '
    'cpa, link_ctr, conversion_rate and cost_per_link_click, an evidence level '
    'from the smaller arm''s conversions (capped over unsettled days), whether '
    'both arms reached p_decision_spend, and the settled days in the window. '
    'A measured reading; it never names a winner.';


-- Owner and grant, 016's and 017's pattern and for 013's reasons: ads_migrate
-- applies as postgres, so default privileges never fire, and a definer
-- function left owned by postgres reads AROUND RLS where every other one reads
-- THROUGH it. Full signatures, defaults included, because a function is
-- identified by its argument types.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_owner') then
        raise notice 'ads_owner does not exist; skipping ownership reassignment';
        return;
    end if;
    execute 'alter function ads.goal_impact(uuid, date, date, date, date, date, '
            'numeric, integer, integer, integer) '
            'owner to ads_owner';
    execute 'alter function ads.facet_by_goal(uuid, date, date, text, '
            'numeric, integer, integer, integer) '
            'owner to ads_owner';
    execute 'alter function ads.entity_outcome(uuid, text, uuid, date, date, date, date, date) '
            'owner to ads_owner';
    execute 'alter function ads.arm_compare(uuid, uuid, uuid, date, date, date, numeric, '
            'integer, integer, integer) '
            'owner to ads_owner';
end;
$$;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_reader') then
        raise notice 'ads_reader does not exist; skipping grant';
        return;
    end if;
    execute 'grant execute on function ads.goal_impact(uuid, date, date, date, date, date, '
            'numeric, integer, integer, integer) '
            'to ads_reader';
    execute 'grant execute on function ads.facet_by_goal(uuid, date, date, text, '
            'numeric, integer, integer, integer) '
            'to ads_reader';
    execute 'grant execute on function ads.entity_outcome(uuid, text, uuid, date, date, date, date, date) '
            'to ads_reader';
    execute 'grant execute on function ads.arm_compare(uuid, uuid, uuid, date, date, date, numeric, '
            'integer, integer, integer) '
            'to ads_reader';
end;
$$;

commit;
