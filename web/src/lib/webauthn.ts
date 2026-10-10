// Passkeys (E16): the browser's WebAuthn calls, with the base64url <-> bytes conversions the JSON options and answers need.
// The server (py_webauthn) makes the options and verifies the answers; this file only moves bytes in and out of the browser API.
import { api, publicPost } from "./api";

export function b64uToBytes(s: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + pad);
  const out = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function bytesToB64u(b: ArrayBuffer | Uint8Array): string {
  const bytes = b instanceof Uint8Array ? b : new Uint8Array(b);
  let bin = "";
  for (const x of bytes) bin += String.fromCharCode(x);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The browser can make and use passkeys (and the page is a secure context: https, or localhost). */
export function passkeysSupported(): boolean {
  return typeof window !== "undefined" && "PublicKeyCredential" in window && typeof navigator.credentials?.create === "function";
}

type Descriptor = { id: string; type: string; transports?: string[] };

function descriptors(list: Descriptor[] | undefined): PublicKeyCredentialDescriptor[] | undefined {
  return list?.map((d) => ({ id: b64uToBytes(d.id), type: "public-key", transports: d.transports as AuthenticatorTransport[] | undefined }));
}

/** The creation options the server sent (JSON, base64url) as the browser wants them (bytes). */
export function toCreationOptions(o: any): PublicKeyCredentialCreationOptions {
  return {
    ...o,
    challenge: b64uToBytes(o.challenge),
    user: { ...o.user, id: b64uToBytes(o.user.id) },
    excludeCredentials: descriptors(o.excludeCredentials),
  };
}

export function toRequestOptions(o: any): PublicKeyCredentialRequestOptions {
  return { ...o, challenge: b64uToBytes(o.challenge), allowCredentials: descriptors(o.allowCredentials) };
}

/** The credential the browser returned, as the JSON the server verifies. */
export function serializeCredential(c: PublicKeyCredential): Record<string, unknown> {
  const r = c.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse;
  const response: Record<string, unknown> = { clientDataJSON: bytesToB64u(r.clientDataJSON) };
  if ("attestationObject" in r && r.attestationObject) {
    response.attestationObject = bytesToB64u(r.attestationObject);
    response.transports = typeof r.getTransports === "function" ? r.getTransports() : [];
  }
  if ("authenticatorData" in r && r.authenticatorData) {
    response.authenticatorData = bytesToB64u(r.authenticatorData);
    response.signature = bytesToB64u(r.signature);
    if (r.userHandle) response.userHandle = bytesToB64u(r.userHandle);
  }
  return {
    id: c.id,
    rawId: bytesToB64u(c.rawId),
    type: c.type,
    authenticatorAttachment: (c as PublicKeyCredential & { authenticatorAttachment?: string }).authenticatorAttachment ?? undefined,
    clientExtensionResults: c.getClientExtensionResults(),
    response,
  };
}

type Challenge = { challenge_id: string; options: any };

/** "Sign in with a passkey": the server's challenge, the browser's prompt, the assertion back. Resolves once the cookie is set. */
export async function signInWithPasskey(): Promise<void> {
  const ch = await publicPost<Challenge>("/session/passkey/options", {});
  const cred = (await navigator.credentials.get({ publicKey: toRequestOptions(ch.options) })) as PublicKeyCredential | null;
  if (!cred) throw new Error("cancelled");
  await publicPost("/session/passkey/verify", { challenge_id: ch.challenge_id, credential: serializeCredential(cred) });
}

/** Enrol a passkey for the login of this session. */
export async function enrolPasskey(label: string): Promise<{ id: string; label: string }> {
  const ch = await api.post<Challenge>("/session/passkeys/options", {});
  const cred = (await navigator.credentials.create({ publicKey: toCreationOptions(ch.options) })) as PublicKeyCredential | null;
  if (!cred) throw new Error("cancelled");
  return api.post<{ id: string; label: string }>("/session/passkeys", { challenge_id: ch.challenge_id, credential: serializeCredential(cred), label });
}
