# Enable Banking: who they are, what they see, how this app uses them

The bank sync of this project goes through [Enable Banking](https://enablebanking.com), a Finnish open-banking aggregator. Before you give a
third party read access to your accounts you deserve to know who they are and what they can do; this page is what the maintainer found on
2026-10-09 (every source is dated: anything older than a year may have changed, and the company's own pages are the authority). It is not legal
advice and it does not replace reading their terms yourself.

## 1. Who they are, and under which rules

* **Enable Banking Oy**, business id 2988499-7, Espoo, Finland, founded in 2019, about fifteen people, B2B customers (accounting, credit scoring,
  payment providers). Sources: the [LEI record](https://lei.bloomberg.com/leis/view/743700U9ROC6WAR6F068) and the company registry (accessed 2026-10-09);
  "Finland's fifth largest fintech" on [their blog, 2024-11-27](https://enablebanking.com/blog/2024/11/27/enable-banking-named-finlands-5th-largest-fintech).
* **A registered AISP** (Account Information Service Provider) under PSD2, supervised by the Finnish Financial Supervisory Authority
  (Finanssivalvonta). Their [terms](https://enablebanking.com/terms/) (last updated 2026-01-09) say the production API "relies on Enable Banking acting
  as an authorised AISP" and point to the EBA register; the [Open Banking UK directory](https://www.openbanking.org.uk/regulated-providers/enable-banking-oy/)
  lists it as registered in Finland. **Check it yourself once** in the [EBA register](https://euclid.eba.europa.eu/register/) ("Enable Banking Oy"): the
  maintainer could not fetch the register entry itself, so the registration number and the exact list of passported countries (France, Italy) are not
  confirmed here from a primary source. Their market pages for [France](https://enablebanking.com/docs/markets/fr/) and
  [Italy](https://enablebanking.com/docs/markets/it/) list the major banks.
* **Read only.** An AISP registration allows reading account information, not moving money. Their API has payment endpoints, but production payment
  initiation needs a PISP licence ([API reference](https://enablebanking.com/docs/api/reference/)), which a personal application does not have.
  This app calls no payment endpoint at all (section 4).
* **Two ways to use them** ([FAQ](https://enablebanking.com/docs/faq/)): a licensed company can bring its own eIDAS certificate and use Enable
  Banking as a technical provider (the company is then the data controller); a private person uses Enable Banking's own registration. **You are in
  the second case**: the bank sees Enable Banking as the regulated party, Enable Banking is the data controller for the account data it relays, and
  your relationship with them is their terms and privacy notice, not a data-processing agreement.

## 2. What they see and keep

* They say they are a **pass-through**: the FAQ states they do not store, cache or process account data for any purpose other than delivering it
  to the authorised application (which is why transaction matching must rely on the bank's `entry_reference`), and that account identifiers are
  stored only as hashes. What they do keep: the session metadata (which bank, which accounts, valid until, status), a request log per application
  in their control panel, and the consent state until it expires or is revoked.
* Their [privacy notice](https://enablebanking.com/privacy) (last updated 2025-03-17 according to search results) could not be read in full by the
  maintainer: **read it yourself** for the end-user section, the server location and the sub-processors. Server location, retention periods and a
  GDPR data-processing agreement are **not published** on the pages the maintainer could reach.
* Their terms contain no clause granting them a right to use, analyse or share account data (good), cap their liability at EUR 100 and disclaim
  liability for data loss.
* **Personal use is free** ([terms](https://enablebanking.com/terms/), 2026-01-09): production use "is limited to Linked Accounts and is available
  solely for evaluation purposes or for the personal use of private individuals". A linked account is one of your own accounts, linked after a
  strong authentication in their control panel; unlinked accounts are stripped from the answers; the allowance is "limited, revocable" and
  "creates no permanent right". Re-read that clause once a year.

## 3. What was not found

* No published ISO 27001 or SOC 2 certification, no public status page, no public incident history (searches found none; absence of evidence is
  not evidence of absence).
* No independent security review. Compared with larger aggregators (Tink, Plaid) they publish less; compared with them they are, as of 2026,
  the only aggregator with a documented, free, personal-use production path for a self-hosted tool: GoCardless Bank Account Data (ex-Nordigen)
  closed its free tier in 2025, Tink, Salt Edge, Powens and Plaid Europe have no personal production tier, and direct bank APIs need your own AISP
  registration and an eIDAS certificate. The zero-third-party alternative is the **file import** of this app (CSV / OFX / CAMT exports of your bank).

## 4. How this app uses them (what the code does)

Verified in the source on 2026-10-09 (`src/coach/ingest/`):

| What | How |
|---|---|
| Endpoints called | `GET /application`, `GET /aspsps`, `POST /auth`, `POST /sessions`, `GET /sessions/{id}`, `GET /accounts/{id}/transactions`, `GET /accounts/{id}/balances`, and `DELETE /sessions/{id}` from `coach wipe`. No payment endpoint, no `/accounts/{id}/details` (the account holder's name is never fetched). |
| Authentication | a JWT signed with **your** RSA private key (RS256, one hour), generated by you; Enable Banking only ever holds the certificate. The key never leaves the machine. |
| Transport | TLS with certificate verification, always; there is no switch to turn it off (`--insecure` of the CLI is about an unencrypted local database, not TLS). |
| Where the bank login lands | the redirect URL must be a loopback address (`https://localhost:8443/callback` by default); the callback server listens on loopback only, over a self-signed certificate, checks a one-time `state`, and never logs the URL. |
| Consent length | `coach connect --days` (default 180, the PSD2 maximum most banks apply; some banks still allow 90). A consent expires on its own; `coach reconnect` renews it. |
| What is stored, where | session ids, bank name and country, account ids, IBANs, account labels, balances and transactions, all in the SQLCipher-encrypted database; the private key as a 0600 file. |
| Revocation | `coach wipe` asks separately and calls `DELETE /sessions/{id}`; per-bank revocation is done in your bank's consent page or in the Enable Banking control panel (there is no `coach disconnect` yet). |
| Egress | every call goes through the egress gate (`coach privacy status`, `coach privacy report`): the journal keeps the host, the size and the purpose, never the path or a payload. `[privacy] offline = true` disables the sync. |

Two caveats of the current code, listed so that you can decide:

* The **private key is a file**, not a Keychain item (the scheduled job needs it on disk). `coach setup enablebanking` stores it under the coach
  home (`enablebanking/eb-private-key.pem`, folder 0700, file 0600). In a git checkout that home is the checkout itself: the file is git-ignored and
  `coach security audit` warns when a key sits inside the project folder, but the safer layout is the Docker one (`coach-home/` outside the
  repository) or a `COACH_HOME` outside the checkout.
* A **second tool using the same Enable Banking application** (another MCP server, a script) can invalidate this app's consents: keep one consent
  owner per bank.

## 5. What to do, as the owner

1. Look up "Enable Banking Oy" in the EBA register once, and read their privacy page's end-user section.
2. Keep the key file outside the repository, 0600, and create a new application (new key) if it may have leaked; revoke the old one in their control panel.
3. Let consents lapse when you stop syncing for a long time, or revoke them at the bank; do not renew blindly.
4. Keep the redirect URL on localhost.
5. Re-read the "personal use" clause of their terms yearly; it changed on 2026-01-09.

Vulnerabilities of Enable Banking or of your bank are out of this project's scope ([SECURITY.md](https://github.com/mmornati/ai-finance-coach/blob/main/SECURITY.md)): report them to the vendor.
