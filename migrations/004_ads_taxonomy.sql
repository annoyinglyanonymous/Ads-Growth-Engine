-- ---------------------------------------------------------------------------
-- 004  The angle bank, and how an ad gets tagged with one.
--
-- 044 called this "045's angle bank" three times. It lands here instead, and
-- growth-engine's 045 is the foreign key 044 pre-wrote as exact SQL -- that has
-- the stronger claim on the number, and this schema has its own sequence
-- anyway. Do not edit 044 to correct the comment if it has been applied:
-- migrate.py checksums applied files and reports an edit as drift.
--
-- WHY A CONTROLLED VOCABULARY AT ALL, when ad_reviews.angle_observed already
-- records what a reviewer saw.
--
-- Free text can tell you what you DID. Only a bank can tell you what you did
-- NOT do. "Which angles haven't we tested for Agency Height carrier access?" is
-- a set difference, and a set difference needs a set. That question -- not
-- reporting -- is the reason this file exists.
--
-- WHAT THIS DOES NOT DO: constrain the reviewer.
--
-- Nothing here migrates angle_observed, rejects a handle, or asks a reviewer to
-- pick from a list. Constraining the observation would bias it, and 044 is
-- right that an ad_reviews row is evidence of what somebody thought at a
-- moment. So: the free text is the SENSOR, this bank is the VOCABULARY, and
-- ads.angle_alias is the TRANSLATION between them.
--
-- FAMILIES ARE GLOBAL, ANGLES ARE PER BRAND.
--
-- "Financial" and "Emotional" mean the same thing for both brands, and "do
-- financial angles beat emotional ones for us" is a question worth being able
-- to ask across both. But "cash upfront" is a Renegade M&A angle and "carrier
-- access" is an Agency Height one, and a merged leaf vocabulary is a bank
-- nobody can read. brand_id is denormalised onto every row below -- 042's rule,
-- for 042's reason: two brands share this database and a misattribution puts
-- one brand's spend under the other's angle.
-- ---------------------------------------------------------------------------

begin;

create table ads.angle_family (
    slug       text primary key,
    name       text not null,
    -- One sentence: what puts an angle in this family rather than the next one.
    -- Written down because the boundary between "Exit" and "Emotional" is a
    -- judgement somebody will have to make again in six months.
    definition text not null,
    position   integer not null default 0
);

insert into ads.angle_family (slug, name, definition, position) values
    ('financial', 'Financial',
     'The promise is about money: what you get, how much, how fast, or what it '
     'does not cost you.', 1),
    ('exit', 'Exit',
     'The promise is about leaving: retiring, handing over, or being finished '
     'with the business.', 2),
    ('buyer', 'Buyer',
     'The promise is about who is on the other side of the table and how the '
     'transaction works.', 3),
    ('timing', 'Timing',
     'The promise is about when: now, before something changes, or with enough '
     'runway to plan.', 4),
    ('emotional', 'Emotional',
     'The promise is about how it feels or who it protects -- burnout, '
     'uncertainty, staff and clients.', 5),
    ('capability', 'Capability',
     'The promise is about what you can suddenly do: markets, carriers, tools, '
     'or reach you did not have.', 6);


create table ads.angle (
    id           uuid primary key default gen_random_uuid(),
    brand_id     uuid not null references public.brands (id) on delete cascade,
    family       text not null references ads.angle_family (slug),

    slug         text not null check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    -- 2-4 words, matching the handle ad_reviews already holds a reviewer to
    -- (review/record.py:265). Same shape on both sides is what makes an alias
    -- a plausible mapping rather than a guess.
    name         text not null check (length(btrim(name)) > 0),
    -- What makes an ad THIS angle and not the one next to it. The tagging pass
    -- reads this; without it, "cash upfront" and "valuation" collapse.
    definition   text not null check (length(btrim(definition)) > 0),

    product_slug text,
    audience     text,

    status       text not null default 'proposed'
                 check (status in ('proposed', 'active', 'retired', 'merged')),
    merged_into  uuid references ads.angle (id),

    -- An angle promoted from a campaign_angles row she already approved.
    -- Deliberately unenforced, exactly as 044 leaves ad_reviews.meta_ad_id
    -- unenforced and for the same reason: this schema must never become the
    -- thing that refuses a delete in public.
    from_campaign_angle_id uuid,

    added_by     text not null check (length(btrim(added_by)) > 0),
    approved_by  text,
    approved_at  timestamptz,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),

    unique (brand_id, slug),

    constraint angle_merge_coherent
        check ((status = 'merged') = (merged_into is not null)),

    -- THE GOVERNANCE HOOK.
    --
    -- An angle joins the working vocabulary only when a person signs it. The
    -- agent's write path hard-codes 'proposed' as a SQL literal -- it is not a
    -- value any caller can supply -- so this constraint is what makes that
    -- structural rather than conventional. Same asymmetry auth.assert_person
    -- enforces in growth-engine: an agent may propose and may not approve.
    constraint angle_active_is_signed
        check (status <> 'active' or approved_by is not null)
);

