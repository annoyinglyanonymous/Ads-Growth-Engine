-- ---------------------------------------------------------------------------
-- 016  How a campaign's spend is spread across its ads.
--
-- The campaign triage (scripts/suggest.py) hands a model each campaign's two
-- biggest-spending ads. What it did not hand over is how much of the
-- campaign those two carry -- and so the model worked it out. Twice on live
-- data it printed a figure no function returned:
--
--     "between them hold $1,627.74 of the campaign's $1,628.51"
--     "so $8,824.52 of this campaign's spend is tested on one headline"
--
-- Both were two ads' spend added together in the model's head. intel/
-- creative.check_figures now flags that on the card, but the flag is the
-- symptom. CLAUDE.md's first rule says the cure: "If a number you need is not
-- in a verb's output, that is a missing verb." This is the verb.
--
-- One row per campaign that spent in the window. Spend is summed per ad FIRST
-- and ranked, then aggregated, so an ad's share is never multiplied by the
-- number of days it ran.
--
-- NOT GOAL-SEGMENTED, and it does not need to be. Every figure here is spend
-- or a share of spend, and a dollar is a dollar under any optimization goal.
-- Nothing here is a cost per result.
-- ---------------------------------------------------------------------------

begin;

create function ads.campaign_concentration(
    p_brand_id uuid,
    p_since    date,
    p_until    date
) returns table (
    campaign_key                      uuid,
    campaign                          text,
    spend                             numeric,
    ads_with_spend                    integer,
    top_ad_spend                      numeric,
    top_two_ads_spend                 numeric,
    -- Percent, 0-100, to two places: the form every rate in 003 is printed in.
    top_ad_share_pct                  numeric,
    top_two_ads_share_pct             numeric,
    ads_with_no_conversions           integer,
    spend_on_ads_with_no_conversions  numeric,
    share_on_ads_with_no_conversions_pct numeric
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
    with per_ad as (
        select d.campaign_key,
               f.ad_key,
               sum(f.spend)                    as spend,
               coalesce(sum(f.conversions), 0) as conversions
          from ads.fact_ad_day f
          join ads.ad d on d.ad_key = f.ad_key
         where f.brand_id = p_brand_id
           and f.day between p_since and p_until
         group by d.campaign_key, f.ad_key
        having sum(f.spend) > 0
    ),
    ranked as (
        select pa.*,
               row_number() over (partition by pa.campaign_key
                                  order by pa.spend desc, pa.ad_key) as rn
          from per_ad pa
    ),
    agg as (
        select r.campaign_key,
               sum(r.spend)                                   as spend,
               count(*)::integer                              as ads_with_spend,
               sum(r.spend) filter (where r.rn = 1)           as top1,
               sum(r.spend) filter (where r.rn <= 2)          as top2,
               count(*) filter (where r.conversions = 0)::integer as nc_ads,
               coalesce(sum(r.spend) filter (where r.conversions = 0), 0) as nc_spend
          from ranked r
         group by r.campaign_key
    )
    select a.campaign_key,
           c.name,
           a.spend,
           a.ads_with_spend,
           a.top1,
           a.top2,
           round(100 * a.top1 / nullif(a.spend, 0), 2),
           round(100 * a.top2 / nullif(a.spend, 0), 2),
           a.nc_ads,
           a.nc_spend,
           round(100 * a.nc_spend / nullif(a.spend, 0), 2)
      from agg a
      left join ads.campaign c on c.campaign_key = a.campaign_key
     order by a.spend desc;
end;
$fn$;

comment on function ads.campaign_concentration is
    'How a campaign''s window spend is spread across its ads: the top one and '
    'top two ads'' spend and share, and how much went to ads that converted '
    'nothing. Spend only -- comparable across optimization goals because no '
    'figure here is a cost per result. Read by intel/creative.py so the '
    'campaign triage never has to add two ads together itself.';

-- Owner and grant, 013's pattern and for 013's reasons.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_owner') then
        raise notice 'ads_owner does not exist; skipping ownership reassignment';
        return;
    end if;
    execute 'alter function ads.campaign_concentration(uuid, date, date) '
            'owner to ads_owner';
end;
$$;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'ads_reader') then
        raise notice 'ads_reader does not exist; skipping grant';
        return;
    end if;
    execute 'grant execute on function ads.campaign_concentration(uuid, date, date) '
            'to ads_reader';
end;
$$;

commit;
