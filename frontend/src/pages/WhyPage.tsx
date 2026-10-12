/* WHY CPA MOVED. A CPA change is almost never one thing: it splits into a
 * RATE effect (an ad got dearer) and a MIX effect (budget moved toward an ad
 * that already was). Ported from templates/why.html.
 *
 * The account edits come first, because a budget, status or creative edit is
 * a CAUSE and the rate/mix split is arithmetic -- read in the other order,
 * every edit gets re-explained as creative decay. The split is drawn with a
 * third bar, the ads that converted in only one of the two windows, so the
 * bars add up to the net.
 */
import { PageFrame } from "../components/Layout";
import { EffectBars, Meter, SplitBar } from "../components/charts";
import { AdLink, Dash, Empty, More, Seg, StageHead, stamp, usePage } from "../components/ui";
import type { PageBase } from "../lib/api";
import { money, num, pct, words } from "../lib/format";

interface Edit {
  changed_at: string;
  entity_name: string | null;
  entity_id: string;
  entity_type: string;
  field: string;
  old_value: string | null;
  new_value: string | null;
}

interface Mover {
  ad_key: string;
  entity_name: string | null;
  reason: string;
  spend_current: number | null;
  spend_prior: number | null;
  cpa_current: number | null;
  cpa_prior: number | null;
  rate_effect: number | null;
  mix_effect: number | null;
  total_effect: number;
}

export interface WhyData extends PageBase {
  d: {
    brand: string; days: number;
    totals: {
      cpa_change: number | null; rate_effect: number | null; mix_effect: number | null;
      unattributable_effect?: number | null; attributable_share?: number | null;
    } | null;
    structure_changes: { available: boolean; why?: string | null; rows?: Edit[] } | null;
    rows: Mover[];
  };
  days: number;
}

/** '+' before a positive figure; money() already puts the minus on a negative one. */
export const signed = (v: number | null | undefined) => `${v !== null && v !== undefined && v > 0 ? "+" : ""}${money(v)}`;

