-- Close the five experiment proposals filed on 2026-09-28. Prepared, NOT run.
--
-- WHO RUNS THIS: a person, against the database, under their own name. It
-- writes ads.experiment.conclusion, which is the one write no agent and no
-- verb may make (CLAUDE.md, "The rules"; experiment_conclusion_attributed in
-- migrations/005_ads_experiments.sql). Replace <your name> before running;
-- the column is called concluded_by and it has to be true.
--
-- WHY: these five were filed by hand (proposed_by cli:Renegade) from the
-- 2026-09-28 campaign triage. That analysis was replaced on 2026-10-01, none
-- of them was ever launched (started_on is null), and since 2026-10-02 a
-- recommended test is filed automatically when the outcomes ledger sees its
-- tagged ad launch. Left open they read as five tests waiting on a launch
-- that will never happen. Their 20% / $500 thresholds were an agent's choice
-- to satisfy the shape; nothing edits a filed proposal, so they are closed,
-- not changed.
--
-- WHAT IT TOUCHES: exactly the five rows named below, and only while each is
-- still unlaunched and unconcluded. The names are listed rather than matched
-- on 'p0923-%': an automatically filed experiment is named "<issue id> | ..."
-- too, and several of those issue ids (p0923-5-0, p0923-7-0, p0923-1-1)
-- are still open in the ledger, so a prefix would close the new ones.
-- created_at before 2026-09-29 and proposed_by are a second fence for the
-- same reason.
--
-- VALIDITY against 005:
--   experiment_conclusion_attributed  conclusion, concluded_by and
--       concluded_at are all set together, so all three are non-null.
--   experiment_window_ordered  untouched: started_on and ended_on are not
--       written, and `started_on is null` holds for every row it touches.
--
-- Expect "UPDATE 5". Fewer means one was launched or concluded since; that
-- row is then not this statement's business. Read what it did with
--   python -m intel experiments --brand renegade

update ads.experiment
   set conclusion   = 'superseded: filed from the 2026-09-28 triage, whose analysis was replaced on 2026-10-01; never launched',
       concluded_by = '<your name>',
       concluded_at = now()
 where brand_id = (select id from ads.brand where slug = 'renegade')
   and name in (
       'p0923-0-0 | 07082025 Franchise IAL | Start Franchise: captive-ceiling opener vs rocket line',
       'p0923-1-1 | 12012026 M&A AVC Retargeting | Retirement Decade video: direct calculator opener',
       'p0923-5-0 | 03082026 B-Roll IAL | Sky Blue static: reader''s-day opener',
       'p0923-7-0 | 22052026 Franchise Instant Form Retarget | Multi Color video: someone-else''s-book opener',
       'p0923-13-1 | 12052026 Franchise Employment TXFL | Start Franchise: stated offer vs rocket line')
   and (name like 'p0923-%' or name like 'p0924-%')
   and proposed_by = 'cli:Renegade'
   and created_at < timestamptz '2026-09-29 00:00:00+00'
   and started_on is null
   and conclusion is null;
