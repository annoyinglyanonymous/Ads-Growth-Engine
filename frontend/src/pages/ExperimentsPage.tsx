/* EXPERIMENTS. What we decided to test, and what would have counted as an
 * answer -- both registered before any number existed. There is no winner
 * column: the result reports whether the registered effect was cleared, and
 * naming a winner is a decision a person makes. Ported from
 * templates/experiments.html.
 *
 * WHAT CHANGED, AND WHY
 *
 * Five tiles say the state of the whole pipeline at once: filed, running,
 * waiting on a conclusion, ideas, and findings that are not a test at all.
 *
 * The ideas were seven tall cards, each with its own before/after table,
 * copy and brief. They are now one row each -- the idea, where it came from,
 * what moved as small chips, and whether it can be filed -- and every row
 * opens to the full card, so nothing a writer needed is gone.
 *
 * The ordering rule is kept: what has been decided outranks what has been
 * suggested. When the log has experiments it comes first; when it is empty,
 * as it is today, it folds into one line at the bottom rather than opening
 * the page with an empty table.
 *
 * ONE FLOW WITH /suggestions (2026-10-02). The report recommends a test and
 * names its tag; the person launches the new ad with utm_content=<tag>; the
 * outcomes ledger sees the launch and scripts/suggest.py files the
 * experiment as a proposal, pre-registered from the plan published before
 * the launch. A proposal whose launch was seen shows the date and the exact
 * statement that records started_on. That statement is here, in the page,
 * and nowhere in intel/: it is run by the person who launched the ad,
 * against the database, and nothing on this site runs it. "Tests the report
 * recommends" replaces `intel propose`'s ideas, which render only when the
 * ledger is empty or unreadable (`ideas` is null otherwise).
 *
 * READ-ONLY, BY CONSTRUCTION. No button and no script: the page prints a
 * statement and a person runs it under their own name.
 */
import { Fragment } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../components/Layout";
import { Dash, Empty, StageHead, usePage } from "../components/ui";
import type { PageBase } from "../lib/api";
import { money, num, truncate } from "../lib/format";

interface Arm {
  label: string;
  angle_slug?: string | null;
  utm_content?: string | null;
  ad_keys?: string[] | null;
}

/** with_launches: the ledger entry the proposal was filed from, once seen launched. */
interface Launch {
  issue_id: string | null;
  implemented_on: string;
  match: string | null;
  matched_by: string | null;
  tag: string | null;
}

interface LogRow {
  id?: string;
  name: string;
  question: string | null;
  hypothesis: string | null;
  primary_metric: string;
  minimum_effect_pct: number | null;
  minimum_spend_per_arm: number | null;
  started_on?: string | null;
  ended_on?: string | null;
  proposed_by?: string | null;
  conclusion: string | null;
  concluded_by: string | null;
  concluded_at?: string | null;
  created_at?: string | null;
  state: "concluded" | "proposed" | "running" | "awaiting_conclusion" | string;
  arms: Arm[] | null;
  launch: Launch | null;
}

/** recommended_tests: a ledger test entry nobody has launched. */
interface Recommended {
  id: string;
  campaign: string | null;
  ad_group: string | null;
  kind: string | null;
  variable: string | null;
  control: string | null;
  challenger: string | null;
  tag: string | null;
  setup: string | null;
  recommended_on: string | null;
  last_mentioned_on?: string | null;
  status?: string | null;
  reason?: string | null;
}

/** launched_unfiled: seen launched, experiment not on file yet. */
interface Unfiled {
  id: string;
  campaign: string | null;
  variable: string | null;
  implemented_on: string | null;
  match: string | null;
  matched_by: string | null;
  why: string;
}

/** propose's `moved`: the verb's own percentage, never recomputed here. */
interface Moved {
  metric: string;
  prior: number | null;
  recent: number | null;
  pct: number;
}

interface Diagnosis {
  stage: string;
  says: string;
  change: string;
  also_broke?: string[] | null;
}

