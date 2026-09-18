-- ---------------------------------------------------------------------------
-- 006  Roles, grants, and the RLS policies without which none of this returns
--      a single row.
--
-- APPLY AS postgres. This file creates roles and grants privileges on another
-- schema's tables; it is the one migration here that is not "my schema, my
-- business". Either set ADS_MIGRATE_URL to the postgres url for the run, or
-- paste it into the Supabase SQL editor -- each file carries its own
-- BEGIN/COMMIT so neither path is privileged over the other.
--
-- NO PASSWORD APPEARS IN THIS FILE.
--
-- The roles are created NOLOGIN. The two
--     alter role ... with login password '...'
-- statements are run once, by hand, and the values go straight into .env, which
-- is gitignored. growth-engine ships scripts/scan_secrets.py for a reason and
-- this repo copies it.
--
--
-- ===========================================================================
-- THE RLS TRAP. Read this before changing anything below.
-- ===========================================================================
--
-- Every public.meta_* table has RLS enabled with ZERO policies (042:557), as do
-- ad_reviews (044), campaign_angles, campaign_assets and creative_concepts
-- (013). There is not one `create policy` in any of growth-engine's 44
-- migrations. 042 explains why that has been harmless:
--
--     "The app connects as postgres and bypasses RLS, so this costs nothing
--      today."
--
-- It stops being harmless the moment a role that is NOT postgres connects.
-- In PostgreSQL, RLS enabled with no policy returns ZERO ROWS -- not an error,
-- not a permission denial. The queries succeed. Every metric reads 0.00, every
-- chart is flat, and the obvious conclusion is that the importer is broken.
--
-- Two consequences, and both are handled below:
--
--  1. ads_owner is NOBYPASSRLS, so it needs an explicit permissive policy on
--     every public table this schema reads. That is the `create policy` block.
--
--  2. A VIEW is not security_invoker by default -- it executes with the VIEW
--     OWNER's rights. So ads.fact_ad_day owned by ads_owner still returns
--     nothing without (1). Owning the view does not buy a bypass; only the
--     policy does.
--
-- The alternative -- granting BYPASSRLS to ads_owner -- would work and is
-- rejected. A bypass is invisible at the table it bypasses; a policy is listed
-- next to the table it applies to, and `\d public.meta_ad_insights` shows it.
--
-- VERIFY, DO NOT ASSUME. After applying, connect as ads_reader and run:
--
--     select count(*) from ads.fact_ad_day;   -- must be a NUMBER, not 0
--     insert into ads.angle (...) values (...);  -- must fail: permission denied
--
-- If the count is 0 while postgres sees rows, it is this file, not the import.
-- ===========================================================================
-- ---------------------------------------------------------------------------

begin;

-- ---------------------------------------------------------------------------
-- The two roles.
--
--   ads_owner   owns schema ads; reads a named list of public tables; writes
--               nothing in public. Used by ads_migrate.py and intel/record.py.
--   ads_reader  reads the ads views and executes the ads functions. Nothing in
--               public, and no base table in ads. Every read verb and the whole
--               dashboard use this one.
-- ---------------------------------------------------------------------------
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_owner') then
        create role ads_owner nologin noinherit nocreatedb nocreaterole nobypassrls;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'ads_reader') then
        create role ads_reader nologin noinherit nocreatedb nocreaterole nobypassrls;
    end if;
end;
$$;