create index angle_brand_idx  on ads.angle (brand_id, status);
create index angle_family_idx on ads.angle (family);

comment on table ads.angle is
    'The controlled angle vocabulary, per brand. Exists so that "which angles '
    'have we never run" is a set difference rather than a guess. status=active '
    'requires approved_by: the agent proposes, a person signs.';


-- Small controlled tables rather than CHECK constraints, so adding a hook is an
-- insert and not a migration. 044 makes the same trade for its scores object.
create table ads.hook (
    slug text primary key, name text not null, definition text not null);
create table ads.offer (
    slug text primary key, name text not null, definition text not null);

insert into ads.hook (slug, name, definition) values
    ('question',          'Question',          'Opens by asking the reader something.'),
    ('stat',              'Statistic',         'Opens with a number or a claim of fact.'),
    ('story',             'Story',             'Opens inside a specific situation or person.'),
    ('callout',           'Callout',           'Names the audience directly in the first line.'),
    ('contrast',          'Contrast',          'Sets two options against each other.'),
    ('demo',              'Demonstration',     'Shows the thing working.'),
    ('testimonial',       'Testimonial',       'Opens in a customer''s voice.'),
    ('pattern_interrupt', 'Pattern interrupt', 'Opens with something deliberately unexpected.');

insert into ads.offer (slug, name, definition) values
    ('free_consult', 'Free consultation', 'A conversation at no cost.'),
    ('valuation',    'Valuation',         'A number for what the business is worth.'),
    ('quote',        'Quote',             'A price for a product.'),
    ('guide',        'Guide',             'A document in exchange for details.'),
    ('demo',         'Demo',              'A walkthrough of the product.'),
    ('call',         'Call',              'A scheduled conversation, usually confidential.'),
    ('none',         'No explicit offer',  'The copy makes no offer; the CTA carries it alone.');


-- ---------------------------------------------------------------------------
-- The translation layer between what a reviewer wrote and what the bank calls
-- it. 'no broker fee', 'zero broker fees' and 'broker fee free' all reach
-- no-broker-fees, and nobody is ever told they wrote the wrong words.
-- ---------------------------------------------------------------------------
create table ads.angle_alias (
    brand_id  uuid not null references public.brands (id) on delete cascade,
    alias     text not null,          -- lower(btrim(angle_observed))
    angle_id  uuid not null references ads.angle (id) on delete cascade,
    mapped_by text not null check (length(btrim(mapped_by)) > 0),
    mapped_at timestamptz not null default now(),
    primary key (brand_id, alias)
);

-- The queue a person drains. Every observed handle nobody has mapped yet,
-- biggest first -- 044 already built the index this needs
-- (ad_reviews_angle_idx on (brand_id, angle_observed)).
create view ads.angle_candidate as
select r.brand_id,
       lower(btrim(r.angle_observed)) as observed,
       count(*)                       as reviews,
       min(r.created_at)              as first_seen,
       max(r.created_at)              as last_seen,
       array_agg(distinct r.meta_ad_id)
           filter (where r.meta_ad_id is not null) as meta_ad_ids
  from public.ad_reviews r
 where r.angle_observed is not null
   and not exists (select 1
                     from ads.angle_alias a
                    where a.brand_id = r.brand_id
                      and a.alias = lower(btrim(r.angle_observed)))
 group by r.brand_id, lower(btrim(r.angle_observed));

comment on view ads.angle_candidate is
    'Angle handles a reviewer wrote that nothing in the bank maps yet, biggest '
    'first. This is how the vocabulary grows from what the ads actually said '
    'rather than from a whiteboard.';


