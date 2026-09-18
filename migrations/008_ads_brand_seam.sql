-- ---------------------------------------------------------------------------
-- 008  ads.brand -- the seam view the read path needed and did not have.
--
-- THE BUG THIS FIXES. intel/context.py::brand() read public.brands directly,
-- through the ads_reader pool. ads_reader holds no grant in public -- by
-- design, stated in 006 and in CLAUDE.md -- so every read verb and every
-- dashboard page died on its FIRST query with:
--
--     permission denied for table brands
--
-- It was invisible until a real ads_reader connection existed. Applied as
-- postgres, or as ads_owner, the same code works: both hold select on
-- public.brands. That is the whole hazard of a second role -- the privileged
-- path is the one you develop against.
--
-- Note it failed LOUDLY, which is the good case. public.brands has no RLS
-- (growth-engine never enabled it), so the missing grant is a permission
-- error rather than a silent empty result. Had brands been RLS-enabled like
-- meta_ads or campaign_angles, this would have returned zero rows, `intel
-- status` would have reported "no brand renegade. Known brands: none", and
-- the obvious conclusion would have been a bad slug or an empty database.
--
-- WHY A VIEW AND NOT A GRANT. Granting select on public.brands to ads_reader
-- would be one line and would work. It would also make "ads_reader reads
-- nothing in public" false, and that sentence is load-bearing: it is how
-- anyone reviewing this repo knows the blast radius of the read credential
-- without auditing every query. Every other public table this schema reads
-- already arrives through a seam view -- ads.ad_account, ads.pull,
-- ads.campaign_angle_approved, ads.structure_change. brands was simply
-- missed, because context.py was written before 006 existed.
--
-- The seam also earns its keep the day a brand lives somewhere other than
-- public.brands: this view changes, and no verb does.
-- ---------------------------------------------------------------------------

begin;

create or replace view ads.brand as
select b.id,
       b.slug,
       b.name
  from public.brands b;

comment on view ads.brand is
    'Brand identity for the read path. ads_reader holds no grant in public; '
    'every public table reaches this schema through a view like this one.';

-- Owned by ads_owner, like every other object here. Not cosmetic: a view
-- executes with its OWNER''s rights, and postgres has BYPASSRLS. A view left
-- owned by whoever ran the migration would read through RLS on some tables and
-- around it on others, depending on who that happened to be -- and the
-- difference would never show up as an error. public.brands has no RLS today,
-- so this changes nothing today; it keeps the rule true if that ever changes.
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'ads_owner') then
        execute 'alter view ads.brand owner to ads_owner';
    end if;
end;
$$;

grant select on ads.brand to ads_reader;

commit;
