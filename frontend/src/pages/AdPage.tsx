/* ONE AD. Its own numbers, what the rules say about it, the words it runs,
 * and how it spent day by day. Ported from templates/ad.html.
 *
 * The rules' readings lead the page. They cost nothing -- rules over a fact
 * pack, no model -- and they are this ad's analysis before anybody asks for
 * one, which is what the dashboard now opens every page with. The tiles above
 * them are the ad's own window from ads.window_metrics, and the fatigue tile
 * is the same five-cell grid the brief and Fatigue draw.
 *
 * "Ask about this ad" is the one button left on the site that calls a model,
 * and it stays a button for the reason it was one: a model call per page view
 * on 245 ads is a cost somebody should choose to spend. Its answer lands
 * beside the copy it is about.
 *
 * The 31-row daily table folds behind a line; the two charts above it carry
 * the shape and the table is there for the exact figure.
 */
import { PageFrame } from "../components/Layout";
import { AnalysisPanel, AskButton, useAnalysis } from "../components/AskButton";
import { LineChart } from "../components/charts";
import { Caveat, Empty, More, usePage } from "../components/ui";
import { useAdKey, type PageBase } from "../lib/api";
import { money, num, pct, truncate, words } from "../lib/format";

interface AdHead {
  ad_key: string;
  name: string | null;
  effective_status: string | null;
  optimization_goal: string | null;
  format: string | null;
  cta: string | null;
  link_url: string | null;
  utm_content: string | null;
  thumbnail_url: string | null;
  campaign_name: string | null;
  ad_group_name: string | null;
  /** On ads.ad, but not in metrics.ad's select today; the branch below is
   *  faithful to the template and waits for the column to be carried. */
  needs_page_scope?: boolean | null;
}

interface CopyText {
  field: string;
  ordinal: number | null;
  text: string;
}

interface DailyRow {
  day: string;
  spend: number | null;
  impressions: number | null;
  link_clicks: number | null;
  conversions: number | null;
  frequency_this_day: number | null;
  reach_this_day: number | null;
}

interface Fatigue {
  score: number | null;
  symptoms?: string[] | null;
  confident?: boolean | null;
}

export interface AdData extends PageBase {
  d: { brand: string };
  ad: {
    ad: AdHead;
    copy: { texts: CopyText[] | null } | null;
    tag_is_stale: boolean;
    daily: DailyRow[];
    day_count: number;
  };
  days: number;
  reading: {
    facts?: { fatigue?: Fatigue | null } | null;
    readings?: { says: string }[] | null;
  } | null;
  totals: {
    spend: number | null;
    conversions: number | null;
    rates: Record<string, number | null> | null;
  } | null;
  spend_series: [string, unknown][];
  conv_series: [string, unknown][];
}

const SYMPTOMS: [string, string][] = [
  ["link_ctr_decline", "click-through down"], ["cpm_rise", "impression cost up"],
  ["cpa_rise", "acquisition cost up"], ["frequency_rise", "daily frequency up"],
  ["ranking_drop", "ranking down"],
];