/** ads.ad_copy, read; nothing here writes ad text. */
interface AdCopy {
  first_headline?: string | null;
  first_body?: string | null;
}

interface Brief {
  angle: string;
  broke: string;
  change: string;
  beat: { metric: string | null; current: number | null };
  never_run_as: string[];
  written_where: string;
}

interface Proposal {
  source: string | null;
  title: string;
  question: string;
  why: string;
  arms: { label: string; blocked_on?: string | null }[];
  would_register: { primary_metric: string };
  fileable: boolean;
  blocked_on: string | null;
  already_tested: boolean;
  how_to_file: string;
  diagnosis?: Diagnosis | null;
  moved?: Moved[] | null;
  ad_copy?: AdCopy | null;
  styles_tried?: string[] | null;
  styles_untried?: string[] | null;
  brief?: Brief | null;
}

interface Finding {
  title: string;
  diagnosis: Diagnosis;
  moved: Moved[] | null;
  ad_copy: AdCopy | null;
  spend_recent: number | null;
  window: string;
  not_an_experiment: string;
}

interface Ideas {
  proposal_count: number;
  proposals: Proposal[] | null;
  diagnoses: Finding[] | null;
  observations: { says: string }[] | null;
  settled_through?: string | null;
}

export interface ExperimentsData extends PageBase {
  d: { brand: string; rows: LogRow[] | null; awaiting_launch: number; row_count?: number };
  ideas: Ideas | null;
  days: number;
  has_ledger: boolean;
  recommended: Recommended[] | null;
  unfiled: Unfiled[] | null;
  launches: LogRow[] | null;
}


/** Python's str.replace: every occurrence. */
const swap = (s: string, from: string, to: string) => s.split(from).join(to);

/** The statement a person runs to record a launch. Quotes in the name and
 *  the brand are doubled, as the template did; `and started_on is null`
 *  keeps it from moving a window already being read. */
export function confirmSql(brand: string, name: string, implementedOn: string): string {
  return "update ads.experiment\n" +
    `   set started_on = date '${implementedOn}'\n` +
    ` where brand_id = (select id from ads.brand where slug = '${swap(brand, "'", "''")}')\n` +
    `   and name     = '${swap(name, "'", "''")}'\n` +
    "   and started_on is null;";
}

/* A metric that moved, as a chip: the metric's name and the verb's own
   percentage. The arrow is the sign; nothing here is coloured by merit. */
function Chips({ moved }: { moved: Moved[] | null | undefined }) {
  return (
    <div className="chips">
      {(moved ?? []).slice(0, 4).map((m, i) => (
        <span key={i} className="chip-m">{m.metric} {m.pct > 0 ? "↑" : "↓"}{num(Math.abs(m.pct), 0)}%</span>
      ))}
    </div>
  );
}

/* The full before/after, for the row that opens. Merit, not direction: for
   CPA and CPM a rise is worse, for the rates a fall is -- said by the word
   .delta.good/.bad prints, not by a colour. */
