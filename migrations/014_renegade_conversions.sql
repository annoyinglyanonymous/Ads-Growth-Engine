-- ---------------------------------------------------------------------------
-- 014  What counts as a lead for renegade.
--
-- Until this file, ads.conversion_definition was empty, which meant every
-- conversion count and every CPA in the schema was zero or null -- correctly,
-- and 002 argues for that default: "Empty means zero conversions, which is
-- honest." Meta returns a bag of forty action types per ad and has no opinion
-- about which of them is a lead for an agency-acquisition business. This file
-- is that opinion, and it is hers.
--
-- A MIGRATION RATHER THAN A ONE-OFF INSERT, because it is a decision somebody
-- should be able to find and argue with later. The rows themselves are meant
-- to be edited -- 002's header is explicit that changing one "applies to
-- history instead of starting from the day it was made", since 042 retains the
-- whole actions jsonb and ads.fact_ad_day re-derives conversions on every
-- read. Correcting a mistake here costs a statement, not a re-pull.
--
-- THE TWO PATHS, AND WHY BOTH ARE LEADS
--
-- Confirmed with her, 2026-09-21: these are separate journeys, not one person
-- counted twice.
--
--   lead                                          908   a Facebook or Instagram
--                                                       Instant Form, filled
--                                                       without leaving the app
--   offsite_complete_registration_add_meta_leads   863   clicked through to
--                                                       renegadeinsurance.com
--                                                       and completed a form
--
-- `lead` does not already contain the second one, and the reason is a pixel
-- setup choice: the website form fires CompleteRegistration, not Lead, so Meta
-- never rolls them together. 75 ads produced both, 3 produced only the Instant
-- Form and 4 only the website registration.
--
-- WHAT IS DELIBERATELY EXCLUDED, AND WHY EACH ONE WOULD HAVE BEEN WRONG
--
-- Every one of these was in the account and each would have inflated the count
-- rather than raised an error, which is the whole hazard: ads.fact_ad_day SUMS
-- the matching rows, so a wrong entry here divides CPA rather than failing.
--
--   onsite_conversion.lead_grouped        739  A SUBSET of `lead`, not a peer.
--                                              They co-occur on 448 ad-days,
--                                              equal on 387 and `lead` larger
--                                              on 61, never smaller. Adding it
--                                              would count 739 leads twice.
--
--   offsite_search_add_meta_leads         740  The SAME custom conversion as
--                                              the one above, reported under a
--                                              different standard event.
--                                              Identical value to
--                                              complete_registration on 427 of
--                                              their 448 shared ad-days: both
--                                              fire on the same page load.
--
--   offsite_content_view_add_meta_leads 2,124  Same custom conversion again,
--                                              on ViewContent. That is a page
--                                              view. A visitor is not a lead.
--
--   offsite_conversion.fb_pixel_custom  1,227  The bucket of ALL custom
--                                              conversions on the pixel,
--                                              including add_20_s_calls and
--                                              five numbered ones. Too broad to
--                                              mean anything.
--
--   the *_add_20_s_calls family                Left out pending her decision.
--                                              The name suggests booked calls,
--                                              which would belong on the
--                                              `booked` rung rather than here
--                                              -- see the note at the bottom.
--
-- Meta reports one conversion under several aliases as a matter of course
-- (view_content appears under six names, all reading exactly 1,385), so the
-- rule when adding to this table is: find the alias that names what the person
-- DID, and take exactly one per action.
-- ---------------------------------------------------------------------------

begin;

insert into ads.conversion_definition
    (brand_id, platform, action_type, counts_as, note, added_by)
select b.id, 'meta', v.action_type, v.counts_as, v.note, 'operator'
  from public.brands b
  cross join (values
    ('lead', 'lead',
     'Facebook/Instagram Instant Form. Contains lead_grouped (739 of 908); '
     'do not also define lead_grouped or those 739 count twice.'),
    ('offsite_complete_registration_add_meta_leads', 'lead',
     'Form completed on renegadeinsurance.com. The add_meta_leads custom '
     'conversion, taken on CompleteRegistration only -- its Search and '
     'ViewContent rows are the same event on the same page load.')
  ) as v(action_type, counts_as, note)
 where b.slug = 'renegade'
    on conflict (brand_id, platform, action_type, counts_as) do nothing;

commit;

-- ---------------------------------------------------------------------------
-- STILL OPEN, and deliberately not decided here.
--
-- `qualified` and `booked` have no rows. ads.fact_ad_day computes both on
-- every read and every window returns zero for them, which is honest and also
-- means two thirds of the funnel this schema was built around is unused.
--
-- The candidate for `booked` is the *_add_20_s_calls custom conversion --
-- offsite_complete_registration_add_20_s_calls is 124 events over 31 ads, and
-- the name reads like a call of at least twenty seconds. If that is what it
-- is, it is a booked call rather than a lead and belongs on its own rung,
-- where cost-per-booked becomes answerable.
--
-- Not added on a guess. 002:62-65 gives the reason the rungs exist at all:
-- "optimising on the first of those is how you buy a lot of the wrong leads
-- cheaply", and a rung filled in wrongly is worse than one left empty,
-- because the empty one is visibly empty.
-- ---------------------------------------------------------------------------