-- ---------------------------------------------------------------------------
-- Membership options for whoever is applying this file.
--
-- Supabase's `postgres` is NOT a superuser -- it is a CREATEROLE role, and that
-- changes what the two `alter ... owner to ads_owner` blocks below are allowed
-- to do.
--
-- PostgreSQL 16 split role membership into three independent options: ADMIN
-- (may grant the role onward), INHERIT (holds its privileges automatically) and
-- SET (may `set role` to it). When a CREATEROLE role creates a role, PG16 grants
-- it membership implicitly -- but only with ADMIN. INHERIT and SET are FALSE.
--
-- Both of those matter here, and they fail differently:
--
--   SET FALSE     -> `alter schema ads owner to ads_owner` fails with
--                    "must be able to SET ROLE \"ads_owner\"". Changing an
--                    object's owner requires being able to become that owner.
--   INHERIT FALSE -> the `grant select on ads.* to ads_reader` statements near
--                    the end fail, because granting on an object requires
--                    holding the owner's rights, not merely being a member.
--
-- ADMIN is deliberately NOT requested. It is already held, and asking for it
-- is an error rather than a no-op -- PostgreSQL refuses to let a grantor give
-- ADMIN to itself:
--
--     ADMIN option cannot be granted back to your own grantor
--
-- which is a confusing thing to hit while trying to fix a SET ROLE failure.
--
-- None of this grants reach the applier lacked. postgres has BYPASSRLS and
-- CREATEROLE; these options concern a NOLOGIN role it just created. What they
-- buy is the ability to hand ownership AWAY, which is the point of the next
-- block: the SECURITY DEFINER functions must run as ads_owner (NOBYPASSRLS) so
-- that the policies further down are load-bearing rather than decorative.
--
-- current_user rather than a literal 'postgres', so a self-hosted database
-- where the applier is somebody else does not fail for a reason nobody would
-- guess from the message.
-- ---------------------------------------------------------------------------
do $$
declare
    r text;
    granular boolean := current_setting('server_version_num')::int >= 160000;
begin
    foreach r in array array['ads_owner', 'ads_reader'] loop
        if granular then
            execute format('grant %I to %I with inherit true', r, current_user);
            execute format('grant %I to %I with set true', r, current_user);
        else
            -- PostgreSQL 15 and earlier have no granular options: membership
            -- carries both, and the creator is not a member until told.
            execute format('grant %I to %I', r, current_user);
        end if;
    end loop;
end;
$$;

alter schema ads owner to ads_owner;

-- Take ownership of everything 001-005 created.
--
-- Whoever applied those files owns their objects, and on a first run that is
-- postgres -- which has BYPASSRLS, so the SECURITY DEFINER functions would work
-- without any policy and the security posture would be an accident of who ran
-- the migration. Reassigning makes it deterministic: the functions run as
-- ads_owner, ads_owner is NOBYPASSRLS, and the policies below are load-bearing
-- rather than decorative.
do $$
declare r record;
begin
    for r in select c.relname, c.relkind
               from pg_class c
               join pg_namespace n on n.oid = c.relnamespace
              where n.nspname = 'ads' and c.relkind in ('r', 'v', 'm')
    loop
        execute format('alter %s ads.%I owner to ads_owner',
                       case r.relkind when 'v' then 'view'
                                      when 'm' then 'materialized view'
                                      else 'table' end,
                       r.relname);
    end loop;

    for r in select p.oid::regprocedure as sig
               from pg_proc p
               join pg_namespace n on n.oid = p.pronamespace
              where n.nspname = 'ads'
    loop
        execute format('alter function %s owner to ads_owner', r.sig);
    end loop;

    for r in select t.typname
               from pg_type t
               join pg_namespace n on n.oid = t.typnamespace
              where n.nspname = 'ads' and t.typtype = 'd'
    loop
        execute format('alter domain ads.%I owner to ads_owner', r.typname);
    end loop;
end;
$$;


-- ---------------------------------------------------------------------------
-- What ads_owner may read in public.
--
-- A NAMED LIST, never `all tables in schema public`. A wildcard here would
-- silently pick up every table growth-engine adds in future -- including ones
-- holding things this schema has no business reading -- and nobody would notice
-- because a grant that is too wide produces no error, ever.
-- ---------------------------------------------------------------------------
grant usage on schema public to ads_owner, ads_reader;

