/* ANGLES. What each angle cost, and -- the part free-text handles cannot
 * answer -- which ones have never run at all. Ported from templates/angles.html.
 *
 * On this brand the angle bank has no active members, so every angle number
 * is empty by construction (CLAUDE.md, "What is structurally empty"). The page
 * leads with the reason, draws the two coverages side by side -- the copy IS
 * labelled by the tagging pass, the angle is not -- and shows the three steps
 * that would change it. The empty sections fold into one line each; the day
 * an angle is signed, the full sections come back by themselves, because each
 * renders whenever it has rows.
 *
 * The approval step stays a person's. There is no button here that signs
 * anything, and the steps name growth-engine as where that happens.
 */
import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { PageFrame } from "../components/Layout";
import { BarChart, SplitBar } from "../components/charts";
import { AdLink, Empty, Seg, StageHead, usePage } from "../components/ui";
import { withParams, type PageBase } from "../lib/api";
import { money, num, truncate } from "../lib/format";

interface AngleRow {
  angle_slug: string;
  angle_name: string;
  family: string | null;
  cpa?: number | null;
}

interface PriorExperiment {
  name: string;
  conclusion: string | null;
  angle_slug: string | null;
}

interface Candidate {
  observed: string;
  reviews: number | null;
  last_seen: string;
}

interface QueueRow {
  ad_key: string;
  entity_name: string | null;
  first_headline: string | null;
  spend: number | null;
  conversions: number | null;
  inheritable: boolean | null;
  campaign_angle_name: string | null;
  angle_observed: string | null;
}

export interface AnglesData extends PageBase {
  d: {
    brand: string;
    product: string | null;
    tested: AngleRow[];
    under_spent: AngleRow[];
    never_run: AngleRow[];
    prior_experiments: PriorExperiment[];
  };
  days: number;
  candidates: Candidate[];
  queue: { rows: QueueRow[] };
  labels: {
    creative_labels?: { ads?: number | null; with_a_hook?: number | null; with_an_offer?: number | null } | null;
    angle_attribution?: { total_spend?: number | null; tagged_spend?: number | null; untagged_spend?: number | null } | null;
  };
  /** (angle_name, cpa) for every tested or under-spent angle. */
  bars: [string, number | null][];
}


/** The product filter: the GET to /angles became a navigation with the same query. */
function ProductFilter({ product }: { product: string | null }) {
  const { search } = useLocation();
  const navigate = useNavigate();
  const [value, setValue] = useState(product ?? "");
  const apply = (e: FormEvent) => {
    e.preventDefault();
    navigate(`/angles${withParams(search, { product: value })}`);
  };
  return (
    <form className="filters" style={{ padding: 0, margin: 0, border: 0, background: "none" }} onSubmit={apply}>
      <label htmlFor="product">Product
        <input id="product" name="product" value={value} placeholder="all" size={8}
               onChange={(e) => setValue(e.target.value)} />
      </label>
      <button type="submit" className="btn ghost">Apply</button>
    </form>
  );
}

