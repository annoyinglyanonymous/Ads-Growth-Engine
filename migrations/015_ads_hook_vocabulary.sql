-- ---------------------------------------------------------------------------
-- 015  Three hooks for the way this account actually opens, and a correction
--      to the one whose definition swallowed them.
--
-- 004 says a new hook should be "an insert and not a migration", and for a
-- typo it should be. This is not a typo. It is a change to the vocabulary
-- every creative number on the site is grouped by, so it follows 014 instead:
-- "A MIGRATION RATHER THAN A ONE-OFF INSERT, because it is a decision somebody
-- should be able to find and argue with later."
--
-- WHAT WENT WRONG
--
-- 004 defined `stat` as "Opens with a number OR A CLAIM OF FACT". The second
-- half is not a statistic hook, it is every declarative sentence in the
-- language, and the first tagging pass read it that way: 91 of 243 ads came
-- back `stat`, of which four opened with a figure. `stat` was not a hook, it
-- was the default.
--
-- scripts/tag.py's prompt now requires a figure, which fixed the false
-- positives and left the ads homeless. As of 2026-09-23, over the 28 days to
-- 2026-09-22:
--
--     ads that spent            239
--     carrying a hook            60
--     carrying none             179     $43,287.59 of $58,987.18
--
-- So 73% of the account's spend sits in a bucket the vocabulary cannot name,
-- and `dimensions` -- the block the whole creative analysis is built on -- is
-- reporting on the quarter of the money that happens to fit.
--
-- WHAT THE COPY ACTUALLY DOES
--
-- 125 distinct unlabelled wordings, read by hand. Three shapes, each with
-- enough behind it to be worth a row:
--
--   problem       "Sales stall when you're buried in servicing."
--                 "Retention drops when your team is too busy servicing."
--                 "The stress owners fear most comes from not knowing what
--                  happens next."
--                 "The captive ceiling is real."
--
--   affirmation   "You already know how to sell."
--                 "You built something real."
--                 "You have spent years building someone else's book."
--                 "You built your agency to grow - not get buried in admin."
--
--   imperative    "Stop letting operations run your day."
--                 "Forget the sluggish rise - blast off with Renegade!"
--                 "Lead your own business and create the future you deserve."
--                 "Take servicing out of the equation."
--
-- `problem` and `affirmation` are both assertions about the reader and could
-- have been one row. They are two because the difference is the interesting
-- one: whether opening on what is wrong with the reader's business costs more
-- or less per lead than opening on what they have already done well. That
-- question is unanswerable today and becomes answerable the moment these
-- exist. One `assertion` bucket holding 73% of spend would answer nothing.
--
-- THE DEFINITIONS CARRY THEIR OWN EDGES
--
-- scripts/tag.py builds its prompt from ads.hook.definition, so these
-- sentences ARE the instruction -- there is no second place to explain them.
-- 004's rows say what each hook is; after a pass that mislabelled 87 ads on an
-- ambiguity, these also say what it is not.
-- ---------------------------------------------------------------------------

begin;

insert into ads.hook (slug, name, definition) values
    ('problem',
     'Problem',
     'Opens by naming something that is going wrong for the reader or their '
     'business. Not stat, which needs a figure; not contrast, which needs two '
     'options set against each other.'),

    ('affirmation',
     'Affirmation',
     'Opens by crediting the reader with something they have already done or '
     'already know. Not callout, which names the audience ("P&C agency '
     'owners"); this addresses them as "you" and grants them something.'),

    ('imperative',
     'Imperative',
     'Opens by telling the reader to do something. The command must be the '
     'first sentence of the copy, not the closing ask -- every ad ends with '
     'one of those and it is the offer, not the hook.');

-- `stat` stops being the default.
--
-- Editing a seeded definition rather than adding a row beside it: the old
-- sentence is not a hook anybody meant to have, and leaving it in place would
-- leave the 91-row failure one re-run away from happening again. ads.ad_facet
-- holds slugs, so no existing row moves -- the four ads that genuinely open on
-- a figure stay `stat`, and this only changes what the next pass is told.
update ads.hook
   set definition = 'Opens with a figure -- a number, a percentage, an amount, '
                    'a year, a count. A general claim of fact is not a stat; '
                    'see problem, affirmation and imperative.'
 where slug = 'stat'
   and definition = 'Opens with a number or a claim of fact.';

comment on table ads.hook is
    'The controlled hook vocabulary. A hook describes how the copy OPENS, '
    'judged on the first sentence of the body or the headline where there is '
    'no body. Each definition says what the hook is not, because the pass that '
    'reads them is a model and the first ambiguity cost 87 mislabelled ads.';

commit;
