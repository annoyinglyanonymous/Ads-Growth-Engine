/* Two fetches, one bubble. The JSON is the receipt, never the answer. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AskBox } from "./AskBox";

const routed = { verb: "why", says: "the CPA bridge", result: { cpa_change: 1.5 } };

function answers(prose: Response | Error, fast: object | Error = routed) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.startsWith("/ask/prose.json")) {
      if (prose instanceof Error) throw prose;
      return prose;
    }
    if (fast instanceof Error) throw fast;
    return new Response(JSON.stringify(fast), { status: 200 });
  }));
}

const ok = (body: object, status = 200) => new Response(JSON.stringify(body), { status });

afterEach(() => vi.unstubAllGlobals());

async function ask(text: string) {
  render(<AskBox brand="renegade" />);
  fireEvent.change(screen.getByLabelText("Ask a question"), { target: { value: text } });
  fireEvent.submit(screen.getByLabelText("Ask a question").closest("form")!);
  await waitFor(() => expect(screen.queryByText("Thinking…")).toBeNull());
}

it("shows the prose and folds the routed JSON under it as the receipt", async () => {
  answers(ok({ ok: true, answer: "CPA rose because fewer ads converted.", cost_usd: 0.42 }));
  await ask("why did CPA move");
  expect(screen.getByText("CPA rose because fewer ads converted.")).toHaveClass("ask-prose");
  expect(screen.getByText("$0.42")).toBeInTheDocument();
  expect(screen.getByText("the why output this came from")).toBeInTheDocument();
  expect(document.querySelector("details")).not.toHaveAttribute("open");
});

it("keeps the routed answer when the session fails, and says why the prose is missing", async () => {
  answers(ok({ ok: false, error: "claude is not installed" }));
  await ask("why did CPA move");
  expect(screen.getByText("why")).toBeInTheDocument();
  expect(screen.getByText("claude is not installed")).toBeInTheDocument();
});

it("names a stale server on a 404 instead of redrawing the JSON", async () => {
  answers(new Response("", { status: 404 }));
  await ask("why did CPA move");
  expect(screen.getByText(/has no \/ask\/prose\.json/)).toBeInTheDocument();
});

it("reports a question nothing could answer", async () => {
  answers(ok({ ok: false, error: "no answer" }), { error: "No verb matches." });
  await ask("tell me a joke");
  expect(screen.getByText("no answer")).toBeInTheDocument();
  expect(screen.queryByText("No verb matches.")).toBeNull();
});

it("echoes the question as the reader's own bubble", async () => {
  answers(ok({ ok: true, answer: "Steady." }));
  await ask("status");
  expect(document.querySelector(".ask-row.me .ask-bubble")?.textContent).toBe("status");
});