-- ---------------------------------------------------------------------------
-- The vocabulary that already exists, as a view.
--
-- public.campaign_angles is ALREADY an approved, human-signed, 2-4 word angle
-- vocabulary: the `angles` skill writes them, she approves them, and they show
-- up in reviewer_verdicts. scripts/seed_angles.py reads this to propose a
-- starting bank rather than inventing a parallel set of names.
--
-- Inventing one would be worse than doing nothing: ads.inherited_facet joins on
-- lower(ads.angle.name) = lower(campaign_angles.name), so a second vocabulary
-- means that view matches nothing and every future campaign arrives untagged
-- despite having carried its angle all along.
--
-- distinct on (lower(name)): the same angle is routinely approved across
-- several campaigns, and a bank holding "Cash upfront" three times is not a
-- bank.
-- ---------------------------------------------------------------------------
create view ads.campaign_angle_approved as
select distinct on (c.brand_id, lower(ang.name))
       ang.id          as campaign_angle_id,
       c.brand_id,
       ang.name,
       ang.hypothesis,
       ang.rationale,
       p.slug          as product_slug,
       ang.campaign_id
  from public.campaign_angles ang
  join public.campaigns c on c.id = ang.campaign_id
  left join public.products p on p.id = c.product_id
 where ang.status = 'approved'
 order by c.brand_id, lower(ang.name), ang.id;


-- ---------------------------------------------------------------------------
-- The latest review per ad, as a view.
--
-- ads_reader has no grant on public.ad_reviews and should not have one: the
-- read surface of this repo is the ads schema, and a reader that can select
-- from a public table is a reader whose surface is whatever growth-engine
-- happens to put there. This view is the seam -- ads_owner reads the table
-- through its policy, ads_reader reads this.
--
-- 044 is emphatic that nothing may read a row in ad_reviews as permission, and
-- nothing here does: `overall` is surfaced as a number beside performance, and
-- the skills are told it is an opinion about copy, not a verdict about spend.
-- ---------------------------------------------------------------------------
create view ads.ad_review_latest as
select distinct on (r.meta_ad_id)
       md5('meta:ad:' || r.meta_ad_id)::uuid as ad_key,
       r.brand_id,
       r.overall,
       r.angle_observed,
       r.created_at as reviewed_at,
       r.reviewed_by
  from public.ad_reviews r
 where r.meta_ad_id is not null
 order by r.meta_ad_id, r.created_at desc;


-- ---------------------------------------------------------------------------
-- The tag itself.
--
-- KEYED ON (ad_key, copy_hash), which is 044's rule applied to tagging: rewrite
-- the copy and the tag must be made again. A tag that silently survives a
-- rewrite describes an ad that no longer exists, and that is precisely what
-- poisons "which angle wins".
--
-- No foreign key to ads.ad, because ads.ad is a view -- and 042's chain-has-no-
-- foreign-keys reasoning applies anyway: a watermarked structure pull routinely
-- leaves an ad out, and a constraint would make the tag the casualty.
-- ---------------------------------------------------------------------------
create table ads.ad_facet (
    ad_key     uuid not null,
    copy_hash  text not null,
    platform   ads.platform_name not null default 'meta',
    brand_id   uuid not null references public.brands (id) on delete cascade,

    angle_id   uuid references ads.angle (id) on delete restrict,
    hook       text references ads.hook (slug),
    -- The promise in the copy. meta_ads.call_to_action_type is the BUTTON, and
    -- the two can legitimately disagree -- "book a confidential call" under a
    -- LEARN_MORE button. The disagreement is a finding, so both are kept and
    -- neither is derived from the other.
    offer      text references ads.offer (slug),
    audience   text,

    -- inherited : read from the campaign_assets chain. No judgement involved.
    -- tagged    : derived from a reviewer's handle through angle_alias.
    -- operator  : a person typed it. Never overwritten by a tagging pass.
    source     text not null check (source in ('inherited', 'tagged', 'operator')),

    -- stated  : the copy says it outright.
    -- inferred: somebody read it into the copy.
    -- The agent writes 'inferred' for anything it decides; 'stated' is for a
    -- tag that came off the campaign_assets chain or a literal phrase.
    confidence text not null check (confidence in ('stated', 'inferred')),
    rationale  text,

    tagged_by  text not null check (length(btrim(tagged_by)) > 0),
    context_id uuid,
    created_at timestamptz not null default now(),

    primary key (ad_key, copy_hash)
);

create index ad_facet_brand_idx on ads.ad_facet (brand_id, angle_id);
create index ad_facet_angle_idx on ads.ad_facet (angle_id) where angle_id is not null;

comment on table ads.ad_facet is
    'What an ad''s copy is doing: angle, hook, offer, audience. Keyed on '
    '(ad_key, copy_hash) so a rewrite retires the tag rather than leaving one '
    'that describes wording which no longer runs (044''s rule for reviews). '
    'source=operator rows are never overwritten by a tagging pass.';


