import { describe, expect, it } from "vitest";
import { b64uToBytes, bytesToB64u, serializeCredential, toCreationOptions, toRequestOptions } from "./webauthn";

describe("base64url", () => {
  it("round-trips bytes without padding", () => {
    const bytes = new Uint8Array([0, 1, 2, 250, 251, 252, 253, 254, 255]);
    const s = bytesToB64u(bytes);
    expect(s).not.toMatch(/[+/=]/);
    expect(Array.from(b64uToBytes(s))).toEqual(Array.from(bytes));
    expect(Array.from(b64uToBytes("AQID"))).toEqual([1, 2, 3]);
  });
});

describe("the server's JSON options", () => {
  it("become bytes where the browser wants bytes", () => {
    const c = toCreationOptions({ challenge: "AQID", rp: { id: "localhost", name: "x" }, user: { id: "b3duZXI", name: "owner", displayName: "owner" },
      pubKeyCredParams: [{ type: "public-key", alg: -7 }], excludeCredentials: [{ id: "AQID", type: "public-key", transports: ["internal"] }] });
    expect(c.challenge).toBeInstanceOf(Uint8Array);
    expect(new TextDecoder().decode(c.user.id as Uint8Array)).toBe("owner");
    expect(c.excludeCredentials?.[0].transports).toEqual(["internal"]);
    const r = toRequestOptions({ challenge: "AQID", rpId: "localhost" });
    expect(Array.from(r.challenge as Uint8Array)).toEqual([1, 2, 3]);
    expect(r.allowCredentials).toBeUndefined();
  });
});

describe("the browser's credential", () => {
  it("is serialised as base64url JSON for the server", () => {
    const raw = new Uint8Array([9, 8, 7]).buffer;
    const cred = {
      id: "CQgH", rawId: raw, type: "public-key", authenticatorAttachment: "platform",
      getClientExtensionResults: () => ({}),
      response: { clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer, signature: new Uint8Array([3]).buffer, userHandle: null },
    } as unknown as PublicKeyCredential;
    const j = serializeCredential(cred) as any;
    expect(j.rawId).toBe("CQgH");
    expect(j.response).toEqual({ clientDataJSON: "AQ", authenticatorData: "Ag", signature: "Aw" });
    const made = { ...cred, response: { clientDataJSON: new Uint8Array([1]).buffer, attestationObject: new Uint8Array([4]).buffer, getTransports: () => ["internal"] } } as unknown as PublicKeyCredential;
    expect((serializeCredential(made) as any).response).toEqual({ clientDataJSON: "AQ", attestationObject: "BA", transports: ["internal"] });
  });
});
