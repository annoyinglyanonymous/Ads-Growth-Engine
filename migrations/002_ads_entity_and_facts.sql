-- ---------------------------------------------------------------------------
-- 002  Entities and the daily fact.
--
-- Everything in this file except conversion_definition is a VIEW. That is the
-- point: there is no import step, no rollup to refresh and no second copy of
-- Meta's numbers to drift from Meta's numbers. A window is a sum of days, and
-- the days already exist in public.meta_ad_insights at the right grain.
--
-- WHY SURROGATE KEYS
--
-- ad_key = md5('meta:ad:' || id)::uuid. Deterministic, so this stays a view --
-- a lookup table mapping platform ids to keys would need an insert path, and
-- an insert path would need something to run it. Deterministic also means the
-- key for a Meta ad is the same key tomorrow, in a test fixture, and in
-- another process, without coordination.
--
-- The prefix is what makes Google free: md5('google:ad:' || id) cannot collide
-- with a Meta key, so ads.ad_facet and ads.experiment_arm key on ad_key and
-- carry no platform-specific column at all.
--
-- WHY THE RATE COLUMNS ARE NOT HERE
--
-- public.meta_ad_insights stores Meta's own per-day ctr, cpc, cpm and
-- cost_per_lead. They stay there, for provenance, and are deliberately NOT
-- carried into any view in this schema. A ratio column that exists is a ratio
-- column somebody eventually averages, and an average of daily ratios weights
-- a day with one lead as heavily as a day with fifty -- growth-engine's
-- store.overview_rows documents the same trap in the one place it aggregates.
-- Every windowed rate in this schema comes from ads.rate() in 003, and nowhere
-- else.
-- ---------------------------------------------------------------------------

begin;

-- ---------------------------------------------------------------------------
-- What counts as a conversion. A ROW, not a Python constant.
--
-- meta_ads/parse.py holds LEAD_ACTION_TYPES as a tuple and derives leads at
-- import time. 042 is right that which pixel events count is a judgement that
-- will change -- but as a constant it is a judgement only a RE-PULL can
-- revise, and Meta stops serving the older days eventually.
--
-- 042 also kept the whole actions array on every row, explicitly so the
-- decision could be "applied to history rather than starting from the day it
-- was made". This table is the other half of that sentence: change a row here
-- and every window over all history answers differently on the next read, with
-- no replay and no rate limit.
--
-- It is also where 042's deliberately-empty BOOKED_ACTION_TYPES lands, and
-- where Google's entirely different conversion vocabulary will land, with no
-- schema change -- just rows.
-- ---------------------------------------------------------------------------
create table ads.conversion_definition (
    brand_id    uuid not null references public.brands (id) on delete cascade,
    platform    ads.platform_name not null default 'meta',

    -- The platform's own action_type string, e.g.
    -- offsite_conversion.fb_pixel_lead or onsite_conversion.lead_grouped.
    action_type text not null check (length(btrim(action_type)) > 0),

    -- Three rungs, not a boolean. A lead is not a qualified lead and a
    -- qualified lead is not a booked call -- and the whole reason to connect a
    -- CRM later is that optimising on the first of those is how you buy a lot
    -- of the wrong leads cheaply.
    counts_as   text not null check (counts_as in ('lead', 'qualified', 'booked')),

    note        text,
    added_by    text not null check (length(btrim(added_by)) > 0),
    created_at  timestamptz not null default now(),

    primary key (brand_id, platform, action_type, counts_as)
);

comment on table ads.conversion_definition is
    'Which platform action types count as a lead, a qualified lead or a booked '
    'call, per brand. A table rather than a constant so the decision applies to '
    'history instead of starting from the day it was made (042 kept the actions '
    'jsonb for exactly this). Empty means zero conversions, which is honest: a '
    'brand nobody has defined conversions for has not had them counted.';


-- ---------------------------------------------------------------------------
-- Entities. Meta's adset becomes the neutral ad_group here, and the word
-- "adset" appears nowhere else in this schema.
-- ---------------------------------------------------------------------------
create view ads.campaign as
select md5('meta:campaign:' || c.id)::uuid as campaign_key,
       'meta'::ads.platform_name           as platform,
       c.id                                as platform_campaign_id,
       c.brand_id,
       c.account_id                        as platform_account_id,
       c.name,
       c.status,
       c.effective_status,
       c.objective,
       c.buying_type,
       c.daily_budget,
       c.lifetime_budget,
       c.spend_cap,
       c.start_time,
       c.stop_time
  from public.meta_campaigns c;

