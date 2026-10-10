import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { Login } from "./Login";
import { ApiError, resetExchangeForTests } from "@/lib/api";

let calls: { url: string; init?: RequestInit }[];
let exchangeStatus = 200;
let methods = { passkeys: false, sso: false, sso_sign_out: null as string | null };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
beforeEach(() => {
  resetExchangeForTests();
  calls = [];
  exchangeStatus = 200;
  methods = { passkeys: false, sso: false, sso_sign_out: null };
  window.history.replaceState(null, "", "/login");
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url === "/api/v1/session/methods") return json(methods);
    if (url === "/api/v1/session/passkey/options") return json({ challenge_id: "c1", options: { challenge: "AQID", rpId: "localhost" } });
    if (url === "/api/v1/session/passkey/verify") return json({ ok: true });
    const ok = exchangeStatus === 200;
    return json(ok ? { ok: true } : { error: { code: "invalid_login_token", message: "this login link is invalid, already used or expired" } }, exchangeStatus);
  }));
});
afterEach(() => vi.unstubAllGlobals());

const posts = () => calls.filter((c) => c.init?.method === "POST");

describe("login screen", () => {
  it("without a link it only explains how to get one and sends nothing but the question of what is possible", async () => {
    render(<Login onDone={() => undefined} />);
    expect(screen.getByRole("heading", { name: /open the login link from your terminal/i })).toBeInTheDocument();
    expect(screen.getByText("uv run coach ui --login-link")).toBeInTheDocument();
    await waitFor(() => expect(calls.map((c) => c.url)).toEqual(["/api/v1/session/methods"]));
    expect(posts()).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /passkey/i })).not.toBeInTheDocument();
  });

  it("trades the token from the URL fragment exactly once (even under StrictMode) and wipes it from the address bar", async () => {
    window.history.replaceState(null, "", "/login#t=tok-123");
    const done = vi.fn();
    render(<StrictMode><Login onDone={done} /></StrictMode>);
    await waitFor(() => expect(done).toHaveBeenCalled());
    const exchanges = calls.filter((c) => c.url === "/api/v1/session/exchange");
    expect(exchanges).toHaveLength(1);
    expect(JSON.parse(exchanges[0].init!.body as string)).toEqual({ token: "tok-123" });
    expect(exchanges[0].url).not.toContain("tok-123"); // never in a URL that is sent anywhere
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/");
  });

  it("says so when the link is used up or expired, and removes the token anyway", async () => {
    exchangeStatus = 401;
    window.history.replaceState(null, "", "/login#t=old");
    const done = vi.fn();
    render(<Login onDone={done} />);
    expect(await screen.findByText(/did not work/i)).toBeInTheDocument();
    expect(screen.getByText(/already used or expired/i)).toBeInTheDocument();
    expect(done).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("");
  });

  it("offers a passkey when the server enables them and the browser can, and signs in with it (E16)", async () => {
    methods = { passkeys: true, sso: false, sso_sign_out: null };
    const get = vi.fn(async () => ({
      id: "AQID", rawId: new Uint8Array([1, 2, 3]).buffer, type: "public-key", getClientExtensionResults: () => ({}),
      response: { clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer, signature: new Uint8Array([3]).buffer, userHandle: null },
    }));
    vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() { /* presence is what is checked */ });
    Object.defineProperty(navigator, "credentials", { value: { get, create: async () => null }, configurable: true });
    const done = vi.fn();
    render(<Login onDone={done} />);
    const button = await screen.findByRole("button", { name: /use my passkey/i });
    fireEvent.click(button);
    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(get).toHaveBeenCalledTimes(1);
    expect(posts().map((c) => c.url)).toEqual(["/api/v1/session/passkey/options", "/api/v1/session/passkey/verify"]);
    const sent = JSON.parse(posts()[1].init!.body as string);
    expect(sent.challenge_id).toBe("c1");
    expect(sent.credential.response).toEqual({ clientDataJSON: "AQ", authenticatorData: "Ag", signature: "Aw" });
  });

  it("behind an SSO proxy it shows which identity is not mapped, and keeps the link as the fallback (E16)", async () => {
    methods = { passkeys: false, sso: true, sso_sign_out: "/outpost.goauthentik.io/sign_out" };
    const reason = new ApiError(401, "sso_unmapped", "signed in as 'zoe', which is mapped to no login", { identity: "zoe" });
    render(<Login onDone={() => undefined} reason={reason} />);
    expect(await screen.findByText(/signed in through your identity provider/i)).toBeInTheDocument();
    expect(screen.getByText("zoe")).toBeInTheDocument();
    expect(screen.getByText(/mapped to no login/i)).toBeInTheDocument();
    expect(screen.getByText("uv run coach ui --login-link")).toBeInTheDocument();
    expect(posts()).toHaveLength(0);
  });
});
