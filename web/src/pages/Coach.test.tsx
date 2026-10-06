import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Coach from "./Coach";
import { resetCsrfForTests } from "@/lib/api";

const REF = "h_0123456789";
const status = { configured: true, backend: "claude-code", model: "sonnet", message: "", max_tool_calls: 12, timeout_seconds: 180, busy: false, current_job: null, tools: [] };

function sse(events: [string, unknown][]) {
  const enc = new TextEncoder();
  const chunks = events.map(([e, d]) => enc.encode(`event: ${e}\ndata: ${JSON.stringify(d)}\n\n`));
  return new Response(new ReadableStream({ start(c) { chunks.forEach((x) => c.enqueue(x)); c.close(); } }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
}
let calls: { url: string; init?: RequestInit }[];
let stream: () => Response;

beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  stream = () => sse([["meta", { job_id: "j_1", configured: true }], ["done", { finish_reason: "stop" }]]);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url === "/api/v1/coach/stream") return stream();
    let body: unknown = {};
    if (url.endsWith("/session")) body = { csrf_token: "tok" };
    else if (url.endsWith("/coach/status")) body = status;
    else if (url.endsWith("/coach/prompts")) body = { prompts: [{ id: "a", text: "Why was last month's spending so high?" }, { id: "monthly-review", text: "Review last month", skill: "monthly-review" }] };
    else if (url.endsWith("/coach/resolve")) body = { refs: { [REF]: { kind: "transaction", tx_key: "real-key-1", date: "2026-09-27", amount: "-27.00", category: "food.groceries", merchant: "Acme Grocers" } } };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("ask the coach", () => {
  it("streams tool calls and the answer, turns evidence refs into links, shows usage and the unverified-number warning", async () => {
    stream = () => sse([
      ["meta", { job_id: "j_1", configured: true, backend: "claude-code", model: "sonnet" }],
      ["status", { state: "running" }],
      ["tool_call", { id: "t1", name: "coverage", args: "", n: 1, max: 12 }],
      ["tool_result", { id: "t1", name: "coverage", ok: true, chars: 10, suspicious: false }],
      ["tool_call", { id: "t2", name: "transactions_search", args: "{\"limit\":3}", n: 2, max: 12 }],
      ["tool_result", { id: "t2", name: "transactions_search", ok: true, chars: 99, suspicious: false }],
      ["delta", { text: "Groceries were **27.00 EUR** " }],
      ["delta", { text: `on one day (${REF}).\n\n- You could save 999.99 EUR` }],
      ["usage", { backend: "claude-code", model: "sonnet", tokens_in: 1200, tokens_out: 150, cache_read_tokens: 0, cost_usd: 0.0123, cost_is_estimate: true, tool_calls: 2, duration_s: 4.2 }],
      ["answer", { insight_id: "cin_1", suspicious: false, proposals: [], unverified_numbers: ["999.99"], finish_reason: "stop" }],
      ["done", { finish_reason: "stop", job_id: "j_1" }],
    ]);
    renderApp(<Coach />);
    expect(await screen.findByText(/up to 12 look-ups/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Your question"), "Why was September high?");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));
    const answer = await screen.findByTestId("coach-answer");
    await waitFor(() => expect(answer).toHaveTextContent(/Groceries were 27.00 EUR/));
    expect(screen.getByTestId("coach-tools")).toHaveTextContent("data coverage");
    expect(screen.getByTestId("coach-tools")).toHaveTextContent("transactions");
    // the ref is a link to the real transaction, resolved on the server (the hash itself is not shown)
    await waitFor(() => expect(screen.getAllByRole("link").some((a) => a.getAttribute("href") === "/transactions?tx=real-key-1")).toBe(true));
    expect(answer).not.toHaveTextContent(REF);
    expect(screen.getByTestId("unverified")).toHaveTextContent("1 number in this answer could not be traced");
    expect(screen.getByTestId("unverified")).toHaveTextContent("999.99");
    expect(screen.getByTestId("coach-usage")).toHaveTextContent("1,200 tokens in");
    const post = calls.find((c) => c.url === "/api/v1/coach/stream")!;
    expect(JSON.parse(post.init!.body as string)).toEqual({ question: "Why was September high?" });
    expect((post.init!.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
    const resolve = calls.find((c) => c.url === "/api/v1/coach/resolve")!;
    expect(JSON.parse(resolve.init!.body as string)).toEqual({ refs: [REF] });
  });

  it("shows a proposal with the command to run in a terminal, never applying anything", async () => {
    stream = () => sse([
      ["meta", { job_id: "j_2" }],
      ["tool_call", { id: "t1", name: "memory_propose", args: "", n: 1, max: 12 }],
      ["proposal", { id: "p-20261004-abc123", file: "assets.yaml", command: "uv run coach memory accept p-20261004-abc123" }],
      ["delta", { text: "I made proposal p-20261004-abc123." }],
      ["done", { finish_reason: "stop" }],
    ]);
    renderApp(<Coach />);
    await userEvent.type(await screen.findByLabelText("Your question"), "The house is worth 410000");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));
    expect(await screen.findByText(/nothing is changed yet/i)).toBeInTheDocument();
    expect(screen.getByText("uv run coach memory accept p-20261004-abc123")).toBeInTheDocument();
    expect(calls.every((c) => !c.url.includes("/memory") && !c.url.includes("/proposals"))).toBe(true);
  });

  it("flags suspicious text found in the data", async () => {
    stream = () => sse([
      ["meta", { job_id: "j_3" }],
      ["notice", { code: "suspicious", message: "Text in your data looks like an instruction" }],
      ["delta", { text: "Done." }],
      ["answer", { insight_id: "cin_2", suspicious: true, proposals: [], unverified_numbers: [], finish_reason: "stop" }],
      ["done", { finish_reason: "stop" }],
    ]);
    renderApp(<Coach />);
    await userEvent.click(await screen.findByRole("button", { name: /why was last month/i }));
    expect(await screen.findByText("Suspicious text in your data")).toBeInTheDocument();
    expect(screen.getByText(/needs a separate confirmation of each field/)).toBeInTheDocument();
  });

  it("an error event becomes a message and the input is usable again", async () => {
    stream = () => sse([["meta", { job_id: "j_4" }], ["error", { code: "coach_unavailable", message: "model has no tool support" }], ["done", { finish_reason: "unavailable" }]]);
    renderApp(<Coach />);
    await userEvent.type(await screen.findByLabelText("Your question"), "hello");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));
    expect(await screen.findByText("model has no tool support")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Your question")).toBeEnabled());
  });

  it("while an answer is streaming the Ask button becomes Cancel, which cancels the job server-side", async () => {
    let release: () => void = () => undefined;
    stream = () => {
      const enc = new TextEncoder();
      return new Response(new ReadableStream({ start(c) {
        c.enqueue(enc.encode(`event: meta\ndata: ${JSON.stringify({ job_id: "j_9" })}\n\n`));
        c.enqueue(enc.encode(`event: delta\ndata: ${JSON.stringify({ text: "Working" })}\n\n`));
        release = () => { c.enqueue(enc.encode(`event: notice\ndata: ${JSON.stringify({ code: "cancelled", message: "Cancelled." })}\n\nevent: done\ndata: {}\n\n`)); c.close(); };
      } }), { status: 200 });
    };
    renderApp(<Coach />);
    await userEvent.type(await screen.findByLabelText("Your question"), "slow");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));
    const cancel = await screen.findByRole("button", { name: /cancel/i });
    await screen.findByText(/Working/);
    await userEvent.click(cancel);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/v1/coach/jobs/j_9/cancel")).toBe(true));
    release();
    expect(await screen.findByText("Cancelled.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /ask/i })).toBeInTheDocument());
  });

  it("explains when the coach is not available and keeps Ask disabled", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      const body = url.endsWith("/coach/status") ? { ...status, configured: false, message: "The claude command is not installed" } : url.endsWith("/session") ? { csrf_token: "t" } : { prompts: [] };
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    renderApp(<Coach />);
    expect(await screen.findByText("The claude command is not installed")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Your question"), "x");
    expect(screen.getByRole("button", { name: /ask/i })).toBeDisabled();
  });

  it("a skill quick prompt sends its skill id with the text, a plain prompt sends the question only", async () => {
    renderApp(<Coach />);
    await userEvent.click(await screen.findByRole("button", { name: "Review last month" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/v1/coach/stream")).toBe(true));
    const post = calls.find((c) => c.url === "/api/v1/coach/stream")!;
    expect(JSON.parse(post.init!.body as string)).toEqual({ question: "Review last month", skill: "monthly-review" });
  });
});

describe("E11-5: AI label and investment-advice banner", () => {
  it("labels every answer as AI-generated and shows the banner only when the check flagged it", async () => {
    stream = () => sse([
      ["meta", { job_id: "j_9" }],
      ["delta", { text: "Vous devriez placer cet argent dans un ETF." }],
      ["answer", { insight_id: "cin_9", suspicious: false, proposals: [], unverified_numbers: [], finish_reason: "stop", ai_generated: true,
        compliance: { label: "Contenu généré par une IA : il peut contenir des erreurs.", label_short: "Généré par IA", lang: "fr", flagged: true, codes: ["recommendation"], banner: "Information générale uniquement, pas un conseil en investissement personnalisé (AMF / CIF)." } }],
      ["done", { finish_reason: "stop", job_id: "j_9" }],
    ]);
    renderApp(<Coach />);
    await userEvent.type(await screen.findByLabelText("Your question"), "Où placer mon épargne ?");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));
    expect(await screen.findByTestId("compliance-banner")).toHaveTextContent(/AMF \/ CIF/);
    expect(screen.getByTestId("ai-label")).toHaveTextContent("Contenu généré par une IA");
  });

  it("an unflagged answer has the label and no banner", async () => {
    stream = () => sse([
      ["meta", { job_id: "j_10" }],
      ["delta", { text: "Groceries rose." }],
      ["answer", { insight_id: "cin_10", suspicious: false, proposals: [], unverified_numbers: [], finish_reason: "stop", ai_generated: true,
        compliance: { label: "AI-generated content: it can contain mistakes. Check the figures against your accounts.", label_short: "AI-generated", lang: "en", flagged: false, codes: [], banner: "" } }],
      ["done", { finish_reason: "stop", job_id: "j_10" }],
    ]);
    renderApp(<Coach />);
    await userEvent.type(await screen.findByLabelText("Your question"), "Why?");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));
    expect(await screen.findByTestId("ai-label")).toHaveTextContent("AI-generated content");
    expect(screen.queryByTestId("compliance-banner")).toBeNull();
  });
});