grant select on
    public.brands,
    public.meta_ad_accounts,
    public.meta_campaigns,
    public.meta_adsets,
    public.meta_ads,
    public.meta_ad_texts,
    public.meta_ad_insights,
    public.meta_pulls,
    public.ad_reviews,
    public.campaign_assets,
    public.creative_concepts,
    public.campaign_angles,
    public.campaigns,
    public.products
  to ads_owner;

-- So ads.* may declare a foreign key to public.brands -- and to nothing else.
-- 042's own rule: the parent that is stable and hers gets a foreign key; the
-- watermarked Meta id chain does not.
grant references on public.brands to ads_owner;


-- ---------------------------------------------------------------------------
-- The policies. See the header -- without these, everything above returns zero
-- rows and looks like an empty database.
--
-- `using (true)` and not a brand predicate: ads_owner is a service role reached
-- only through this repo's code, and the brand filter is a parameter on every
-- function in 003. Putting brand scoping here as well would be a second place
-- to get it wrong, and the one that fails silently.
-- ---------------------------------------------------------------------------
do $$
declare t text;
begin
    foreach t in array array[
        'meta_ad_accounts', 'meta_campaigns', 'meta_adsets', 'meta_ads',
        'meta_ad_texts', 'meta_ad_insights', 'meta_pulls',
        'ad_reviews', 'campaign_assets', 'creative_concepts', 'campaign_angles'
    ] loop
        execute format(
            'drop policy if exists ads_owner_select on public.%I', t);
        execute format(
            'create policy ads_owner_select on public.%I '
            'for select to ads_owner using (true)', t);
    end loop;
end;
$$;


-- ---------------------------------------------------------------------------
-- The reader's surface: views and functions. Not tables.
--
-- ads_reader cannot select from ads.angle, ads.ad_facet, ads.experiment or
-- ads.conversion_definition directly. It reads them through the views and
-- functions, which is what lets the shape of an answer change without every
-- caller having to know the shape of a table. It is also one more thing between
-- a read verb and an accidental mutation.
-- ---------------------------------------------------------------------------
grant select on
    ads.ad,
    ads.ad_group,
    ads.campaign,
    ads.ad_copy,
    ads.fact_ad_day,
    ads.angle_candidate,
    ads.inherited_facet,
    ads.ad_review_latest,
    ads.ad_account,
    ads.pull,
    ads.campaign_angle_approved
  to ads_reader;

-- The taxonomy and experiment tables are small, slow-moving and read constantly
-- by the dashboard. Select is enough; nothing here grants insert, update or
-- delete to the reader, so a write fails at the database rather than at review.
grant select on
    ads.angle_family, ads.angle, ads.hook, ads.offer, ads.angle_alias,
    ads.ad_facet, ads.experiment, ads.experiment_arm,
    ads.conversion_definition
  to ads_reader;

grant execute on all functions in schema ads to ads_reader;

grant usage on schema ads to ads_reader;

-- Anything ads_owner creates in future is readable by ads_reader without a
-- follow-up grant -- and is still not writable by it.
alter default privileges for role ads_owner in schema ads
    grant select on tables to ads_reader;
alter default privileges for role ads_owner in schema ads
    grant execute on functions to ads_reader;

commit;


-- ---------------------------------------------------------------------------
-- RUN THESE TWO BY HAND, ONCE, AND PUT THE VALUES IN .env. NOT IN THIS FILE.
--
--   alter role ads_owner  with login password '...';   -- ADS_DATABASE_URL
--   alter role ads_reader with login password '...';   -- ADS_DATABASE_URL_RO
--
-- Then verify, as ads_reader:
--
--   select count(*) from ads.fact_ad_day;        -- a number, not zero
--   select * from ads.window_metrics(
--       (select id from public.brands where slug = 'renegade'),
--       current_date - 30, current_date, 'ad') limit 5;
--   insert into ads.angle (brand_id, family, slug, name, definition, added_by)
--       values (...);                            -- must fail
-- ---------------------------------------------------------------------------