function Movers({ title, rows, brand }: { title: string; rows: Mover[]; brand: string }) {
  const five = rows.slice(0, 5);
  const tops = five.map((r) => Math.abs(r.total_effect));
  const top = tops.length ? Math.max(...tops) : null;
  return (
    <section aria-label={title}>
      <div className="stage-head"><h2>{title}</h2></div>
      {!rows.length ? <Empty>Nothing moved it this way.</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr>
              <th scope="col">Ad</th>
              <th scope="col" className="num">CPA before → now</th>
              <th scope="col" className="num">Effect</th>
            </tr></thead>
            <tbody>
              {five.map((r) => (
                <tr key={r.ad_key}>
                  <td className="name"><AdLink adKey={r.ad_key} brand={brand}>{r.entity_name || r.ad_key}</AdLink>
                    {r.reason !== "attributable" && <div className="why">{words(r.reason)}</div>}</td>
                  <td className="num">{r.cpa_prior ? money(r.cpa_prior) : <Dash />} →{" "}
                    <strong>{r.cpa_current ? money(r.cpa_current) : <Dash />}</strong></td>
                  <td className="num"><div className="num-bar"><Meter value={r.total_effect} top={top} width={70} />
                    <strong style={{ minWidth: 52 }}>{signed(r.total_effect)}</strong></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function WhyPage() {
  const page = usePage<WhyData>("/why", "why", "Why CPA moved");
  if (page.gate) return page.gate;
  const data = page.data;
  const { d, days } = data;
  const t: NonNullable<WhyData["d"]["totals"]> = d.totals ?? { cpa_change: null, rate_effect: null, mix_effect: null };
  const ch = d.structure_changes ?? { available: false };
  const edits = ch.rows ?? [];
  const up = d.rows.filter((r) => r.total_effect > 0).sort((a, b) => b.total_effect - a.total_effect);
  const down = d.rows.filter((r) => r.total_effect < 0).sort((a, b) => a.total_effect - b.total_effect);
  const oneWindow = d.rows.filter((r) => r.reason !== "attributable");
  const rest = d.rows.length - Math.min(up.length, 5) - Math.min(down.length, 5);

  const eff: [string, unknown, number][] = [["Rate effect", t.rate_effect, -1], ["Mix effect", t.mix_effect, -1]];
  if (t.unattributable_effect !== undefined && t.unattributable_effect !== null) {
    eff.push(["One window", t.unattributable_effect, -1]);
  }
  eff.push(["Net", t.cpa_change, -1]);
  // The share is quoted only when it can honestly be called coverage: when the
  // one-window ads move CPA the other way it reads 126%, true and meaningless.
  const share = t.attributable_share ?? null;

  return (
    <PageFrame nav="why" title="Why CPA moved" brand={d.brand} settled={data.settled}
               toolbar={<Seg param="days" options={[7, 14, 28]} current={days} />}>
      <section className="answer" aria-label="What moved CPA">
        <p>
          {t.cpa_change === null || t.cpa_change === undefined || t.cpa_change === 0
            ? <>CPA did not move over the last {d.days} days</>
            : <>CPA {t.cpa_change > 0 ? "rose" : "fell"} <strong>{money(Math.abs(t.cpa_change))}</strong> over
                the last {d.days} days. Rate effect was <strong>{signed(t.rate_effect)}</strong> and mix
                effect <strong>{signed(t.mix_effect)}</strong></>}
          {ch.available
            ? <>, and <strong>{edits.length}</strong> account edit{edits.length === 1 ? "" : "s"} landed in the same window.</>
            : "."}
        </p>
        <div className="jumps">
          <a href="#edits">Account edits</a>
          <a href="#split">The split</a>
          <a href="#ads">Which ads</a>
        </div>
      </section>

      {/* "no changes" and "we cannot see changes" look identical as an empty
          list, and only one is a fact, so `why` is printed verbatim. */}
      <section className="stage" id="edits" aria-label="What changed in the account">
        <StageHead title="Read these first: what changed in the account" source="ads.structure_change" />
        {!ch.available ? (
          <Empty>{ch.why || "The change log could not be read, which is itself a gap."}</Empty>
        ) : !edits.length ? (
          <Empty>No budget, status or goal edit was recorded in this window, and the change log was
            readable, so this is a finding rather than a blind spot.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr>
                <th scope="col">When</th><th scope="col">What</th>
                <th scope="col">Field</th><th scope="col">From → to</th>
              </tr></thead>
              <tbody>
                {edits.map((c, i) => (
                  <tr key={i}>
                    <td className="small">{stamp(c.changed_at)}</td>
                    <td className="name">{c.entity_name || c.entity_id}
                      <div className="why">{words(c.entity_type)}</div></td>
                    <td><span className="pill quiet">{c.field.replace(/_/g, " ")}</span></td>
                    <td className="mono">{c.old_value || "--"} → {c.new_value || "--"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="stage" id="split" aria-label="The split">
        <StageHead title="The two things that can move a CPA" source="ads.cpa_bridge_totals" />
        <div className="grid">
          <div className="card span2">
            <EffectBars rows={eff} kind="cpa" width={640} rowH={30} labelW={76} valueW={90} />
          </div>
          <div className="card">
            <div className="k">Outside the split</div>
            <div className="big">{t.unattributable_effect !== undefined ? signed(t.unattributable_effect) : "--"}</div>
            {share !== null && share >= 0 && share <= 100 && (
              <>
                <div style={{ marginTop: 9, borderRadius: 3, overflow: "hidden" }}>
                  <SplitBar segments={[["Covered", share, "--accent"], ["Not covered", 100 - share, "--line"]]} height={8} />
                </div>
                <div className="n" style={{ marginTop: 6 }}>the split covers {pct(share)} of the move</div>
              </>
            )}
            <div className="n" style={{ marginTop: 8 }}>{num(oneWindow.length)} of the {num(d.rows.length)} ads
              below converted in only one of the two windows, so no rate/mix split exists for them.</div>
          </div>
        </div>
      </section>

      <section className="stage" id="ads" aria-label="Which ads">
        {!d.rows.length ? <Empty>Nothing spent in either window.</Empty> : (
          <>
            <div className="boards">
              <Movers title="Pushed CPA up" rows={up} brand={d.brand} />
              <Movers title="Pulled CPA down" rows={down} brand={d.brand} />
            </div>
            {rest > 0 && (
              <More style={{ marginTop: 14, background: "var(--surface)", border: "1px solid var(--line)",
                             borderRadius: "var(--radius)" }}
                    summary={<><strong>Show all {d.rows.length}</strong> — {num(rest)} more moved it less.</>}>
                <div className="table-wrap" style={{ border: 0 }}>
                  <table>
                    <thead><tr>
                      <th scope="col">Ad</th>
                      <th scope="col" className="num">Spend now</th><th scope="col" className="num">Spend before</th>
                      <th scope="col" className="num">CPA now</th><th scope="col" className="num">CPA before</th>
                      <th scope="col" className="num">Rate</th><th scope="col" className="num">Mix</th>
                      <th scope="col" className="num">Total</th>
                    </tr></thead>
                    <tbody>
                      {d.rows.map((r) => (
                        <tr key={r.ad_key}>
                          <td className="name"><AdLink adKey={r.ad_key} brand={d.brand}>{r.entity_name || r.ad_key}</AdLink>
                            {r.reason !== "attributable" && <div className="why">{words(r.reason)}</div>}</td>
                          <td className="num">{money(r.spend_current)}</td>
                          <td className="num">{money(r.spend_prior)}</td>
                          <td className="num">{r.cpa_current ? money(r.cpa_current) : "--"}</td>
                          <td className="num">{r.cpa_prior ? money(r.cpa_prior) : "--"}</td>
                          <td className="num">{r.rate_effect !== null ? signed(r.rate_effect) : "--"}</td>
                          <td className="num">{r.mix_effect !== null ? signed(r.mix_effect) : "--"}</td>
                          <td className="num"><strong>{signed(r.total_effect)}</strong></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </More>
            )}
          </>
        )}
      </section>
    </PageFrame>
  );
}
