/* ASK. A question, answered twice: in prose, and in the verb's own JSON.
 * Ported from templates/ask.html.
 *
 * The prose comes from a Claude Code session (chat.py) that runs the read
 * verbs itself. The JSON below it is the router's answer to the same
 * question, and it is the receipt: how a figure in the prose gets checked
 * against the verb that produced it. The page fetches both itself, the way
 * the overview's box does -- /api/ask only carries the verb list -- so the
 * prose half's failure never costs the routed JSON.
 */
import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Icon } from "../components/Icon";
import { PageFrame } from "../components/Layout";
import { Caveat, Empty, usePage } from "../components/ui";
import type { Prose, Routed } from "../components/AskBox";
import { getJson, type PageBase } from "../lib/api";

export interface AskData extends PageBase {
  d: { brand: string };
  verbs: Record<string, string>;
}

export function AskPage() {
  const page = usePage<AskData>("/ask", "overview", "Ask");
  const { search } = useLocation();
  const navigate = useNavigate();
  const q = new URLSearchParams(search).get("q") ?? "";
  const brand = page.data?.d.brand ?? new URLSearchParams(search).get("brand") ?? "";
  const [typed, setTyped] = useState(q);
  const [reply, setReply] = useState<Prose | null>(null);
  const [replyError, setReplyError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<Routed | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setTyped(q);
    setReply(null); setReplyError(null); setAnswer(null);
    if (!q.trim()) return;
    let live = true;
    setBusy(true);
    const qs = `?q=${encodeURIComponent(q)}&brand=${encodeURIComponent(brand)}`;
    const fast = getJson<Routed>(`/ask.json${qs}`)
      .then((r) => { if (live) setAnswer(r); })
      .catch((e: unknown) => { if (live) setAnswer({ error: e instanceof Error ? e.message : String(e) }); });
    const slow = getJson<Prose>(`/ask/prose.json${qs}`)
      .then((p) => { if (!live) return; if (p.ok) setReply(p); else setReplyError(p.error ?? "No answer."); })
      .catch((e: unknown) => { if (live) setReplyError(e instanceof Error ? e.message : String(e)); });
    void Promise.all([fast, slow]).then(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [q, brand]);

  if (page.gate) return page.gate;
  const data = page.data;

  function submit(e: FormEvent) {
    e.preventDefault();
    navigate(`/ask?brand=${encodeURIComponent(brand)}&q=${encodeURIComponent(typed)}`);
  }

  // Only surfaced when the prose half did NOT answer: the router matches a
  // regex against a fixed verb list and fails on most naturally-worded
  // questions, while the session answers them fine. Showing both put a red
  // error directly above a correct answer.
  const failed = answer?.error && !reply ? answer.error : null;

  return (
    <PageFrame nav="overview" title="Ask" brand={brand} settled={null} hideAsk
               sub={<div className="sub">Same routing, same verbs, same output as the box on every page —
                 here the answer renders as a page you can link to.</div>}>
      <div className="grid">
        <section className="card span2">
          <form className="ask-form" onSubmit={submit} style={{ marginBottom: 14 }}>
            <Icon name="ask" />
            <input name="q" type="text" value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)}
                   placeholder="Ask about spend, CPA, fatigue, angles…" />
            <button type="submit" aria-label="Ask" disabled={busy}><Icon name="arrow-up" /></button>
          </form>

          {failed && <div className="flash error">{failed}</div>}
          {busy && <div className="n ask-thinking" style={{ marginBottom: 12 }}>Thinking… the written answer takes about a minute.</div>}

          {reply ? (
            <>
              <div className="card-head">
                <h2 className="card-t">Answer</h2>
                {reply.turns ? <span className="more">{reply.turns} step(s)</span> : null}
              </div>
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55, marginBottom: 6 }}>{reply.answer}</div>
              {reply.cost_usd ? <div className="n" style={{ marginBottom: 14 }}>${reply.cost_usd.toFixed(2)}</div> : null}
            </>
          ) : replyError ? <Caveat>{replyError}</Caveat> : null}

          {answer && !answer.error ? (
            <>
              <div className="card-head">
                <h2 className="card-t">{answer.verb}</h2>
                <span className="more">{answer.says}</span>
              </div>
              <div className="n" style={{ marginBottom: 8 }}>
                {answer.args && Object.keys(answer.args as object).length
                  ? `called with ${JSON.stringify(answer.args)}` : "called with its defaults"}
              </div>
              {/* The verb's own JSON, not a sentence about it. */}
              <pre className="mono small" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word",
                   background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--radius-sm)",
                   padding: 12, margin: 0, maxHeight: 520, overflow: "auto" }}>
                {JSON.stringify(answer.result, null, 2)}
              </pre>
            </>
          ) : !q.trim() ? <Empty>Ask something, or pick one from the list beside this.</Empty> : null}
        </section>

        <section className="card">
          <div className="card-head"><h2 className="card-t">What it can answer</h2></div>
          <div className="n" style={{ marginBottom: 10 }}>A fixed list. A question that matches none of these is
            refused rather than guessed at, and there is no free-text path to the database.</div>
          <table>
            <tbody>
              {Object.entries(data.verbs).map(([verb, says]) => (
                <tr key={verb}>
                  <td style={{ paddingLeft: 0 }}>
                    <Link className="mono" to={`/ask?brand=${encodeURIComponent(brand)}&q=${encodeURIComponent(verb)}`}>{verb}</Link>
                  </td>
                  <td className="small muted" style={{ paddingRight: 0 }}>{says}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="n" style={{ marginTop: 12 }}>
            <code>record</code> and <code>live</code> are deliberately absent: one writes, and the other
            spends a rate limit.
          </div>
        </section>
      </div>
    </PageFrame>
  );
}