export function AnglesPage() {
  const page = usePage<AnglesData>("/angles", "angles", "Angles");
  if (page.gate) return page.gate;
  const data = page.data;
  const { d, days, candidates, queue, labels, bars } = data;
  const cl = labels.creative_labels ?? {};
  const aa = labels.angle_attribution ?? {};
  const tested = d.tested ?? [], underSpent = d.under_spent ?? [], neverRun = d.never_run ?? [];
  const inert = !tested.length && !underSpent.length && !neverRun.length;

  // Each section renders when it has rows and folds into one line when it does not.
  const silent: [string, string, string][] = [];
  if (!bars.length) {
    silent.push(["CPA by angle", "nothing to show", `No active angle carried spend in the last ${days} days.`]);
  }
  if (!neverRun.length) {
    silent.push(["Never run", "nothing to show",
      inert ? "A set difference against a bank with no active members." : "Every active angle has run in this window."]);
  }
  if (!candidates.length) {
    silent.push(["Unmapped handles", "clear", "Every handle a reviewer wrote maps to an angle."]);
  }

  const toolbar = (
    <>
      <Seg param="days" options={[30, 90, 180, 365]} current={days} />
      <ProductFilter key={d.product ?? ""} product={d.product} />
    </>
  );

  return (
    <PageFrame nav="angles" title="Angles" brand={d.brand} settled={data.settled} toolbar={toolbar}>
      <section className="answer" aria-label="Angles in one line">
        <p>
          {inert
            ? <>No angle is active in the bank, so no ad can carry one.</>
            : <><strong>{tested.length}</strong> active angle(s) ran in this window,{" "}
                <strong>{underSpent.length}</strong> too little to read and{" "}
                <strong>{neverRun.length}</strong> not at all.</>}
          {cl.ads ? <> Hook, offer and audience are labelled on{" "}
            <strong>{num(cl.with_an_offer)}</strong> of <strong>{num(cl.ads)}</strong>{" "}
            ads that spent{inert && <> — only angle is empty</>}.</> : null}
        </p>
        {inert && <div className="jumps"><a href="#steps">How an angle becomes active</a></div>}
      </section>

      {/* Two questions, two cards. Printing only the angle one says "100%
          untagged" about copy that has been read. */}
      <div className="boards" style={{ marginBottom: 20 }}>
        <section className="card" aria-label="Creative labels">
          <div className="card-head"><h2 className="card-t">Creative labels</h2>
            <span className="more">Source: <code>ads.facet_effective</code></span></div>
          <div className="big">{num(cl.with_an_offer)}{" "}
            <span className="small muted" style={{ fontWeight: 500 }}>of {num(cl.ads)} ads</span></div>
          {cl.ads ? (
            <>
              <div style={{ marginTop: 10, borderRadius: 3, overflow: "hidden" }}>
                <SplitBar segments={[["Labelled", cl.with_an_offer, "--accent"],
                                     ["Not labelled", cl.ads - (cl.with_an_offer ?? 0), "--line"]]} height={12} />
              </div>
              <div className="n" style={{ marginTop: 6 }}>hook on {num(cl.with_a_hook)} ·{" "}
                offer on {num(cl.with_an_offer)}</div>
            </>
          ) : null}
        </section>
        <section className="card" aria-label="Angle">
          <div className="card-head"><h2 className="card-t">Angle</h2>
            <span className="more">Source: <code>ads.untagged_spend</code></span></div>
          <div className="big">{money(aa.tagged_spend)}{" "}
            <span className="small muted" style={{ fontWeight: 500 }}>of {money(aa.total_spend)}</span></div>
          {aa.tagged_spend ? (
            <div style={{ marginTop: 10, borderRadius: 3, overflow: "hidden" }}>
              <SplitBar segments={[["With an angle", aa.tagged_spend, "--accent"],
                                   ["Without", aa.untagged_spend, "--line"]]} height={12} />
            </div>
          ) : (
            <>
              <div style={{ marginTop: 10, height: 12, borderRadius: 3, border: "1px dashed var(--focus)" }} />
              <div className="n" style={{ marginTop: 6 }}>zero until the bank has an active member</div>
            </>
          )}
        </section>
      </div>

      {/* The steps. Shown while the bank is inert, because it is the one thing
          on this page somebody can act on. The signing step names growth-engine
          and a person: CLAUDE.md is explicit that no verb here performs it. */}
      {inert && (
        <section className="stage" id="steps" aria-label="How an angle becomes active">
          <div className="stage-head"><h2>How an angle becomes active</h2></div>
          <div className="steps">
            <div className="step"><div className="step-h"><span className="n">1</span><strong>Propose</strong></div>
              <p>An agent or a person files it. It lands as proposed and counts for nothing yet.</p>
              <code>python -m intel record --kind angle_proposal</code></div>
            <div className="step-arrow" aria-hidden="true">→</div>
            <div className="step"><div className="step-h"><span className="n">2</span><strong>Sign</strong></div>
              <p>A person signs it in growth-engine, under their own name. Nothing on this site does this step.</p></div>
            <div className="step-arrow" aria-hidden="true">→</div>
            <div className="step"><div className="step-h"><span className="n">3</span><strong>Active</strong></div>
              <p>Every angle number on the site starts filling in, and ads can be tagged against it.</p></div>
          </div>
        </section>
      )}

      {bars.length > 0 && (
        <section className="stage" aria-label="CPA by angle">
          <StageHead title="CPA by angle" source="intel coverage" />
          <div className="card">
            <BarChart rows={bars} kind="cpa" />
            <p className="n" style={{ margin: "10px 0 0" }}>Only comparable between angles whose ads ran
              under the same optimization goal.</p>
          </div>
        </section>
      )}

      {neverRun.length > 0 && (
        <section className="stage" aria-label="Never run">
          <div className="stage-head"><h2>Never run</h2></div>
          <div className="card">
            {neverRun.map((r) => {
              const prior = (d.prior_experiments ?? []).filter((p) => p.angle_slug === r.angle_slug);
              return (
                <div key={r.angle_slug} style={{ marginBottom: 10 }}>
                  <span className="pill quiet">{r.family}</span> <strong>{r.angle_name}</strong>
                  {prior.length > 0 && <div className="why">tested before: {prior[0].name} —{" "}
                    {prior[0].conclusion || "no conclusion recorded"}</div>}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {candidates.length > 0 && (
        <section className="stage" aria-label="Unmapped handles">
          <div className="stage-head"><h2>Unmapped handles</h2></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Observed</th><th scope="col" className="num">Reviews</th>
                <th scope="col">Last seen</th></tr></thead>
              <tbody>
                {candidates.slice(0, 12).map((c) => (
                  <tr key={c.observed}><td>{c.observed}</td><td className="num">{c.reviews}</td>
                    <td className="small muted">{String(c.last_seen).slice(0, 10)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {silent.length > 0 && (
        <section className="stage" aria-label="Nothing to report">
          <div className="stage-head"><h2>Nothing to report, and why</h2></div>
          <div className="card" style={{ padding: 0 }}>
            {silent.map(([title, code, why]) => (
              <div className="quiet-row" key={title}>
                <span className="t">{title}</span>
                <span className={`pill ${code === "clear" ? "ok" : "quiet"}`}>{code}</span>
                <span className="w">{why}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Ads that spent and carry no label at all for the wording they run now --
          ranked by spend, because labelling effort should follow the money. */}
      <section className="stage" aria-label="Still unlabelled">
        <StageHead title="Still unlabelled" source="intel queue" />
        {!queue.rows.length ? (
          <Empty>Every ad that spent in this window carries a label.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr>
                <th scope="col">Ad</th><th scope="col">Headline</th>
                <th scope="col" className="num">Spend</th><th scope="col" className="num">Conv</th>
                <th scope="col">Can be inherited</th>
              </tr></thead>
              <tbody>
                {queue.rows.map((r) => (
                  <tr key={r.ad_key}>
                    <td className="name"><AdLink adKey={r.ad_key} brand={d.brand}>{r.entity_name}</AdLink></td>
                    <td className="small">{truncate(r.first_headline || "--", 60)}</td>
                    <td className="num">{money(r.spend)}</td>
                    <td className="num">{num(r.conversions)}</td>
                    <td>
                      {r.inheritable ? <span className="pill ok">{r.campaign_angle_name}</span>
                        : r.angle_observed ? <span className="muted small">reviewer said “{r.angle_observed}”</span>
                        : <span className="muted small">needs reading</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </PageFrame>
  );
}
