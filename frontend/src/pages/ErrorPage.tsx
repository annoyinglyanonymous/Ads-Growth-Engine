/* One sentence on the page, never a traceback. Ported from error.html.
 *
 * ui._fail answers 200 with `error` set, because a NotConfigured almost
 * always means ADS_DATABASE_URL_RO is unset -- a state of the install rather
 * than a fault -- so the page explains itself. A failed fetch (`failed`)
 * lands here too, with its own sentence.
 */
import { PageFrame } from "../components/Layout";

export function ErrorPage({ nav, error, detail }: { nav: string; error: string; detail?: string | null }) {
  return (
    <PageFrame nav={nav} title="This page cannot answer yet" brand={null} settled={null}>
      <div className="card">
        <p><strong>{error}</strong></p>
        {detail && detail !== error && (
          <pre className="mono small muted" style={{ whiteSpace: "pre-wrap", margin: "10px 0 0" }}>{detail}</pre>
        )}
        <hr className="sep" />
        <p className="small muted">The usual causes, in the order they happen:</p>
        <ul className="small muted">
          <li><code>ADS_DATABASE_URL_RO</code> is unset — see <code>.env.example</code>.</li>
          <li>The migrations have not been applied. <code>python ads_migrate.py --status</code></li>
          <li>The RLS policies in <code>006_ads_roles.sql</code> are missing, in which
              case queries succeed and return nothing rather than failing.</li>
          <li>Nothing has been imported yet:
              <code>python -m meta_ads --pull --brand renegade</code> in growth-engine.</li>
        </ul>
      </div>
    </PageFrame>
  );
}

/** The page while its data is on the way. Same frame, so the rail does not
 *  jump when the numbers land. */
export function Loading({ nav, title }: { nav: string; title: string }) {
  return (
    <PageFrame nav={nav} title={title}>
      <div className="empty small">Loading…</div>
    </PageFrame>
  );
}
