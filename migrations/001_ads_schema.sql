-- ---------------------------------------------------------------------------
-- 001  The ads schema.
--
-- WHAT THIS SCHEMA IS
--
-- The intelligence half of the ads system. growth-engine imports Meta's data
-- into public.meta_* and produces copy; this schema reads that data, derives
-- every rate and comparison from it, and holds the few facts that are ours
-- rather than Meta's: what an angle is, which ad carries which angle, what we
-- decided to test, and what counts as a conversion.
--
-- WHY A SCHEMA AND NOT A SECOND DATABASE
--
-- Almost nothing here copies Meta's data. The performance layer is entirely
-- views and functions over public.meta_*, so there is no ETL, no sync job, no
-- second source of truth and nothing to go stale. That is only possible in one
-- database: the whole value of this system is joining ad performance to
-- campaign_assets, campaign_angles and ad_reviews, and Postgres cannot join
-- across databases without a foreign data wrapper or a copy -- and a copy is
-- the staleness we just avoided.
--
-- THE DIRECTION IS ONE-WAY AND IT IS ENFORCED, NOT AGREED
--
--     public.*  ---- select ---->  ads.*
--     public.*  <--- nothing ----  ads.*
--
-- ads_owner holds SELECT on a named list of public tables and INSERT on none
-- of them (006). So "this repo does not write growth-engine's data" fails at
-- the database rather than at code review.
--
-- WHY THE LEDGER LIVES HERE
--
-- ads.schema_migrations, not public.schema_migrations. growth-engine's runner
-- keys that table on filename and numbers its own migrations from 001, so a
-- shared ledger would let this file collide with 001_kb_init.sql and leave one
-- of the two silently considered applied. Two globs, two ledgers, no overlap.
--
-- PLATFORM NEUTRALITY IS BUILT IN FROM THE FIRST FILE
--
-- Meta is the only platform today and Google is the one that follows. Every
-- table and view below carries a `platform` discriminator, keys on a surrogate
-- ad_key rather than on a Meta id, and uses the neutral level vocabulary
-- account / campaign / ad_group / ad -- Meta's "adset" is translated exactly
-- once, in the ads.ad view, and the word does not appear anywhere else. Adding
-- Google later is then a union branch and some rows, not a migration.
-- ---------------------------------------------------------------------------

begin;

create schema if not exists ads;

comment on schema ads is
    'The ads intelligence layer (Ads Growth Engine). Reads public.meta_* and '
    'derives every rate over a window; owns the angle taxonomy, the creative '
    'tags, the conversion definitions and the experiment record. Writes '
    'nothing in public -- the ads_owner role holds SELECT there and nothing '
    'else (006).';

-- The vocabulary, as a check helper rather than an enum type.
--
-- An enum would be the obvious choice and it is the wrong one: adding a value
-- to a Postgres enum cannot run inside a transaction with other DDL in some
-- versions, and every platform we add is otherwise a data change. A domain
-- over text keeps the constraint and keeps ALTER out of the picture.
create domain ads.platform_name as text
    check (value in ('meta', 'google'));

comment on domain ads.platform_name is
    'Which ad platform a row came from. Google is listed before it is built '
    'so that adding it is an insert and a union branch rather than a domain '
    'change that every dependent view has to be rebuilt for.';

commit;
