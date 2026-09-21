-- ---------------------------------------------------------------------------
-- 013  The verbs that read the taxonomy back.
--
-- 004 built the vocabulary. 010 built the view that finally collects it. What
-- neither of them built is a question. Four of the five functions below exist
-- because a question this repo has already written down has no verb:
--
--   004:29-30    "do financial angles beat emotional ones for us" -- the
--                sentence that justifies families being GLOBAL while angles
--                are per brand. Nothing has ever grouped by family. `hook`,
--                `offer` and `audience` are written by `intel record` onto
--                every tag and read by NOTHING. ads.facet_performance is the
--                verb that was missing.
--
--   010:186-190  deferred optimization_goal out of angle_coverage on purpose:
--                "The honest version is a separate function that partitions by
--                goal and returns NULL for a rank it cannot compute. It
--                belongs with angle ranking, not here." This is that function.
--                angle_coverage orders by spend and never sorts on CPA;
--                intel/angles.py:169's `versus` compares exactly two. Nothing
--                ranks N angles, which is what anybody actually asks for.
--
--   intel/metrics.py:93-97 documents attributable_share as something `why`
--                reports -- "says how much of the move the split actually
--                accounts for. Reporting the split as if it covered everything
--                would be the interesting half of a lie" -- and `why` does not
--                return it. The caller cannot derive it either, because
--                CLAUDE.md forbids it: "You never compute a rate, a delta or a
--                share yourself." So the number is unreachable rather than
--                merely absent, and the honest sentence the docstring promises
--                has never been available. ads.cpa_bridge_totals returns it.
--
--   intel/angles.py:122 sums untagged spend in Python over the rows `queue`
--                fetched, and `queue` takes `limit: int = 50` (angles.py:86).
--                The headline therefore reports whatever the fifty most
--                expensive untagged ads happen to add up to, and it goes DOWN
--                as tagging gets worse in the tail, because a longer tail
--                pushes rows past the limit. A total capped by a display limit
--                is worse than no total: it is wrong in the reassuring
--                direction.
--
-- The fifth, ads.match_health, is not a reporting gap. It is the missing alarm
-- on the one manual step in this entire pipeline.
--
-- WHAT EVERY FUNCTION HERE INHERITS FROM 003's HEADER
--
-- security definer with search_path pinned, stable, no dynamic SQL, no read of
-- current_date, nullif on every denominator, and every rate out of ads.rate.
-- Two of those do real work in this file rather than being recited:
--
--   p_dimension in ads.facet_performance is a STATIC CASE with all six
--   branches named. It is the argument most obviously shaped like a column
--   name, inside a definer function, which is 003:22-25's privilege escalation
--   with extra steps. Naming every branch also means a seventh dimension
--   cannot arrive by accident through an `else`.
--
--   cpa is read out of the `rates` object ads.rate built, never divided by
--   hand, even where it is only being used to sort. 003:34-35 is about one
--   number meaning one thing everywhere, and a sort key computed differently
--   from the value printed beside it is that same disagreement in a hat.
--
-- THE RANK COLUMN IS THE POINT OF THE FIRST TWO FUNCTIONS
--
-- 042, CLAUDE.md and intel/angles.py:47-48 all say the same thing in prose:
-- CPA is not comparable across optimization goals. Prose has been the entire
-- defence, and prose loses to a column of numbers that sorts.
--
-- rank_within_goal is NULL wherever the rows being ranked did not all run
-- under one known goal. Not omitted, not flagged, not footnoted -- absent. The
-- cross-goal ranking becomes something the database cannot produce, so
-- producing it would require a caller to sit down and do arithmetic it has
-- been told not to do. optimization_goals is returned beside it as the
-- evidence: which goals ran and what each one spent, so a reader can see WHY
-- the rank is missing and decide for themselves. The function returns the
-- evidence and declines the judgement, which is review/context.py:121's rule
-- applied to a number instead of a word.
-- ---------------------------------------------------------------------------

begin;

