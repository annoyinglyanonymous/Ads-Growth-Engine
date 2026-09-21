-- ---------------------------------------------------------------------------
-- 012  Ownership and grants for everything 010 and 011 created.
--
-- A correction to 010, in a new file rather than an edit to it, because 010 is
-- applied and the ledger records its digest. 009 set the precedent and said
-- why: "THE CATALOGUE IS THE TRUTH; the migration history is only a record of
-- one way things get changed."
--
-- WHAT WENT WRONG
--
-- 010 closed with a comment asserting that 006's default privileges covered
-- its new objects. They did not, and the reason is one word in 006:279-281:
--
--     alter default privileges FOR ROLE ads_owner in schema ads
--         grant select on tables to ads_reader;
--
-- Default privileges apply only to objects created BY THAT ROLE. ads_migrate
-- applies as postgres, so every object 010 created was created by postgres and
-- ads_owner's defaults never fired. The failure was immediate and loud --
--
--     psycopg.errors.InsufficientPrivilege: permission denied for view
--     facet_effective
--
-- -- on the first `intel overview` after applying, which is the good case.
--
-- THE QUIETER HALF, WHICH IS THE REAL REASON THIS FILE EXISTS
--
-- ads.angle_coverage was DROPPED and recreated by 010, because its return
-- table gained four columns. A dropped function does not keep its owner, so it
-- came back owned by postgres -- and it worked, which is worse than failing.
--
-- 008:48-53 states the hazard exactly: "a view executes with its OWNER's
-- rights, and postgres has BYPASSRLS. A view left owned by whoever ran the
-- migration would read through RLS on some tables and around it on others,
-- depending on who that happened to be -- and the difference would never show
-- up as an error."
--
-- The same is true of a SECURITY DEFINER function, and more sharply: every
-- public.meta_* table has RLS on with no policy (042:557), so a definer
-- function owned by postgres reads AROUND it and one owned by ads_owner reads
-- THROUGH it, via the policies in 006. Two functions in one schema silently
-- disagreeing about whether RLS applies is the single hardest failure in this
-- repo to see, because both return rows.
--
-- 006:156-177 swept every existing view, function and domain to ads_owner in
-- one loop. That sweep ran once. 007:70 and 008:57 each reassign their own new
-- object by hand for this reason. 010 and 011 did not. This fixes it.
--
-- WHAT `create or replace` DID NOT BREAK
--
-- ads.experiment_result (010) and ads.window_metrics (011) were REPLACED, not
-- dropped, and `create or replace function` preserves ownership -- so both are
-- still owned by ads_owner from 006's sweep. They are left alone here on
-- purpose. Only the dropped-and-recreated function and the genuinely new
-- objects need reassigning, and listing the untouched ones would imply a
-- problem that does not exist.
-- ---------------------------------------------------------------------------

begin;

-- The guard is 008's, for 008's reason: these migrations must remain runnable
-- against a database where the roles were never created -- a fresh clone, a
-- test harness -- rather than failing on a role that is a deployment concern.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_owner') then
        raise notice 'ads_owner does not exist; skipping ownership reassignment';
        return;
    end if;

    -- The three views 010 created.
    execute 'alter view ads.ad_facet_current owner to ads_owner';
    execute 'alter view ads.ad_facet_stale   owner to ads_owner';
    execute 'alter view ads.facet_effective  owner to ads_owner';

    -- Dropped and recreated by 010, so its owner was reset to the applier.
    execute 'alter function ads.angle_coverage(uuid, date, date, text, numeric) '
            'owner to ads_owner';

    -- New in 011.
    execute 'alter function ads.ad_daily_rates(uuid[], date, date) '
            'owner to ads_owner';
end;
$$;

-- Explicit, because the default privileges cannot be relied on while the
-- applier is postgres rather than ads_owner. Named objects rather than
-- `all tables in schema ads`, which is 006:189's rule: "a grant that is too
-- wide produces no error, ever."
--
-- Only SELECT. Nothing here grants insert, update or delete to the reader, so
-- a write fails at the database rather than at review.
grant select on
    ads.ad_facet_current,
    ads.ad_facet_stale,
    ads.facet_effective
  to ads_reader;

-- Functions are executable by PUBLIC in PostgreSQL unless revoked, which is
-- why ads.angle_coverage kept working after 010 dropped and recreated it and
-- why the missing view grant was the only thing that surfaced. Granted
-- explicitly anyway: relying on a PUBLIC default to carry the read path is
-- relying on something no line in this schema says out loud.
grant execute on function ads.angle_coverage(uuid, date, date, text, numeric)
  to ads_reader;
grant execute on function ads.ad_daily_rates(uuid[], date, date)
  to ads_reader;

commit;