-- ---------------------------------------------------------------------------
-- DOOR ONE: ads that arrive already tagged.
--
-- An ad this engine drafted already carries an angle -- campaign_assets ->
-- creative_concepts.angle_id -> campaign_angles.name -- and store.upsert_ad
-- already resolves campaign_asset_id by matching the stamped tracked_url. So
-- for anything shipped through the engine, the angle is knowable with ZERO
-- judgement, and this view is the whole mechanism.
--
-- This is what tracking.py has been paying into since 025. Every utm_content of
-- the form 'ad-a-v5' stamped at approval was buying this join, and until now
-- nothing collected.
--
-- Make this the default path. The tagging pass is for the legacy backlog.
--
-- The angle match is on lower(name) against ads.angle.name, which is why
-- scripts/seed_angles.py promotes the existing campaign_angles vocabulary
-- rather than inventing a parallel one -- invent a second set of names and this
-- view matches nothing.
-- ---------------------------------------------------------------------------
create view ads.inherited_facet as
select md5('meta:ad:' || a.id)::uuid as ad_key,
       c.copy_hash,
       'meta'::ads.platform_name     as platform,
       a.brand_id,
       ang.id                        as angle_id,
       cc.hook                       as concept_hook,
       ca_ang.name                   as campaign_angle_name,
       ca.id                         as campaign_asset_id,
       ca.variant,
       ca.version_number
  from public.meta_ads a
  join ads.ad_copy c              on c.ad_key = md5('meta:ad:' || a.id)::uuid
  join public.campaign_assets ca  on ca.id = a.campaign_asset_id
  join public.creative_concepts cc on cc.id = ca.concept_id
  join public.campaign_angles ca_ang on ca_ang.id = cc.angle_id
  left join ads.angle ang on ang.brand_id = a.brand_id
                         and lower(ang.name) = lower(ca_ang.name)
                         and ang.status = 'active'
 where a.campaign_asset_id is not null;

comment on view ads.inherited_facet is
    'Ads whose angle is knowable without judgement, by following the '
    'campaign_asset_id that store.upsert_ad already resolved from the stamped '
    'tracked_url. angle_id is NULL when the campaign angle has no counterpart '
    'in the bank yet -- that is the signal to run scripts/seed_angles.py.';


-- ---------------------------------------------------------------------------
-- Coverage: what we have run, and what we have not.
--
-- The set difference this whole file exists for. never_run is the answer to
-- "which angles haven't we tested", and under_spent is the answer to the more
-- honest version of it -- an angle that ran on $40 has not been tested, it has
-- been glanced at.
-- ---------------------------------------------------------------------------
create function ads.angle_coverage(
    p_brand_id     uuid,
    p_since        date,
    p_until        date,
    p_product_slug text default null,
    p_min_spend    numeric default 250
) returns table (
    angle_id    uuid,
    family      text,
    angle_slug  text,
    angle_name  text,
    ads_run     integer,
    spend       numeric,
    conversions bigint,
    cpa         numeric,
    first_run   date,
    last_run    date,
    state       text
)
language plpgsql
stable
security definer
set search_path = ads, public, pg_temp
as $fn$
begin
    return query
    with perf as (
        select fa.angle_id,
               count(distinct f.ad_key)::integer as ads_run,
               sum(f.spend)                      as spend,
               sum(f.conversions)::bigint        as conversions,
               min(f.day)                        as first_run,
               max(f.day)                        as last_run
          from ads.fact_ad_day f
          join ads.ad_facet fa on fa.ad_key = f.ad_key
         where f.brand_id = p_brand_id
           and f.day between p_since and p_until
           and fa.angle_id is not null
         group by fa.angle_id
    )
    select a.id, a.family, a.slug, a.name,
           coalesce(p.ads_run, 0),
           coalesce(p.spend, 0),
           coalesce(p.conversions, 0),
           round(p.spend / nullif(p.conversions, 0), 4),
           p.first_run, p.last_run,
           case
               when p.angle_id is null              then 'never_run'
               when coalesce(p.spend, 0) < p_min_spend then 'under_spent'
               else                                     'tested'
           end
      from ads.angle a
      left join perf p on p.angle_id = a.id
     where a.brand_id = p_brand_id
       and a.status = 'active'
       and (p_product_slug is null or a.product_slug = p_product_slug)
     order by coalesce(p.spend, 0) desc, a.name;
end;
$fn$;

comment on function ads.angle_coverage is
    'Every active angle for a brand with what it actually spent and returned in '
    'the window, and whether it has been tested at all. never_run is the set '
    'difference that free-text angle handles cannot produce.';

commit;