create view ads.ad_group as
select md5('meta:ad_group:' || s.id)::uuid           as ad_group_key,
       'meta'::ads.platform_name                     as platform,
       s.id                                          as platform_ad_group_id,
       md5('meta:campaign:' || s.campaign_id)::uuid  as campaign_key,
       s.brand_id,
       s.account_id                                  as platform_account_id,
       s.name,
       s.status,
       s.effective_status,
       -- THE SEGMENTING COLUMN. 042: "an adset optimising for LINK_CLICKS and
       -- one optimising for OFFSITE_CONVERSIONS are not comparable on CPL
       -- however similar their copy is." Every angle comparison and every
       -- fatigue verdict has to be able to hold this constant, so it is carried
       -- all the way down to the daily fact.
       s.optimization_goal,
       s.billing_event,
       s.bid_strategy,
       s.daily_budget,
       s.lifetime_budget,
       s.start_time,
       s.end_time
  from public.meta_adsets s;

create view ads.ad as
select md5('meta:ad:' || a.id)::uuid                 as ad_key,
       'meta'::ads.platform_name                     as platform,
       a.id                                          as platform_ad_id,
       a.brand_id,
       md5('meta:ad_group:' || a.adset_id)::uuid     as ad_group_key,
       md5('meta:campaign:' || a.campaign_id)::uuid  as campaign_key,
       a.account_id                                  as platform_account_id,
       a.name,
       a.status,
       a.effective_status,
       a.call_to_action_type                         as cta,
       a.link_url,
       a.utm_campaign,
       a.utm_content,
       a.image_url,
       a.thumbnail_url,
       a.campaign_asset_id,
       a.needs_page_scope,
       -- Together these answer "when did this ad stop running", which
       -- effective_status cannot: an ad deleted in Ads Manager simply stops
       -- appearing in the response (042).
       a.first_seen_at,
       a.last_seen_at,

       -- FORMAT IS DERIVED, NEVER JUDGED.
       --
       -- CLAUDE.md: code holds what must be true, skills hold what must be
       -- judged. A model asked to name the format from the copy would be right
       -- most of the time; this is right always, and it costs one jsonb probe.
       -- It is also the one place 042's retained raw earns its keep at read
       -- time rather than at replay time.
       --
       -- Order matters: a dynamic creative can also carry a video, and dynamic
       -- is the more useful answer because it says the media varies per
       -- impression and no single asset is "the" creative.
       case
           when a.is_dynamic then 'dynamic'
           when a.video_id is not null then 'video'
           when jsonb_array_length(coalesce(
                    a.raw #> '{creative,object_story_spec,link_data,child_attachments}',
                    '[]'::jsonb)) > 1 then 'carousel'
           else 'static'
       end                                           as format
  from public.meta_ads a;


-- ---------------------------------------------------------------------------
-- Operational seams. ads_reader holds no grant on public.* and should not:
-- the read surface of this repo is the ads schema, and a reader that can
-- select from a public table has a surface defined by whatever growth-engine
-- puts there next. These two views are how `intel status` sees the import
-- without being handed the tables.
-- ---------------------------------------------------------------------------
create view ads.ad_account as
select acc.id            as account_key,
       'meta'::ads.platform_name as platform,
       acc.act_id        as platform_account_id,
       acc.brand_id,
       acc.label,
       acc.currency,
       -- Load-bearing, not decoration: an insights date is a day in THIS zone,
       -- and ads.settled_through reads it rather than trusting the importer's
       -- UTC window (042).
       acc.timezone_name,
       acc.active
  from public.meta_ad_accounts acc;

create view ads.pull as
select p.run_id,
       p.brand_id,
       p.account_id as platform_account_id,
       p.kind,
       p.since,
       p.until,
       p.status,
       p.counts,
       p.error,
       p.api_version,
       p.started_at,
       p.finished_at
  from public.meta_pulls p;

comment on view ads.pull is
    'Import runs, so `intel status` can say when the numbers last moved and '
    'whether the last attempt failed. A token that expired presents as a run '
    'with status=failed and nothing else changing -- 042 notes the symptom of '
    'a sunset or a revoked token is silence, not an error on screen.';


