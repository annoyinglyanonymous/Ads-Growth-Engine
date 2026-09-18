-- ---------------------------------------------------------------------------
-- 005  Experiment memory.
--
-- WHAT THIS IS FOR
--
-- So that the agent stops proposing "let's test no broker fees" for the seventh
-- time. An angle bank says what could be tested; this says what was, when, and
-- what came of it.
--
-- FOUR THINGS, FOUR AUTHORS. Conflating them is how an agent ends up approving
-- something:
--
--   the proposal    the agent's, and an opinion -- exactly like an ad_reviews row
--   the launch      hers, in Ads Manager, outside this system entirely
--   the result      computed from ads.fact_ad_day, never typed by anyone
--   the conclusion  hers, and the only thing in this file carrying a signature
--
-- THERE IS NO winner COLUMN AND NO verdict COLUMN.
--
-- 044 has no status and no approved_by, and says why: "Nothing reads a row in
-- this table as permission and nothing ever should." The same applies here.
-- ads.experiment_result reports whether the pre-registered effect was cleared
-- and whether each arm has enough spend to be read. It does not name a winner,
-- because naming a winner is the decision, and the decision is hers.
--
-- WHY THE THRESHOLDS ARE REGISTERED BEFORE THE RESULT EXISTS
--
-- minimum_effect_pct and minimum_spend_per_arm are NOT NULL and are set when
-- the experiment is proposed -- before anybody has looked at a number. That is
-- the entire mechanism against reading a 12% difference on $80 of spend as a
-- result. Decide what would convince you while you still don't know the answer,
-- or you will be convinced by whatever you find.
-- ---------------------------------------------------------------------------

begin;

create table ads.experiment (
    id             uuid primary key default gen_random_uuid(),
    brand_id       uuid not null references public.brands (id) on delete cascade,

    -- A name, never a bare id. CLAUDE.md's first principle: a person has to be
    -- able to say which one they mean out loud.
    name           text not null check (length(btrim(name)) > 0),

    -- What we are actually asking, in a sentence that could be wrong.
    question       text not null check (length(btrim(question)) > 0),
    -- Falsifiable, and tied to the metric below -- the angles skill already
    -- holds campaign hypotheses to this shape.
    hypothesis     text not null check (length(btrim(hypothesis)) > 0),

    primary_metric text not null
                   check (primary_metric in ('cpa', 'cpc', 'cost_per_link_click',
                                             'ctr', 'link_ctr', 'cpm',
                                             'conversion_rate')),

    -- Both registered up front. See the header.
    minimum_effect_pct    numeric not null check (minimum_effect_pct > 0),
    minimum_spend_per_arm numeric not null check (minimum_spend_per_arm > 0),

    -- Facts about a window, not sign-off. Set in the UI once she has actually
    -- launched it; deliberately absent from the agent's accepted write shape.
    started_on     date,
    ended_on       date,

    -- cli:<user> is normal and honest here. A proposal has an author, and the
    -- author being an agent is information, not a problem.
    proposed_by    text not null check (length(btrim(proposed_by)) > 0),
    context_id     uuid,
    knowledge_snapshot jsonb,

    -- Hers. No default, no agent path, and no intel verb writes these.
    conclusion     text,
    concluded_by   text,
    concluded_at   timestamptz,

    created_at     timestamptz not null default now(),

    unique (brand_id, name),

    constraint experiment_window_ordered
        check (started_on is null or ended_on is null or started_on <= ended_on),

    -- 027's coherence stance: a conclusion with nobody's name on it is a row
    -- nobody can act on, and a name with no time on it is a row nobody can
    -- audit. All three move together or none of them do.
    constraint experiment_conclusion_attributed
        check ((conclusion is null) = (concluded_by is null)
               and (concluded_by is null) = (concluded_at is null))
);

create index experiment_brand_idx on ads.experiment (brand_id, created_at desc);

comment on table ads.experiment is
    'What we decided to test, why, and what would have counted as an answer -- '
    'registered before the numbers existed. No winner column and no verdict '
    'column: ads.experiment_result reports sufficiency and effect, and naming a '
    'winner is a decision a person makes (044 takes the same stance).';


-- ---------------------------------------------------------------------------
-- How an ad joins an arm. EXACTLY ONE rule per arm, so membership is never
-- ambiguous and never overlapping-by-accident.
--
--   angle_id    every ad tagged with this angle        (the usual case)
--   utm_content the slot tracking.py stamped           ('ad-a-v5')
--   ad_keys     a literal list                         (when nothing else fits)
--
-- Two rules on one arm would mean an ad could be in the arm by one and out by
-- the other, and the resulting number would depend on which clause the query
-- happened to evaluate.
-- ---------------------------------------------------------------------------
create table ads.experiment_arm (
    experiment_id uuid not null references ads.experiment (id) on delete cascade,
    label         text not null check (length(btrim(label)) > 0),

    angle_id      uuid references ads.angle (id),
    utm_content   text,
    ad_keys       uuid[],

    primary key (experiment_id, label),

    constraint experiment_arm_one_rule
        check ((angle_id is not null)::int
             + (utm_content is not null)::int
             + (ad_keys is not null)::int = 1)
);


-- ---------------------------------------------------------------------------
-- The result. Computed, never typed.
--
-- effect_pct is measured against the FIRST arm by label order, which is the
-- control by convention. The function does not decide which arm is the control,
-- because that is not knowable from the data -- it reports the comparison and
-- names the baseline it used.
--
-- For every metric here except ctr, link_ctr and conversion_rate, LOWER IS
-- BETTER (they are costs). effect_pct is signed raw -- negative means the arm
-- came in below the baseline -- and `improvement` carries the direction so no
-- caller has to remember which way round each metric runs.
-- ---------------------------------------------------------------------------
create function ads.experiment_result(p_experiment_id uuid)
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
          left join ads.ad_facet fa on fa.ad_key = f.ad_key
          left join ads.ad d        on d.ad_key  = f.ad_key
         where arm.experiment_id = p_experiment_id
           and (
                (arm.angle_id    is not null and fa.angle_id = arm.angle_id)
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
    'experiment that was never launched.';

commit;