function MovedTable({ moved }: { moved: Moved[] | null | undefined }) {
  if (!moved || !moved.length) return null;
  return (
    <div className="table-wrap" style={{ marginTop: 8, maxWidth: "34rem" }}>
      <table>
        <thead><tr><th>metric</th><th className="num">before</th>
          <th className="num">now</th><th className="num">change</th></tr></thead>
        <tbody>
          {moved.map((m, i) => {
            const bad = m.metric === "CPM" || m.metric === "CPA" ? m.pct > 0 : m.pct < 0;
            return (
              <tr key={i}>
                <td>{m.metric}</td>
                <td className="num">{num(m.prior, 2)}</td>
                <td className="num">{num(m.recent, 2)}</td>
                <td className="num"><span className={`delta ${bad ? "bad" : "good"} ${m.pct > 0 ? "up" : "down"}`}>
                  {num(m.pct, 0)}%</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* What the ad actually says, read out of ads.ad_copy; nothing is generated. */
function CurrentCopy({ copy }: { copy: AdCopy | null | undefined }) {
  if (!copy || !(copy.first_headline || copy.first_body)) return null;
  return (
    <div className="quote">
      {copy.first_headline && <div><strong>{copy.first_headline}</strong></div>}
      {copy.first_body && <div className="muted" style={{ marginTop: 2 }}>{copy.first_body}</div>}
    </div>
  );
}

const TONE: Record<string, string> = {
  concluded: "ok", running: "warn", proposed: "quiet", awaiting_conclusion: "stop",
};

export function ExperimentsPage() {
  const page = usePage<ExperimentsData>("/experiments", "experiments", "Experiments");
  if (page.gate) return page.gate;
  const data = page.data;

  const { d, ideas } = data;
  const brand = data.brand || "renegade";
  const rows = d.rows ?? [];
  const running = rows.filter((r) => r.state === "running");
  const waiting = rows.filter((r) => r.state === "awaiting_conclusion");
  const props = (ideas ? ideas.proposals : null) ?? [];
  const fileable = props.filter((p) => p.fileable);
  const dx = (ideas ? ideas.diagnoses : null) ?? [];
  const recs = data.recommended ?? [];
  const seen = data.launches ?? [];
  const unfiled = data.unfiled ?? [];
  const suggestions = `/suggestions?brand=${brand}`;

  return (
    <PageFrame nav="experiments" title="Experiments" brand={d.brand} settled={data.settled}>
      {/* ---------------------------------------------------- SCOREBOARD */}
      <div className="stats">
        <div className="stat"><div className="k">Filed</div><div className="v">{num(rows.length)}</div>
          {d.awaiting_launch ? <div className="sub">{num(d.awaiting_launch)} never launched</div> : null}</div>
        <div className="stat"><div className="k">Running</div><div className="v">{num(running.length)}</div></div>
        <div className="stat"><div className="k">Awaiting a conclusion</div><div className="v">{num(waiting.length)}</div>
          {waiting.length > 0 && <div className="sub">a person writes it, under their name</div>}</div>
        <div className="stat"><div className="k">Launch detected</div><div className="v">{num(seen.length)}</div>
          {seen.length > 0 && <div className="sub">a person confirms started_on</div>}</div>
        {ideas ? (
          <>
            <div className="stat"><div className="k">Ideas</div><div className="v">{num(props.length)}</div>
              <div className="sub">{num(fileable.length)} ready to file</div></div>
            <div className="stat"><div className="k">Not a creative problem</div><div className="v">{num(dx.length)}</div></div>
          </>
        ) : (
          <div className="stat"><div className="k">Tests the report recommends</div><div className="v">{num(recs.length)}</div>
            <div className="sub">not launched yet</div></div>
        )}
      </div>

      {/* ------------------------------------------------------- THE LOG
          First when it has anything in it: decided outranks suggested. */}
      {rows.length > 0 && (
        <section className="stage" aria-label="The experiment log">
          <StageHead title="The log" source="intel experiments" />
          <div className="table-wrap">
            <table>
              <thead><tr>
                <th>Name</th><th>State</th><th>Hypothesis</th><th>Metric</th>
                <th className="num">Min effect</th><th className="num">Min spend/arm</th>
                <th>Arms</th><th>Conclusion</th>
              </tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.id ?? i}>
                    <td className="name"><strong>{r.name}</strong><div className="why">{r.question}</div></td>
                    <td>
                      <span className={`pill ${TONE[r.state] ?? "quiet"}`}>{swap(r.state, "_", " ")}</span>
                      {r.launch && <div className="why">Launch detected {r.launch.implemented_on}</div>}
                    </td>
                    <td className="small">{truncate(r.hypothesis ?? "", 90)}</td>
                    <td><span className="pill quiet">{r.primary_metric}</span></td>
                    <td className="num">{num(r.minimum_effect_pct, 1)}%</td>
                    <td className="num">{money(r.minimum_spend_per_arm)}</td>
                    <td className="small">{(r.arms ?? []).map((a) => a.label).join(" vs ")}</td>
                    <td className="small">{r.conclusion
                      ? <>{r.conclusion}<div className="why">{r.concluded_by}</div></>
                      : <span className="muted">--</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ------------------------------------------------ LAUNCH DETECTED
          The ledger saw the new ad. The date is the ledger's implemented_on
          (the new ad's first spend day, from SQL, or the review date that saw
          it). It is not recorded on the experiment until a person runs the
          statement: `and started_on is null` keeps it from moving a window
          already being read. The brand is the page's; quotes in the name are
          doubled. */}
      {seen.length > 0 && (
        <section className="stage" aria-label="Launch detected">
          <StageHead title="Launch detected"
                     state={<span className="state">proposed · started_on is recorded by a person</span>} />
          {seen.map((r, i) => (
            <div key={r.id ?? i} className="card" style={{ marginBottom: 12 }}>
              <p><strong>{r.name}</strong></p>
              <p className="small" style={{ marginTop: 4 }}>Launch detected {r.launch?.implemented_on} ({r.launch?.match}, {r.launch?.matched_by})</p>
              <p className="small muted" style={{ marginTop: 4 }}>If the new ad went live that day, the person
                who launched it records it by running this against the database, under their own
                name. Change the date first if it went live on another day.</p>
              <pre className="mono" style={{ margin: "8px 0 0", whiteSpace: "pre-wrap" }}>{confirmSql(brand, r.name, r.launch?.implemented_on ?? "")}</pre>
              <p className="small muted" style={{ marginTop: 6 }}>Then read it with{" "}
                <code>python -m intel experiment --brand {brand} --name "{r.name}"</code>.
                The conclusion is written by a person too, after reading it.</p>
            </div>
          ))}
        </section>
      )}

      {unfiled.length > 0 && (
        <section className="stage" aria-label="Launched, not filed yet">
          <StageHead title="Launched, not filed yet"
                     state={<span className="state">the next review tries again</span>} />
          <div className="card" style={{ padding: 0 }}>
            {unfiled.map((u) => (
              <div key={u.id} className="quiet-row">
                <span className="t">{u.id}</span>
                <span className="w">{u.campaign} · {u.variable} · launch detected{" "}
                  {u.implemented_on} ({u.match}, {u.matched_by}). {u.why}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ------------------------------------- TESTS THE REPORT RECOMMENDS
          From the outcomes ledger: every test the campaign report has
          recommended that nobody has launched. The tag is the whole hand-off:
          put it on the new ad as utm_content and the next review sees the
          launch and files the experiment. The full plan, control and
          challenger, is on /suggestions. */}
      {!ideas && (
        <section className="stage" aria-label="Tests the report recommends">
          <StageHead title="Tests the report recommends"
                     state={<span className="state">{recs.length} not launched · Source: <Link to={suggestions}>/suggestions</Link></span>} />
          {!recs.length ? (
            <Empty>Every test the report has recommended has been launched, or none
              has been recommended yet. The next review after an import adds any new one here.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead><tr>
                  <th scope="col">Campaign</th><th scope="col">What is tested</th>
                  <th scope="col">Tag to use</th><th scope="col">Recommended</th>
                </tr></thead>
                <tbody>
                  {recs.map((t) => (
                    <tr key={t.id}>
                      <td className="name"><strong>{t.campaign}</strong>
                        {t.ad_group && <div className="why">{t.ad_group}</div>}</td>
                      <td className="small" style={{ maxWidth: 440 }}>
                        <span className="pill quiet">{swap(t.variable || t.kind || "", "_", " ")}</span>
                        <div style={{ marginTop: 4 }}>{t.challenger || "--"}</div>
                        {t.control && <div className="why">against {t.control}</div>}
                        {t.setup && <div className="why">{t.setup}</div>}
                      </td>
                      <td className="small">{t.tag
                        ? <code>utm_content={t.tag}</code>
                        : <span className="muted">no tag yet; the next review adds it</span>}</td>
                      <td className="small">{t.recommended_on}
                        <div className="why"><Link to={suggestions}>the report</Link></div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {ideas && (
        <>
          {/* ===== FALLBACK: `intel propose`, only while the ledger is empty. =====

              NOT A CREATIVE PROBLEM. Above the ideas, deliberately: the ad is
              still earning the click and what follows it is not converting, so
              rewriting the creative would change the one part that works. No
              arms and no filing command -- there is nothing on the AD to test,
              and giving these the shape of an experiment would invite somebody
              to register one. */}
          {dx.length > 0 && (
            <section className="stage" aria-label="Not a creative problem">
              <StageHead title="Not a creative problem"
                         state={<span className="state">findings, not tests</span>} />
              {dx.map((x, i) => (
                <div key={i} className="card" style={{ marginBottom: 12 }}>
                  <div className="finding flag" style={{ padding: 0, border: 0, background: "none", alignItems: "flex-start" }}>
                    <div className="rank" aria-hidden="true">!</div>
                    <div className="says">
                      <p><strong>{x.title}</strong> <span className="pill stop">{x.diagnosis.stage}</span></p>
                      <p style={{ marginTop: 4 }}>{x.diagnosis.says}</p>
                      <div style={{ marginTop: 8 }}><Chips moved={x.moved} /></div>
                      <details style={{ marginTop: 8 }}>
                        <summary className="small" style={{ cursor: "pointer" }}>The change it points to, and the copy</summary>
                        <div className="small muted" style={{ marginTop: 6, maxWidth: "86ch" }}>{x.diagnosis.change}</div>
                        <MovedTable moved={x.moved} />
                        {x.diagnosis.also_broke && x.diagnosis.also_broke.length > 0 && (
                          <div className="small muted" style={{ marginTop: 4 }}>Also moved:{" "}
                            {x.diagnosis.also_broke.join(", ")}</div>
                        )}
                        <CurrentCopy copy={x.ad_copy} />
                        <div className="small muted" style={{ marginTop: 6 }}>{x.not_an_experiment}</div>
                      </details>
                    </div>
                    <div style={{ flex: "none", textAlign: "right" }}>
                      <div className="k" style={{ fontSize: 11.5, color: "var(--muted)" }}>spend, {x.window}</div>
                      <div className="big" style={{ fontSize: 19 }}>{money(x.spend_recent)}</div>
                    </div>
                  </div>
                </div>
              ))}
            </section>
          )}

          {/* ------------------------------------------------------- IDEAS
              Every row is unfiled and says so. Filing is `intel record`, which
              takes a path and is run by a person -- there is no button, for
              the reason ui.py's header gives about every other button on this
              site. Both thresholds are deliberately blank: deciding what would
              convince you after reading the window is not deciding it in
              advance. */}
          <section className="stage" aria-label="Ideas">
            <div className="stage-head"><h2>Ideas</h2>
              <span className="state">{ideas.proposal_count} · none filed · Source: <code>intel propose</code></span></div>
            {!props.length ? (
              <Empty>Nothing to propose from this window. That is a real answer:
                no ad cleared the fatigue confidence bar and no style is notably under-funded.</Empty>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead><tr>
                    <th scope="col" style={{ width: 28 }}></th><th scope="col">Idea</th>
                    <th scope="col">From</th><th scope="col">What moved</th><th scope="col">Status</th>
                  </tr></thead>
                  <tbody>
                    {props.map((p, i) => (
                      <tr key={i}>
                        <td><strong style={{ color: "var(--accent)" }}>{i + 1}</strong></td>
                        <td className="name" style={{ maxWidth: 440 }}>
                          <strong>{p.title}</strong>
                          <div className="why">{p.question}</div>
                          <details style={{ marginTop: 6 }}>
                            <summary className="small" style={{ cursor: "pointer" }}>The full card</summary>
                            <div className="small muted" style={{ marginTop: 6 }}>{p.why}</div>
                            {p.moved && p.moved.length > 0 && <MovedTable moved={p.moved} />}
                            {p.diagnosis && p.diagnosis.also_broke && p.diagnosis.also_broke.length > 0 && (
                              <div className="small muted" style={{ marginTop: 4 }}>Also moved:{" "}
                                {p.diagnosis.also_broke.join(", ")}</div>
                            )}
                            <CurrentCopy copy={p.ad_copy} />
                            {p.styles_untried && p.styles_untried.length > 0 && (
                              <div className="small" style={{ marginTop: 8 }}>
                                <span className="muted">Run as</span> {(p.styles_tried ?? []).join(", ") || "--"}.{" "}
                                <span className="muted">Never as</span> <strong>{p.styles_untried.slice(0, 6).join(", ")}</strong>.</div>
                            )}
                            {/* THE BRIEF IS A HANDOFF, NOT COPY. Every line is read from
                                the database; nothing in this repo writes ad text, because
                                the claim gate, the approved concept and the signature live
                                in growth-engine. */}
                            {p.brief && (
                              <div className="quote" style={{ background: "var(--surface-3)" }}>
                                <div><span className="muted">angle</span> {p.brief.angle}</div>
                                <div><span className="muted">broke</span> {p.brief.broke}</div>
                                <div><span className="muted">change</span> {p.brief.change}</div>
                                <div><span className="muted">beat</span> {p.brief.beat.metric} {num(p.brief.beat.current, 2)}</div>
                                <div><span className="muted">never run as</span> {p.brief.never_run_as.slice(0, 6).join(", ") || "--"}</div>
                                <pre className="mono" style={{ margin: "8px 0 0", whiteSpace: "pre-wrap" }}>{p.brief.written_where}</pre>
                              </div>
                            )}
                            <div className="small" style={{ marginTop: 6 }}>
                              {p.arms.map((a, j) => <Fragment key={j}><span className="pill quiet">{a.label}</span>{" "}</Fragment>)}
                              <span className="pill">{p.would_register.primary_metric}</span>
                            </div>
                          </details>
                        </td>
                        <td><span className="pill quiet">{swap(p.source || "", "_", " ")}</span>
                          {p.already_tested && <div style={{ marginTop: 4 }}><span className="pill warn">tested before</span></div>}</td>
                        <td>{p.moved && p.moved.length > 0 ? <Chips moved={p.moved} /> : <Dash />}</td>
                        <td className="small">
                          {p.fileable ? (
                            <>
                              <span className="pill ok">ready to file</span>
                              <div style={{ marginTop: 4 }}><code>{p.how_to_file}</code></div>
                            </>
                          ) : (
                            <>
                              <span className="pill quiet">not fileable yet</span>
                              <div className="why">waits on {swap(p.blocked_on || "", " -- ", " — ")}</div>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Observations: they describe what already ran, so their thresholds
              would be chosen with the numbers in view and none can be
              registered as a test. */}
          {ideas.observations && ideas.observations.length > 0 && (
            <section className="stage" aria-label="Worth looking at">
              <StageHead title="Worth looking at"
                         state={<span className="state">describes what already ran, so it cannot be a test</span>} />
              <div className="card">
                {ideas.observations.map((o, i) => <p key={i} style={{ margin: "0 0 6px", maxWidth: "86ch" }}>{o.says}</p>)}
              </div>
            </section>
          )}
        </>
      )}

      {!rows.length && (
        <section className="stage" aria-label="Nothing to report">
          <StageHead title="Nothing to report, and why" />
          <div className="card" style={{ padding: 0 }}>
            <div className="quiet-row">
              <span className="t">The log</span><span className="pill quiet">nothing to show</span>
              <span className="w">Nothing has been filed. A recommended test is filed on its own
                when the next review sees its tagged ad launched; one can also be filed by hand with{" "}
                <code>python -m intel record --kind experiment --json &lt;path&gt;</code>.</span>
            </div>
          </div>
        </section>
      )}
    </PageFrame>
  );
}