export function AdPage() {
  const adKey = useAdKey();
  const page = usePage<AdData>(`/ad/${adKey}`, "overview", "Ad");
  // The URL is known only once the data is; the button is drawn only then too.
  const askUrl = page.data
    ? `/ad/${adKey}/analyse.json?brand=${encodeURIComponent(page.data.brand ?? "")}&days=${page.data.days}`
    : "";
  const analysis = useAnalysis(askUrl);
  if (page.gate) return page.gate;
  const data = page.data;

  const { ad, days } = data;
  const a = ad.ad;
  const rt = data.totals?.rates ?? {};
  const fat: Partial<Fatigue> = data.reading?.facts?.fatigue ?? {};
  const rules = data.reading?.readings ?? [];
  const texts = ad.copy?.texts ?? [];
  const on = fat.symptoms ?? [];

  const toolbar = (
    <>
      {a.effective_status && (
        <span className={`pill ${a.effective_status === "ACTIVE" ? "ok" : "quiet"}`}>{words(a.effective_status)}</span>
      )}
      {a.optimization_goal && <span className="chip">{words(a.optimization_goal)}</span>}
      {a.format && <span className="chip">{words(a.format)}</span>}
      {/* A button rather than a link because it is not navigation: it starts a
          Claude Code session that reads this ad's numbers, which takes the
          better part of a minute and costs a model call. */}
      <AskButton label="Ask about this ad" phase={analysis.phase} onClick={analysis.ask} />
    </>
  );

  return (
    <PageFrame nav="overview" title={a.name} brand={data.brand} settled={data.settled} toolbar={toolbar}
               sub={<div className="sub">{a.campaign_name || "campaign"} › {a.ad_group_name || "ad group"}</div>}>
      {ad.tag_is_stale && (
        <Caveat><strong>Stale tag.</strong> This ad's copy has changed
          since it was tagged, so its label describes wording that is no longer running.
          Re-tag it before counting it toward an angle.</Caveat>
      )}

      {/* The scoreboard. */}
      <div className="stats">
        <div className="stat"><div className="k">Spend, {days}d</div>
          <div className="v">{money(data.totals?.spend)}</div></div>
        <div className="stat"><div className="k">Conversions</div>
          <div className="v">{num(data.totals?.conversions)}</div></div>
        <div className="stat"><div className="k">CPA</div>
          {/* Comparable only against other ad groups buying the same goal: the
              goal chip in the topbar is there for exactly this tile. */}
          <div className="v">{money(rt.cpa)}</div></div>
        <div className="stat"><div className="k">Link CTR</div>
          <div className="v">{pct(rt.link_ctr)}</div></div>
        <div className="stat"><div className="k">Fatigue</div>
          {fat.score !== null && fat.score !== undefined && fat.symptoms !== undefined ? (
            <>
              <div className="sym" role="img" style={{ marginTop: 8 }}
                   aria-label={on.length ? on.join(", ").replace(/_/g, " ") : "no symptoms"}>
                {SYMPTOMS.map(([key, name]) => (
                  <span key={key} className={on.includes(key) ? "on" : undefined} title={name} />
                ))}
              </div>
              <div className="sub">{num(fat.score)} of 5 symptoms{fat.confident === false && <> · too little spend to read</>}</div>
            </>
          ) : (
            <>
              <div className="v" style={{ fontSize: 16, marginTop: 6 }}>—</div>
              <div className="sub">not in both windows</div>
            </>
          )}
        </div>
      </div>

      {/* What the rules say. */}
      {rules.length > 0 && (
        <section className="stage" aria-label="What the rules say">
          <div className="stage-head"><h2>What the rules say about it</h2>
            <span className="state">readings of the figures on this page, not decisions about them</span></div>
          <div className="card" style={{ padding: 0 }}>
            {rules.map((r, i) => (
              <div className="finding" key={i}>
                <div className="rank" aria-hidden="true">{i + 1}</div>
                <div className="says"><p>{r.says}</p></div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* The copy, and how it spent. */}
      <div className="grid" style={{ marginBottom: 20 }}>
        <section className="card" aria-label="The copy">
          <div className="card-head"><h2 className="card-t">The copy</h2>
            {texts.length > 0 && <span className="more">{texts.length} variant(s)</span>}</div>
          {a.thumbnail_url && (
            <img src={a.thumbnail_url} alt={`Thumbnail of ${a.name ?? ""}`}
                 style={{ borderRadius: "var(--radius-sm)", margin: "8px 0 4px", maxHeight: 180 }} />
          )}
          {texts.length > 0 ? (
            texts.map((t, i) => (
              <div key={i} style={{ padding: "9px 0", borderBottom: "1px solid var(--line)" }}>
                <div className="eyebrow">{t.field}{t.ordinal ? ` ${t.ordinal}` : ""}</div>
                <div style={{ marginTop: 3 }}>{t.text}</div>
              </div>
            ))
          ) : a.needs_page_scope ? (
            <p className="muted small">This is a boosted page post, so its copy is not readable
              through the ads API.</p>
          ) : (
            <p className="muted small">No copy imported.</p>
          )}
          <p className="small" style={{ margin: "10px 0 0" }}>
            Button <span className="pill quiet">{a.cta ? words(a.cta) : "--"}</span>
            {" "}· <span className="mono">{a.utm_content || "no utm_content"}</span>
            {a.link_url && <><br /><span className="mono">{truncate(a.link_url, 70)}</span></>}
          </p>
        </section>

        <div className="span2" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Where the answer lands, beside the copy it is about. Nothing until
              the button is pressed: an analysis nobody asked for is a model
              call nobody asked for. */}
          <AnalysisPanel id="ad-analysis" ariaLabel="AI read" style={{ margin: 0 }}
                         phase={analysis.phase} text={analysis.text} asked={analysis.asked}
                         foot={<p className="small muted">Written by a Claude Code session
                           from this ad's own numbers. It reports; it decides nothing.</p>} />
          <section className="board" aria-label="Spend per day">
            <header><h2>Spend per day</h2></header>
            <div className="body"><LineChart points={data.spend_series} kind="spend" /></div>
          </section>
          <section className="board" aria-label="Conversions per day">
            <header><h2>Conversions per day</h2></header>
            <div className="body"><LineChart points={data.conv_series} kind="count" /></div>
          </section>
        </div>
      </div>

      {/* Daily. */}
      {ad.daily.length ? (
        <More style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--radius)" }}
              summary={<><strong>Show {ad.day_count} days</strong> — spend, impressions,
                link clicks, conversions, daily frequency and reach that day.</>}>
          <div className="table-wrap" style={{ border: 0 }}>
            <table>
              <thead><tr>
                <th>Day</th><th className="num">Spend</th><th className="num">Impressions</th>
                <th className="num">Link clicks</th><th className="num">Conv</th>
                <th className="num">Daily freq</th><th className="num">Reach that day</th>
              </tr></thead>
              <tbody>
                {ad.daily.map((r) => (
                  <tr key={r.day}>
                    <td>{r.day}</td>
                    <td className="num">{money(r.spend)}</td>
                    <td className="num">{num(r.impressions)}</td>
                    <td className="num">{num(r.link_clicks)}</td>
                    <td className="num">{num(r.conversions)}</td>
                    <td className="num">{num(r.frequency_this_day, 2)}</td>
                    {/* Deduplicated within the day, so adding this column up
                        would count the same person once per day. */}
                    <td className="num">{num(r.reach_this_day)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </More>
      ) : (
        <Empty>No insights rows for this ad.</Empty>
      )}
    </PageFrame>
  );
}