-- ---------------------------------------------------------------------------
-- 1. Every angle, ranked -- where ranking is honest, and nowhere else.
--
-- Reads ads.facet_effective and never ads.ad_facet. 010 is emphatic about why:
-- ad_facet is keyed (ad_key, copy_hash) and accumulates one row per wording
-- ever tagged, so `join ads.ad_facet on ad_key` multiplies an ad's spend by
-- its number of historical tags -- which is the direction the key exists to
-- prevent. facet_effective holds at most one row per ad by construction, and
-- it also carries the angle of every ad this engine shipped whether or not a
-- human ever tagged it.
--
-- WHAT IS HERE THAT angle_coverage DOES NOT HAVE
--
--   the full rate object     angle_coverage returns cpa alone. link_ctr and
--                            conversion_rate are how you tell "nobody clicks
--                            this angle" from "they click and do not convert",
--                            which are different problems with different fixes.
--
--   spend_share_pct and      "Earning its place" is not a CPA question. An
--   conversion_share_pct     angle taking 40% of the spend and returning 12%
--                            of the conversions is the finding, and it is
--                            invisible in a column of absolute numbers next to
--                            angles with different budgets. Both are window
--                            functions in SQL, because a caller dividing one
--                            row by a total it summed itself is the second
--                            definition of a share that CLAUDE.md exists to
--                            prevent.
--
--   the goal evidence        optimization_goals, its count, comparable_on_cost
--                            and the rank that refuses to exist without them.
--
-- THE SHARES ARE OVER THE ROWS THIS CALL RETURNS, which matters when
-- p_product_slug is passed: the denominator becomes that product's tagged
-- spend, so the answer reads "share within this product". That is the useful
-- reading and also the only available one -- a share against brand-wide spend
-- would have a denominator the caller cannot see anywhere in the result.
--
-- The denominator is TAGGED spend, not total spend. Untagged spend is not in
-- these rows at all, and the size of what is missing is ads.untagged_spend's
-- job below. A reader who wants "share of everything" needs both verbs, and
-- needing both is better than one verb quietly answering a smaller question.
-- ---------------------------------------------------------------------------
create function ads.angle_performance(
    p_brand_id     uuid,
    p_since        date,
    p_until        date,
    p_product_slug text default null,
    p_min_spend    numeric default 250
) returns table (
    angle_id                uuid,
    family                  text,
    angle_slug              text,
    angle_name              text,
    definition              text,
    ads_run                 integer,
    tagged_ads              integer,
    inherited_ads           integer,
    spend                   numeric,
    impressions             bigint,
    clicks                  bigint,
    link_clicks             bigint,
    landing_page_views      bigint,
    conversions             bigint,
    rates                   jsonb,
    spend_share_pct         numeric,
    conversion_share_pct    numeric,
    optimization_goals      jsonb,
    optimization_goal_count integer,
    comparable_on_cost      boolean,
    spend_sufficient        boolean,
    rank_within_goal        integer,
    first_run               date,
    last_run                date,
    state                   text
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
begin
    if p_since > p_until then
        raise exception 'window is inverted: since % is after until %', p_since, p_until;
    end if;

    return query
    with perf as (
        select fe.angle_id,
               count(distinct f.ad_key)::integer as ads_run,
               -- 010 added this split to angle_coverage and the reason holds
               -- here: an angle whose number is 90% inherited and one that is
               -- 90% judged read identically in a spend column, and they are
               -- not the same claim about the same quality of evidence.
               count(distinct f.ad_key) filter (
                   where fe.source <> 'inherited')::integer as tagged_ads,
               count(distinct f.ad_key) filter (
                   where fe.source =  'inherited')::integer as inherited_ads,
               sum(f.spend)                                 as spend,
               sum(f.impressions)::bigint                   as impressions,
               sum(f.clicks)::bigint                        as clicks,
               sum(f.link_clicks)::bigint                   as link_clicks,
               sum(f.landing_page_views)::bigint            as landing_page_views,
               sum(f.conversions)::bigint                   as conversions,
               min(f.day)                                   as first_run,
               max(f.day)                                   as last_run,
               -- Sum-over-sum for the window, through the one function that
               -- defines these (003:40-56). Never an average of the daily
               -- ratios public.meta_ad_insights already carries, which 002
               -- deliberately refuses to lift into this schema at all.
               ads.rate(sum(f.impressions)::bigint,
                        sum(f.clicks)::bigint,
                        sum(f.link_clicks)::bigint,
                        sum(f.spend),
                        sum(f.conversions)::bigint,
                        sum(f.landing_page_views)::bigint)  as rates
          from ads.fact_ad_day f
          join ads.facet_effective fe on fe.ad_key = f.ad_key
         where f.brand_id = p_brand_id
           and f.day between p_since and p_until
           and fe.angle_id is not null
         group by fe.angle_id
    ),
    -- The goal spread per angle, with what each goal spent. LEFT joined the
    -- whole way down: an ad whose ad_group the structure pull has not caught up
    -- with must not vanish out of the angle's spend. Losing spend out of a
    -- denominator is the failure this schema is least able to see, so the row
    -- stays and its goal is NULL, which is then treated as the unknown it is
    -- rather than as a goal.
    goals as (
        select fe.angle_id,
               g.optimization_goal as goal,
               sum(f.spend)        as spend
          from ads.fact_ad_day f
          join ads.facet_effective fe on fe.ad_key = f.ad_key
          left join ads.ad d          on d.ad_key = f.ad_key
          left join ads.ad_group g    on g.ad_group_key = d.ad_group_key
         where f.brand_id = p_brand_id
           and f.day between p_since and p_until
           and fe.angle_id is not null
         group by fe.angle_id, g.optimization_goal
    ),
    goal_roll as (
        select gg.angle_id,
               jsonb_agg(jsonb_build_object('goal', gg.goal, 'spend', gg.spend)
                         order by gg.spend desc nulls last) as optimization_goals,
               count(*)::integer                            as goal_count,
               -- The partition key for the rank, and it is NULL on purpose in
               -- two cases: more than one goal (the comparison is invalid) and
               -- exactly one goal that is itself NULL (the comparison is
               -- unknowable, because nothing here knows what those ads were
               -- optimising for). The second matters more than it looks:
               -- `partition by` treats NULLs as equal, so without this every
               -- unknown-goal angle would be ranked against every other
               -- unknown-goal angle inside one silent cross-goal partition --
               -- precisely the ranking this column exists to make unavailable.
               case when count(*) = 1 then min(gg.goal) end as only_goal
          from goals gg
         group by gg.angle_id
    ),
    base as (
        select a.id                             as angle_id,
               a.family,
               a.slug                            as angle_slug,
               a.name                            as angle_name,
               a.definition,
               coalesce(p.ads_run, 0)            as ads_run,
               coalesce(p.tagged_ads, 0)         as tagged_ads,
               coalesce(p.inherited_ads, 0)      as inherited_ads,
               coalesce(p.spend, 0)              as spend,
               coalesce(p.impressions, 0)        as impressions,
               coalesce(p.clicks, 0)             as clicks,
               coalesce(p.link_clicks, 0)        as link_clicks,
               coalesce(p.landing_page_views, 0) as landing_page_views,
               coalesce(p.conversions, 0)        as conversions,
               -- '{}' rather than NULL for an angle that never ran: ads.rate
               -- already returns an empty object when every rate is undefined
               -- (jsonb_strip_nulls), and one shape for "no rates" is one less
               -- thing every caller has to special-case.
               coalesce(p.rates, '{}'::jsonb)    as rates,
               coalesce(gr.optimization_goals, '[]'::jsonb) as optimization_goals,
               coalesce(gr.goal_count, 0)        as goal_count,
               gr.only_goal,
               p.first_run,
               p.last_run,
               -- angle_coverage's rule, unchanged, because two functions
               -- disagreeing about what "tested" means is worse than either
               -- definition being wrong. under_spent is the honest version of
               -- never_run: an angle that ran on $40 was glanced at.
               case
                   when p.angle_id is null                 then 'never_run'
                   when coalesce(p.spend, 0) < p_min_spend then 'under_spent'
                   else                                         'tested'
               end                               as state
          from ads.angle a
          left join perf      p  on p.angle_id  = a.id
          left join goal_roll gr on gr.angle_id = a.id
         where a.brand_id = p_brand_id
           -- status = 'active', as angle_coverage does. A proposed angle is
           -- something the agent suggested and nobody has signed (004:108-117),
           -- and ranking it beside signed ones would read as part of the bank.
           and a.status = 'active'
           and (p_product_slug is null or a.product_slug = p_product_slug)
    )
    select b.angle_id, b.family, b.angle_slug, b.angle_name, b.definition,
           b.ads_run, b.tagged_ads, b.inherited_ads,
           b.spend, b.impressions, b.clicks, b.link_clicks,
           b.landing_page_views, b.conversions,
           b.rates,
           round(100.0 * b.spend / nullif(sum(b.spend) over (), 0), 2),
           round(100.0 * b.conversions / nullif(sum(b.conversions) over (), 0), 2),
           b.optimization_goals,
           b.goal_count,
           (b.goal_count = 1),
           (b.spend >= p_min_spend),
           -- The refusal. NULL wherever comparable_on_cost is false, and also
           -- where the single goal is unknown (see only_goal above). cpa is
           -- read out of ads.rate's object rather than divided here, so the
           -- number that decides the order is the same number the caller
           -- prints. `nulls last` puts an angle that converted nothing at the
           -- bottom instead of letting an undefined cpa behave like a cheap
           -- one -- 003:48-51's argument, applied to a sort key.
           case when b.only_goal is not null
                then (rank() over (partition by b.only_goal
                                   order by (b.rates ->> 'cpa')::numeric nulls last))::integer
           end,
           b.first_run, b.last_run, b.state
      from base b
     -- Ordered by spend, not by rank. Ordering the whole result by
     -- rank_within_goal would interleave the rank-1 angle of one goal with the
     -- rank-1 angle of another and sit them adjacent on a screen, which is the
     -- cross-goal comparison the column refuses, reintroduced as a row order.
     order by b.spend desc, b.angle_name;
end;
$fn$;

comment on function ads.angle_performance is
    'Every active angle for a brand with what it spent, what it returned, its '
    'share of tagged spend and conversions, and its rank against the other '
    'angles that ran under the SAME optimization goal. rank_within_goal is '
    'NULL whenever those ads did not all run under one known goal, so a '
    'cross-goal CPA ranking is structurally unavailable rather than merely '
    'discouraged; optimization_goals is returned as the evidence for that '
    'refusal. Reads ads.facet_effective, so an ad the engine shipped counts '
    'even when nobody filed a tag for it.';


-- ---------------------------------------------------------------------------
-- 2. The same question asked of any one facet.
--
-- 004:29-30 is the whole justification for angle_family being a GLOBAL table
-- while angles are per brand: "'Financial' and 'Emotional' mean the same thing
-- for both brands, and 'do financial angles beat emotional ones for us' is a
-- question worth being able to ask across both." Nothing has ever asked it.
-- The same is true of hook, offer and audience -- `intel record` writes them
-- onto every tag, ads.ad_facet indexes them, and no verb reads them back.
--
-- p_dimension IS A STATIC CASE. Six branches, all named; the `else` is
-- reachable only if the validation above it is edited away. It is never an
-- interpolated column name, for 003:22-25's reason -- a SECURITY DEFINER
-- function that builds SQL out of an argument is a privilege escalation with
-- extra steps -- and this is the argument most obviously shaped like a column
-- name in the whole schema.
--
-- format is NOT a facet. It comes from ads.ad.format (002:153-171), which is
-- derived from the creative rather than judged by anybody. It is in this
-- function because "do videos beat statics for us" is the same question with
-- the same trap, and answering it in a second function would be a second place
-- for the optimization-goal guard to be left out.
--
-- family comes from ads.angle.family through facet_effective.angle_id, so an
-- ad reaches its family by exactly the path it reaches its angle, and the two
-- numbers cannot come to disagree.
--
-- THE UNKNOWN BUCKET IS RETURNED, NOT FILTERED. `value is null` means "ads
-- carrying no tag on this dimension", and it is usually the largest row in the
-- table. A reader who cannot see that two thirds of the spend has no hook tag
-- will read the other third as the whole picture -- the same reasoning 010
-- gives for surfacing ad_facet_stale instead of quietly dropping it. It is
-- never ranked, because "untagged" is not a hook.
-- ---------------------------------------------------------------------------
create function ads.facet_performance(
    p_brand_id  uuid,
    p_since     date,
    p_until     date,
    p_dimension text,
    p_min_spend numeric default 250
) returns table (
    dimension               text,
    value                   text,
    ads_run                 integer,
    spend                   numeric,
    conversions             bigint,
    rates                   jsonb,
    optimization_goals      jsonb,
    optimization_goal_count integer,
    comparable_on_cost      boolean,
    spend_sufficient        boolean,
    rank_within_goal        integer
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
begin
    if p_dimension not in ('family', 'hook', 'offer', 'audience', 'format', 'angle') then
        raise exception 'unknown dimension %, expected family, hook, offer, '
                        'audience, format or angle', p_dimension
            using hint = 'family and angle come from ads.angle through '
                         'facet_effective.angle_id; hook, offer and audience '
                         'are ads.ad_facet columns; format is derived on '
                         'ads.ad and is not a facet at all.';
    end if;

    if p_since > p_until then
        raise exception 'window is inverted: since % is after until %', p_since, p_until;
    end if;

    return query
    -- One scoped set, read twice. The totals and the goal spread MUST see
    -- exactly the same rows: a goal breakdown computed over a slightly
    -- different population than the spend it is explaining is a disagreement
    -- that never surfaces as an error.
    with scoped as (
        select case p_dimension
                   when 'family'   then ang.family
                   when 'hook'     then fe.hook
                   when 'offer'    then fe.offer
                   when 'audience' then fe.audience
                   when 'format'   then d.format
                   when 'angle'    then ang.slug
                   else                 null
               end                 as dim_value,
               g.optimization_goal as goal,
               f.ad_key, f.spend, f.impressions, f.clicks, f.link_clicks,
               f.landing_page_views, f.conversions
          from ads.fact_ad_day f
          -- Every join here is LEFT. The population is "spend in the window",
          -- not "spend we happen to have tagged", and an inner join would make
          -- the unknown bucket disappear by making its rows disappear.
          left join ads.facet_effective fe on fe.ad_key = f.ad_key
          left join ads.angle ang          on ang.id = fe.angle_id
          left join ads.ad d               on d.ad_key = f.ad_key
          left join ads.ad_group g         on g.ad_group_key = d.ad_group_key
         where f.brand_id = p_brand_id
           and f.day between p_since and p_until
    ),
    agg as (
        select s.dim_value,
               count(distinct s.ad_key)::integer as ads_run,
               sum(s.spend)                      as spend,
               sum(s.conversions)::bigint        as conversions,
               ads.rate(sum(s.impressions)::bigint,
                        sum(s.clicks)::bigint,
                        sum(s.link_clicks)::bigint,
                        sum(s.spend),
                        sum(s.conversions)::bigint,
                        sum(s.landing_page_views)::bigint) as rates
          from scoped s
         group by s.dim_value
    ),
    goals as (
        select s.dim_value, s.goal, sum(s.spend) as spend
          from scoped s
         group by s.dim_value, s.goal
    ),
    goal_roll as (
        select gg.dim_value,
               jsonb_agg(jsonb_build_object('goal', gg.goal, 'spend', gg.spend)
                         order by gg.spend desc nulls last) as optimization_goals,
               count(*)::integer                            as goal_count,
               -- The same construction and the same two NULL cases as
               -- ads.angle_performance. Written out again rather than factored
               -- into a helper, because the one rule that makes this function
               -- honest belongs where a reader of this function will see it.
               case when count(*) = 1 then min(gg.goal) end as only_goal
          from goals gg
         group by gg.dim_value
    )
    select p_dimension,
           a.dim_value,
           a.ads_run,
           a.spend,
           a.conversions,
           a.rates,
           coalesce(gr.optimization_goals, '[]'::jsonb),
           coalesce(gr.goal_count, 0),
           (coalesce(gr.goal_count, 0) = 1),
           (a.spend >= p_min_spend),
           -- Same refusal as ads.angle_performance, plus one more NULL case:
           -- the unknown bucket is never ranked. Sorting "no hook tag" into
           -- second place among hooks would report the tagging backlog as a
           -- creative finding.
           case when gr.only_goal is not null and a.dim_value is not null
                then (rank() over (partition by gr.only_goal
                                   order by (a.rates ->> 'cpa')::numeric nulls last))::integer
           end
      from agg a
      -- `is not distinct from`, not `=`: the unknown bucket's key is NULL on
      -- both sides and `=` would silently fail to join it to its own goals,
      -- leaving the largest row in the table with no evidence attached.
      left join goal_roll gr on gr.dim_value is not distinct from a.dim_value
     order by a.spend desc nulls last, a.dim_value;
end;
$fn$;

comment on function ads.facet_performance is
    'One facet dimension -- family, hook, offer, audience, format or angle -- '
    'with spend, conversions, rates and a rank that exists only within a '
    'single known optimization goal. This is the verb 004:29-30 describes and '
    'nothing answered: hook, offer and audience were written by every tag and '
    'read by nothing. p_dimension is a static CASE with all six branches '
    'named, never an interpolated column name. The untagged bucket is returned '
    'as value NULL and is never ranked.';


-- ---------------------------------------------------------------------------
-- 3. How much of the money the angle numbers do not describe.
--
-- intel/angles.py:122 computes this today as `sum((r["spend"] or 0) for r in
-- rows)` over the rows `queue` fetched, and `queue` takes `limit: int = 50`
-- (angles.py:86). So the number shrinks as the untagged tail grows, which is
-- the exact opposite of what it is for. It is also arithmetic in Python, which
-- CLAUDE.md forbids for the ordinary reason: two definitions of one number.
--
-- THE FOUR PAIRS ARE FOUR QUESTIONS ABOUT ONE POPULATION, NOT A PARTITION.
-- They overlap on purpose and adding them together means nothing:
--
--   tagged / untagged   DO partition the window's spend. "Tagged" means
--                       ads.facet_effective resolves an angle for the ad --
--                       which is exactly the spend that appears in
--                       ads.angle_performance -- so total minus tagged is
--                       precisely what those rows leave out.
--
--   inheritable         is a SUBSET OF TAGGED, not of untagged, and this is
--                       the line most likely to be misread. Before 010 these
--                       ads were untagged; since 010 facet_effective resolves
--                       them off the campaign_asset chain and they count. What
--                       the number says now is "this much of the tagged spend
--                       carries an angle nobody judged" -- it came off the
--                       stamped tracked_url with zero judgement, which is a
--                       different quality of evidence, not a gap.
--
--   stale               ads carrying only tags for wording they no longer run.
--                       Some are still tagged, via inheritance; the rest are
--                       not. 010's closing note applies and nothing can fix
--                       it: ads.ad_copy holds one wording per ad (002:249), so
--                       spend that ran under previous copy cannot be
--                       attributed to what that copy said. A reader is owed
--                       the size of the effect even so.
--
-- One row, always, with zeros rather than nothing when the brand had no spend
-- in the window. An empty result set reads as "the query failed"; a row of
-- zeros reads as "nothing ran", which is the fact.
-- ---------------------------------------------------------------------------
create function ads.untagged_spend(
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
begin
    if p_since > p_until then
        raise exception 'window is inverted: since % is after until %', p_since, p_until;
    end if;

    return query
    -- Per ad first, then classified, then summed. Doing it in one pass with
    -- the membership tests written as joins would multiply an ad's spend by
    -- the number of facet rows it matches, which is 010's double-count
    -- arriving through a side door.
    with per_ad as (
        select f.ad_key, sum(f.spend) as spend
          from ads.fact_ad_day f
         where f.brand_id = p_brand_id
           and f.day between p_since and p_until
         group by f.ad_key
    ),
    flagged as (
        select pa.ad_key,
               pa.spend,
               exists (select 1 from ads.facet_effective fe
                        where fe.ad_key = pa.ad_key
                          and fe.angle_id is not null)        as attributed,
               -- "Inheritable" as intel/angles.py's queue defines it: the
               -- campaign_asset chain resolves an angle and no filed tag
               -- describes the wording currently running. Door one, drainable
               -- with no judgement at all, and it should be drained before
               -- anybody reads copy and forms an opinion.
               (exists (select 1 from ads.inherited_facet i
                         where i.ad_key = pa.ad_key
                           and i.angle_id is not null)
                and not exists (select 1 from ads.ad_facet_current c
                                 where c.ad_key = pa.ad_key)) as inheritable,
               (exists (select 1 from ads.ad_facet_stale st
                         where st.ad_key = pa.ad_key)
                and not exists (select 1 from ads.ad_facet_current c
                                 where c.ad_key = pa.ad_key)) as stale
          from per_ad pa
    )
    select coalesce(sum(fl.spend), 0),
           coalesce(sum(fl.spend) filter (where fl.attributed), 0),
           coalesce(count(*) filter (where not fl.attributed), 0)::integer,
           coalesce(sum(fl.spend) filter (where not fl.attributed), 0),
           coalesce(count(*) filter (where fl.inheritable), 0)::integer,
           coalesce(sum(fl.spend) filter (where fl.inheritable), 0),
           coalesce(count(*) filter (where fl.stale), 0)::integer,
           coalesce(sum(fl.spend) filter (where fl.stale), 0)
      from flagged fl;
end;
$fn$;

comment on function ads.untagged_spend is
    'How much of a window''s spend the angle numbers do not describe, over ALL '
    'ads rather than the page of them a caller fetched -- intel/angles.py:122 '
    'sums this in Python across at most 50 rows, so it falls as tagging gets '
    'worse. tagged and untagged partition the spend; inheritable is a SUBSET '
    'OF TAGGED (an angle read off the campaign_asset chain that nobody judged) '
    'and stale overlaps both. Adding the four together means nothing.';


-- ---------------------------------------------------------------------------
-- 4. The bridge's own totals, including the share it does not explain.
--
-- ads.cpa_bridge (003:393) returns per-ad rows whose total_effect sums EXACTLY
-- to the brand's CPA change -- that identity is 003's stated reason the
-- function is worth printing at all. But rate_effect and mix_effect are NULL
-- for any ad that converted in only one of the two windows, deliberately,
-- because the split is undefined there and "a fabricated zero would break the
-- sum".
--
-- Which means the split explains SOME of the move, and how much is a fact the
-- reader needs before quoting it. intel/metrics.py:93-97 already promises that
-- fact by name and then does not return it, and the verb cannot compute it
-- either, because CLAUDE.md forbids the module to divide. So the honest
-- sentence -- "the split covers 80% of the move" -- has been unavailable since
-- the docstring that describes it was written.
--
-- HOW TO READ attributable_share, AND WHY IT IS NOT CLAMPED
--
-- It is signed, and it is against the NET change. When the attributable and
-- unattributable halves move in opposite directions -- one ad's CPA improved
-- while a brand-new ad spent with no conversions at all -- the share exceeds
-- 100% or goes negative. That is information, not an error, and clamping it to
-- 0..100 would hide the one case where a summary sentence is most wrong. NULL
-- when the net change is zero, per 003:48-51: an undefined share is NULL,
-- never 0. It is read beside attributable_effect and unattributable_effect,
-- never alone.
--
-- rate_effect + mix_effect equals attributable_effect to within rounding, and
-- only to within rounding: ads.cpa_bridge rounds each component at four
-- decimal places per ad, so sums of the rounded parts can differ from the
-- rounded whole in the last place. Said out loud rather than left for somebody
-- to find and file as a bug.
--
-- Calls ads.cpa_bridge ONCE. intel/metrics.py's `why` calls it twice today --
-- once for the rows, once for the totals -- and two calls are two chances for
-- a total and the rows printed beneath it to have been computed over different
-- data.
-- ---------------------------------------------------------------------------
create function ads.cpa_bridge_totals(
    p_brand_id uuid,
    p_since    date,
    p_until    date
) returns table (
    cpa_change            numeric,
    rate_effect           numeric,
    mix_effect            numeric,
    attributable_effect   numeric,
    unattributable_effect numeric,
    attributable_share    numeric,
    attributable_ads      integer,
    unattributable_ads    integer
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
begin
    -- No window validation here. ads.cpa_bridge derives its own prior window
    -- and reaches ads.window_metrics, which raises on an inverted one. A
    -- second copy of that check is a second place for the message to drift.
    return query
    with b as (
        select * from ads.cpa_bridge(p_brand_id, p_since, p_until)
    ),
    t as (
        select sum(b.total_effect)                                       as total,
               sum(b.rate_effect)                                        as rate,
               sum(b.mix_effect)                                         as mix,
               sum(b.total_effect) filter (where b.reason =  'attributable') as attr,
               sum(b.total_effect) filter (where b.reason <> 'attributable') as unattr,
               count(*) filter (where b.reason =  'attributable')::integer   as attr_ads,
               count(*) filter (where b.reason <> 'attributable')::integer   as unattr_ads
          from b
    )
    select round(t.total, 4),
           round(t.rate, 4),
           round(t.mix, 4),
           round(t.attr, 4),
           round(t.unattr, 4),
           round(100.0 * t.attr / nullif(t.total, 0), 2),
           coalesce(t.attr_ads, 0),
           coalesce(t.unattr_ads, 0)
      from t;
end;
$fn$;

comment on function ads.cpa_bridge_totals is
    'The one-row summary of ads.cpa_bridge: the brand CPA change, the rate and '
    'mix effects that sum to the attributable part of it, the part no split '
    'can explain, and attributable_share -- the number intel/metrics.py:93-97 '
    'names and `why` has never returned, so a caller can say "the split covers '
    '80% of the move" without dividing. The share is signed and against the '
    'net change: it can exceed 100% or go negative when the two halves move in '
    'opposite directions, and is NULL when the net change is zero.';


-- ---------------------------------------------------------------------------
-- 5. The alarm on the one manual step.
--
-- Everything else in this pipeline is machinery. This is not: a person opens
-- Ads Manager and pastes the tracked link onto the ad by hand. If that link is
-- mangled -- a truncated paste, an editor that ate the query string, a retype
-- with one character wrong -- meta_ads.parse.match_asset finds no candidate
-- and store.upsert_ad writes campaign_asset_id = NULL.
--
-- And a NULL there is INDISTINGUISHABLE from "this ad was written elsewhere",
-- which is the ordinary case and much the commoner one. parse.py:410-412 says
-- so outright: "None is the ordinary answer, not a failure. Most ads in an
-- account predate this system or were written elsewhere." So the failure hides
-- inside the normal case and nothing anywhere counts it. Every consequence
-- downstream is silent: ads.inherited_facet skips the ad, facet_effective has
-- nothing to fall back to, the angle goes untagged, and copy this engine wrote
-- is reported as somebody else's.
--
-- THE DENOMINATOR IS with_tracked_url, NOT approved_assets. An asset that was
-- never stamped could never have matched, and counting it as a miss blames a
-- person for a gap in the stamping path. Both counts are returned so that gap
-- stays visible as its own number rather than as a worse match rate.
--
-- THE EXPECTED VALUE IS PARSED OUT OF THE STAMPED URL, NEVER REBUILT from
-- asset_type, variant and version. parse.py:403-408 is emphatic about why:
-- "Recomputing the slug from the campaign's current name would be the obvious
-- implementation and it breaks silently the first time a campaign is renamed."
-- A diagnostic that reconstructed the expected value from today's data would
-- print a string that was never stamped anywhere and send somebody hunting for
-- a link that never existed. The stamped URL is the record; this reads it.
--
-- ON GRANTS, checked rather than assumed, because 012 exists entirely because
-- a grant was assumed: public.campaign_assets and public.campaigns are BOTH on
-- 006's named grant list (006:193-208 -- campaign_assets at 203, campaigns at
-- 206), alongside public.meta_ads. Both also carry an ads_owner select policy,
-- campaign_assets from 006's loop and campaigns from 009, which had to be
-- written because brands, campaigns and products had RLS enabled outside the
-- migration files and were quietly returning zero rows to ads_owner. So this
-- function needs no new grant and none is added here.
--
-- The window bounds are approval times, and a timestamptz compared against a
-- date resolves in the session's timezone. That is a smaller concern than
-- ads.settled_through's and deliberately not solved the same way: an approval
-- is an event on growth-engine's clock, not a day in a Meta ad account's.
-- ---------------------------------------------------------------------------
create function ads.match_health(
    p_brand_id uuid,
    p_since    date,
    p_until    date
) returns table (
    approved_assets  integer,
    with_tracked_url integer,
    matched          integer,
    match_rate       numeric,
    unmatched        jsonb
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
begin
    if p_since > p_until then
        raise exception 'window is inverted: since % is after until %', p_since, p_until;
    end if;

    return query
    with asset as (
        select ca.id                          as campaign_asset_id,
               c.name                         as campaign,
               ca.variant,
               ca.version_number,
               ca.approved_at,
               (ca.tracked_url is not null)   as has_url,
               -- No brand predicate on meta_ads. campaign_asset_id is a uuid
               -- that identifies one asset globally, so a match is a match; a
               -- brand filter here would turn a cross-brand misattribution --
               -- a real thing, and one worth seeing -- into an ordinary miss.
               exists (select 1
                         from public.meta_ads a
                        where a.campaign_asset_id = ca.id) as is_matched,
               -- The utm_content the importer would have had to read back off
               -- this ad's link. [?&] anchors the parameter name so it is not
               -- found inside some other key, and [^&#] stops at the next
               -- parameter or the fragment. No decoding: slot_content emits
               -- [a-z0-9-] only (tracking.py:94-113), so urlencode leaves it
               -- byte-identical and a decode step could only introduce a
               -- difference that is not in the data.
               substring(ca.tracked_url from '[?&]utm_content=([^&#]*)')
                                              as expected_utm_content
          from public.campaign_assets ca
          join public.campaigns c on c.id = ca.campaign_id
         where c.brand_id = p_brand_id
           and ca.status = 'approved'
           -- channel = 'meta_ads' exactly as store.candidates_for_match
           -- (meta_ads/store.py:206-219) scopes it. That is the candidate set
           -- the importer actually tries to match against; measuring a
           -- different set would report a health figure for an attempt nobody
           -- made.
           and ca.channel = 'meta_ads'
           and ca.approved_at >= p_since::timestamptz
           and ca.approved_at <  (p_until + 1)::timestamptz
    )
    select count(*)::integer,
           count(*) filter (where s.has_url)::integer,
           count(*) filter (where s.has_url and s.is_matched)::integer,
           round(100.0 * count(*) filter (where s.has_url and s.is_matched)
                 / nullif(count(*) filter (where s.has_url), 0), 2),
           -- '[]' and not NULL when nothing is missing: an empty array says
           -- "nothing is missing" and a NULL says nothing at all.
           --
           -- Not limited. A limit is how the size of a problem gets hidden,
           -- which is the whole complaint against intel/angles.py:122 above.
           -- Newest first, because the paste that just went wrong is the one
           -- somebody can still walk over and fix.
           coalesce(jsonb_agg(jsonb_build_object(
                        'campaign_asset_id',    s.campaign_asset_id,
                        'campaign',             s.campaign,
                        'variant',              s.variant,
                        'version_number',       s.version_number,
                        'expected_utm_content', s.expected_utm_content)
                    order by s.approved_at desc)
                    filter (where s.has_url and not s.is_matched),
                    '[]'::jsonb)
      from asset s;
end;
$fn$;

comment on function ads.match_health is
    'Whether the tracked links a person pasted into Ads Manager by hand came '
    'back. A mangled link leaves meta_ads.campaign_asset_id NULL, which is '
    'indistinguishable from an ad written elsewhere (parse.py:410-412), so this '
    'failure has had no alarm anywhere. match_rate is over assets that actually '
    'carry a stamped URL -- an unstamped asset could never have matched. '
    'unmatched carries the EXPECTED utm_content, PARSED out of the stamped URL '
    'and never rebuilt (parse.py:403-408), so a person can diff it against Ads '
    'Manager in one glance.';


-- ---------------------------------------------------------------------------
-- Ownership and grants. 012's block, in structure exactly, for 012's reason.
--
-- ads_migrate applies as postgres, so 006:279-281's `alter default privileges
-- for role ads_owner` never fires for anything created here: default
-- privileges apply only to objects created BY that role. Every function above
-- would otherwise come back owned by postgres.
--
-- That is not a permissions inconvenience, it is the hardest failure in this
-- repo to see. Every public.meta_* table has RLS on with no policy (042:557).
-- A SECURITY DEFINER function owned by postgres reads AROUND that RLS; one
-- owned by ads_owner (NOBYPASSRLS) reads THROUGH it, via the policies in 006
-- and 009. Two functions in one schema silently disagreeing about whether RLS
-- applies is 012's exact finding, and both of them return rows.
--
-- The role guard is 008's and 012's, for their reason: these migrations must
-- stay runnable against a database where the roles were never created -- a
-- fresh clone, a test harness -- rather than failing on what is a deployment
-- concern.
-- ---------------------------------------------------------------------------
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_owner') then
        raise notice 'ads_owner does not exist; skipping ownership reassignment';
        return;
    end if;

    execute 'alter function ads.angle_performance(uuid, date, date, text, numeric) '
            'owner to ads_owner';
    execute 'alter function ads.facet_performance(uuid, date, date, text, numeric) '
            'owner to ads_owner';
    execute 'alter function ads.untagged_spend(uuid, date, date) '
            'owner to ads_owner';
    execute 'alter function ads.cpa_bridge_totals(uuid, date, date) '
            'owner to ads_owner';
    execute 'alter function ads.match_health(uuid, date, date) '
            'owner to ads_owner';
end;
$$;

-- Explicit, and named one at a time rather than `all functions in schema ads`,
-- which is 006:189's rule: "a grant that is too wide produces no error, ever."
--
-- Functions are executable by PUBLIC in PostgreSQL unless revoked, so every
-- statement below is arguably redundant today -- and that is precisely 012's
-- argument for writing them anyway: relying on a PUBLIC default to carry the
-- read path is relying on something no line in this schema says out loud.
--
-- Only execute. Nothing here creates a table or a view, so there is nothing to
-- grant select on, and nothing anywhere in this file gives ads_reader a write.
grant execute on function ads.angle_performance(uuid, date, date, text, numeric)
  to ads_reader;
grant execute on function ads.facet_performance(uuid, date, date, text, numeric)
  to ads_reader;
grant execute on function ads.untagged_spend(uuid, date, date)
  to ads_reader;
grant execute on function ads.cpa_bridge_totals(uuid, date, date)
  to ads_reader;
grant execute on function ads.match_health(uuid, date, date)
  to ads_reader;

commit;