-- ---------------------------------------------------------------------------
-- The copy, and its fingerprint.
--
-- copy_hash IS NOT ad_reviews.text_hash AND MUST NOT BE JOINED TO IT.
--
-- text_hash is a sha256 computed in growth-engine's validation/review.py over
-- the assembled variants of a draft. Reproducing that construction in SQL would
-- couple two repos through a hash function, and the failure mode of that
-- coupling is silent: the two stop agreeing and every join quietly returns
-- nothing. This is a different fingerprint of the same idea -- stable, and
-- different whenever a word changes. Both answer "this ad, this wording".
-- Neither is the other's key.
--
-- Its job is in ads.ad_facet (004): a tag is keyed on (ad_key, copy_hash), so
-- rewriting an ad's copy retires the tag rather than silently leaving one that
-- describes an ad which no longer exists.
-- ---------------------------------------------------------------------------
create view ads.ad_copy as
select md5('meta:ad:' || t.ad_id)::uuid as ad_key,
       'meta'::ads.platform_name        as platform,
       md5(string_agg(t.field || ':' || t.ordinal || ':' || t.text,
                      chr(10) order by t.field, t.ordinal)) as copy_hash,
       jsonb_agg(jsonb_build_object('field', t.field,
                                    'ordinal', t.ordinal,
                                    'text', t.text)
                 order by t.field, t.ordinal) as texts,
       count(*) filter (where t.field = 'headline') as headlines,
       count(*) filter (where t.field = 'body')     as bodies,
       min(t.text) filter (where t.field = 'headline' and t.ordinal = 0) as first_headline,
       min(t.text) filter (where t.field = 'body'     and t.ordinal = 0) as first_body
  from public.meta_ad_texts t
 group by t.ad_id;


-- ---------------------------------------------------------------------------
-- The daily fact. One ad, one day -- the grain 042 chose, unchanged.
--
-- REACH AND FREQUENCY ARE NAMED SO THAT NOBODY SUMS THEM.
--
-- Daily reach is deduplicated WITHIN the day. A 14-day sum counts the same
-- person up to fourteen times, so reach over a window is not derivable from
-- these rows at all -- Meta has to be asked for it at the window grain, which
-- this importer does not do.
--
-- Daily frequency is impressions / that day's reach, so it sits near 1.0-1.3
-- for almost everything. It is NOT cumulative frequency, which is the number
-- people mean when they say "frequency is up to 4", and which is not in this
-- database. ads.fatigue (003) therefore tests for ACCELERATION in daily
-- frequency and says so, rather than testing a threshold that would never fire.
--
-- The _this_day suffix is the whole defence. A column called reach gets summed
-- by the third person who reads this schema.
-- ---------------------------------------------------------------------------
create view ads.fact_ad_day as
select md5('meta:ad:' || i.ad_id)::uuid as ad_key,
       'meta'::ads.platform_name        as platform,
       i.date                           as day,
       i.brand_id,
       i.account_id                     as platform_account_id,

       i.impressions,
       i.clicks,
       -- inline_link_clicks, under a neutral name. clicks counts likes,
       -- comments and post expansions; the link click is the one that means
       -- intent, and using the wrong one is how a CTR trend becomes noise.
       -- Both are kept so the difference stays visible.
       i.inline_link_clicks             as link_clicks,
       i.landing_page_views,
       i.spend,
       i.currency,

       i.reach     as reach_this_day,
       i.frequency as frequency_this_day,

       conv.leads      as conversions,
       conv.qualified  as qualified_conversions,
       conv.booked     as booked_conversions,

       -- Meta's own relative grading. NULL means "not enough impressions to
       -- grade", which is not the same as a grade below the lowest -- 003 never
       -- reads a NULL here as a drop.
       i.quality_ranking,
       i.engagement_rate_ranking,
       i.conversion_rate_ranking,

       i.actions
  from public.meta_ad_insights i
  cross join lateral (
      select coalesce(sum(e.value) filter (where cd.counts_as = 'lead'),      0)::bigint as leads,
             coalesce(sum(e.value) filter (where cd.counts_as = 'qualified'), 0)::bigint as qualified,
             coalesce(sum(e.value) filter (where cd.counts_as = 'booked'),    0)::bigint as booked
        from jsonb_array_elements(coalesce(i.actions, '[]'::jsonb)) as node
        cross join lateral (
            select (node ->> 'action_type') as action_type,
                   coalesce((node ->> 'value')::numeric, 0) as value
        ) e
        join ads.conversion_definition cd
          on cd.brand_id    = i.brand_id
         and cd.platform    = 'meta'
         and cd.action_type = e.action_type
  ) conv;

comment on view ads.fact_ad_day is
    'One ad, one day. Conversions are recomputed from the retained actions '
    'jsonb against ads.conversion_definition on every read, so changing what '
    'counts as a lead re-answers all of history without a re-pull. Carries no '
    'rate columns on purpose: every rate comes from ads.rate() in 003.';

commit;
