-- ---------------------------------------------------------------------------
-- 011  A brand level for window_metrics, so the front page stops doing maths.
--
-- 003's first sentence is "NOBODY COMPUTES A RATE TWICE". ui.py breaks it, in
-- the most visible place available:
--
--     ui.py:115-127  _roll(), whose own docstring says "Sums only -- no rate
--                    is recomputed here", and which then computes
--
--                        out["cpa"]      = spend / conversions
--                        out["cpm"]      = spend * 1000 / impressions
--                        out["link_ctr"] = 100 * link_clicks / impressions
--
--                    Those are the four headline numbers on the front page.
--                    The `rates` column it already selects is never read, and
--                    the hand-rolled versions skip ads.rate's round(..., 4).
--
--     ui.py:130-142  _brand_delta(), which correctly REFUSES to compute a
--                    brand-level delta -- because there was no function that
--                    returned one. That refusal is honest and it is also why
--                    the front page has a CPA tile with no movement on it.
--
-- The cause is the same in both: ads.window_metrics stops at campaign level,
-- so there has never been a brand-level row to read. This adds one.
--
-- WHY THIS IS A FOURTH BRANCH AND NOT A NEW FUNCTION
--
-- ads.compare (003:300-301) passes p_level straight through to window_metrics
-- and validates nothing itself. So a brand branch here gives compare a
-- brand-level delta for free -- the exact number _brand_delta could not
-- produce -- without a second function that would then need its own prior-
-- window derivation and could drift from compare's.
--
-- p_level remains a static CASE over the grouping key, never an interpolated
-- column name. 003's header rule about no dynamic SQL in a SECURITY DEFINER
-- function is untouched.
--
-- WHAT A BRAND ROW DOES NOT HAVE
--
-- optimization_goal is NULL at brand level, for the reason 003:203-206 already
-- gives for campaign level: a brand spans ad groups optimising for different
-- things, and min() would silently pick one. A NULL is the honest answer and
-- the one that stops a caller comparing CPAs that are not comparable.
--
-- reach_best_day stays max() and is still not a window reach. currencies stays
-- a count, and at brand level it is the one that matters most: two accounts in
-- different currencies produce a brand total that is dollars plus pounds, and
-- the caller is expected to refuse to print it.
-- ---------------------------------------------------------------------------

begin;

create or replace function ads.window_metrics(
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
    if p_level not in ('ad', 'ad_group', 'campaign', 'brand') then
        raise exception 'unknown level %, expected ad, ad_group, campaign or brand', p_level
            using hint = 'Meta''s "adset" is called ad_group here; the word adset '
                         'appears nowhere in the ads schema.';
    end if;

    if p_since > p_until then
        raise exception 'window is inverted: since % is after until %', p_since, p_until;
    end if;

    return query
    with scoped as (
        select case p_level
                   when 'brand'    then f.brand_id
                   when 'campaign' then d.campaign_key
                   when 'ad_group' then d.ad_group_key
                   else d.ad_key
               end as entity_key,
               case p_level
                   when 'brand'    then null
                   when 'campaign' then c.platform_campaign_id
                   when 'ad_group' then g.platform_ad_group_id
                   else d.platform_ad_id
               end as platform_id,
               case p_level
                   when 'brand'    then b.name
                   when 'campaign' then c.name
                   when 'ad_group' then g.name
                   else d.name
               end as entity_name,
               -- Only meaningful at ad and ad_group level. At campaign level a
               -- single campaign can hold ad groups with different goals, and
               -- min() would silently pick one -- so it is NULL there, which is
               -- the honest answer and the one that stops a caller comparing
               -- CPAs that are not comparable. A brand spans more of them, so
               -- the same applies with more force.
               case when p_level in ('campaign', 'brand') then null
                    else g.optimization_goal end as optimization_goal,
               f.*
          from ads.fact_ad_day f
          join ads.ad d        on d.ad_key       = f.ad_key
          left join ads.ad_group g on g.ad_group_key = d.ad_group_key
          left join ads.campaign c on c.campaign_key = d.campaign_key
          left join ads.brand b    on b.id           = f.brand_id
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
    'Totals and rates for one window at ad, ad_group, campaign or brand level. '
    'At brand level it returns exactly one row and optimization_goal is NULL, '
    'because a brand spans goals that are not comparable on cost. '
    'reach_best_day is max() and not sum() -- daily reach cannot be summed. '
    'currencies is a count so a caller can refuse to print a mixed-currency '
    'total rather than adding dollars to pounds.';


-- ---------------------------------------------------------------------------
-- Daily rates for a set of ads, for the sparklines on /creative.
--
-- The second rate computation outside ads.rate, and the more surprising one
-- because it is not in Python -- it is raw SQL inside the page:
--
--     ui.py:206-213   case when impressions > 0
--                          then 100.0 * link_clicks / impressions end as v
--
-- In the same file whose docstring opens "EVERY PAGE CALLS THE SAME FUNCTION
-- THE AGENT CALLS". A sparkline drawn from that expression and a link_ctr
-- printed from ads.rate() are two definitions of one number, and the fatigue
-- page shows them side by side.
--
-- Takes an array rather than one ad because the caller needs forty of them for
-- one page, and forty round trips through a pooler is a four-second page for
-- no reason. That was already the shape of the query being replaced.
--
-- Returns the whole rates jsonb, not just link_ctr. A caller wanting a CPM
-- shape next month should not need a migration to get one, and ads.rate
-- computes all eight from the same row anyway.
-- ---------------------------------------------------------------------------
create function ads.ad_daily_rates(
    p_ad_keys uuid[],
    p_since   date,
    p_until   date
) returns table (
    ad_key uuid,
    day    date,
    rates  jsonb
)
language sql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
    select f.ad_key,
           f.day,
           ads.rate(f.impressions, f.clicks, f.link_clicks,
                    f.spend, f.conversions, f.landing_page_views)
      from ads.fact_ad_day f
     where f.ad_key = any (p_ad_keys)
       and f.day between p_since and p_until
     order by f.ad_key, f.day;
$fn$;

comment on function ads.ad_daily_rates is
    'Per-ad, per-day rates from ads.rate, for sparklines and shapes. Exists so '
    'no page has to divide link_clicks by impressions itself -- a second '
    'definition of a rate is how a chart and a number beside it come to '
    'disagree. A rate whose denominator is zero is NULL, never 0.';

commit;
