/* The "ask a model" button and the panel its answer lands in.
 *
 * Ported from the last block of static/app.js. One POST, one Claude Code
 * session, the better part of a minute. The button says so while it waits
 * rather than spinning silently: a page that looks frozen gets clicked again,
 * and every click is another session.
 *
 * POST, not GET, and not on page load. The answer is work with a cost, so it
 * happens when somebody asks for it. ui.py's endpoints make the same argument.
 *
 * /ad/<key> and /brief both use it; a third page passes a URL and a label.
 */
import { useState, type CSSProperties, type ReactNode } from "react";
import { staleServer } from "../lib/api";

interface Reply { ok: boolean; answer?: string; error?: string; cost_usd?: number }

type Phase = "idle" | "busy" | "done" | "failed";

export function useAnalysis(url: string) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [text, setText] = useState<string>("");
  const [asked, setAsked] = useState(false);

  async function ask() {
    if (phase === "busy") return;
    setPhase("busy");
    setAsked(true);
    setText("Reading the numbers and writing it up…");
    try {
      const r = await fetch(url, { method: "POST", headers: { Accept: "application/json" } });
      if (r.status === 404) throw new Error(staleServer(url.split("?")[0]));
      const d = (await r.json()) as Reply;
      if (!d.ok || !d.answer) throw new Error(d.error || "The session returned nothing.");
      setText(d.answer);
      setPhase("done");
    } catch (e) {
      setText(e instanceof Error ? e.message : "Could not reach the server.");
      setPhase("failed");
    }
  }

  return { phase, text, asked, ask };
}

/** The button. `label` is the idle text ("Explain this brief", "Ask about this
 *  ad"); after one answer it offers the same thing again. */
export function AskButton({ label, phase, onClick, className = "btn" }: {
  label: string; phase: Phase; onClick: () => void; className?: string;
}) {
  const again = label.replace(/^Ask about|^Explain/, (m) => (m === "Explain" ? "Explain again:" : "Ask again about"))
    .replace("Explain again: this brief", "Explain again");
  const text = phase === "busy" ? "Reading…" : phase === "idle" ? label : again;
  return (
    <button type="button" className={className} onClick={onClick} disabled={phase === "busy"}>{text}</button>
  );
}

/** Where the answer lands: plain paragraphs. chat.SYSTEM tells the session to
 *  write sentences with no markdown, so anything arriving with asterisks in it
 *  is a prompt that drifted -- rendering it as text keeps that visible. */
export function AnalysisPanel({ title, phase, text, asked, foot, pill = "AI read", id, ariaLabel, style }: {
  title?: ReactNode; phase: Phase; text: string; asked: boolean; foot?: ReactNode; pill?: string;
  id?: string; ariaLabel?: string; style?: CSSProperties;
}) {
  if (!asked) return null;
  const state = phase === "busy" ? "this takes about a minute" : phase === "failed" ? "failed" : "";
  return (
    <section className="ai-read" aria-live="polite" id={id} aria-label={ariaLabel} style={style}>
      <div className="ai-read-head">
        <span className="pill stop">{pill}</span>
        {title}
        <span className="state">{state}</span>
      </div>
      <div>
        {String(text).split(/\n{2,}/).map((para, i) => {
          const t = para.trim();
          return t ? (
            <p key={i} className={phase === "failed" ? "n err" : undefined}
               style={{ margin: "0 0 10px", maxWidth: "80ch" }}>{t}</p>
          ) : null;
        })}
      </div>
      {phase === "done" && foot}
    </section>
  );
}
