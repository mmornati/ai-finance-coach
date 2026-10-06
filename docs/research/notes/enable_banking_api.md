# Enable Banking API for a personal, self-hosted daily ingestion script (technical and contractual feasibility, as of Oct 2026)

Method note: these sources were used. Enable Banking API reference, FAQ, market docs and Terms of Service; the EBA and press coverage of the PSD2 RTS amendment; Firefly III and Actual Budget docs; GitHub. The Enable Banking ASPSP (bank) catalogue for IT and FR was also pulled live on 2026-10-04 through the user's own already-configured "BankMCP" server, which is backed by Enable Banking (tool `list_banks`). That listing is first-hand data from the live API, not a web page, so it has no URL. It is cited as "[EB live ASPSP list via BankMCP, 2026-10-04]". The user already appears to have a working Enable Banking restricted-mode setup behind BankMCP, which is itself evidence of feasibility.

Several WebFetch results were summarised by an intermediate model. Where exact field and enum spelling matters, check it against the raw API reference (https://enablebanking.com/docs/api/reference/).

## 1. Personal and restricted use: registration, account linking, cost, limits

### Takeaway
Enable Banking is currently the only mainstream PSD2 aggregator that, as a matter of contract, offers free production access to private individuals for their own accounts. This is called "restricted mode": you activate the app by linking your own accounts, and only those linked accounts are ever returned. The Terms of Service (updated 9 Jan 2026) state that this use is free. They also state that production use is limited to linked accounts and to evaluation or the personal use of private individuals.

### Cited Findings
- Terms of Service: "Use of the API in the Production Environment is limited to Linked Accounts and is available solely for evaluation purposes or for the personal use of private individuals." — [Enable Banking Terms](https://enablebanking.com/terms)
- Terms: "Use of the Control Panel and the API under these Terms is free of charge." Any use outside these Terms needs "a separate agreement with Enable Banking covering the intended use case and applicable pricing". — [Enable Banking Terms](https://enablebanking.com/terms)
- The Terms were last updated on 9 January 2026. — [Enable Banking Terms (search snippet)](https://enablebanking.com/terms)
- FAQ: when a production app is "activated in restricted mode via the 'Activate by linking accounts' feature, you can only fetch data from the specific accounts that have been explicitly linked to the application". You must link all the accounts you want, because "any other accounts will be stripped from the response". — [Enable Banking FAQ](https://enablebanking.com/docs/faq/)
- FAQ on pricing: commercial pricing is "volume based… number of accounts accessed and payments made per month". "When getting started you can use sandbox environment or link own accounts to your production application." — [Enable Banking FAQ](https://enablebanking.com/docs/faq/)
- Registration flow, as documented by Firefly III:
  1. Sign up at enablebanking.com and confirm by email.
  2. In the Control Panel, create an application. Choose Production or Sandbox and set a redirect URL.
  3. On submit, the browser downloads a PEM private key ("Save this file securely").
  4. Link your accounts in the portal before you call the API. Otherwise "the Data Importer will not receive any accounts".
  5. The app then shows as "Restricted" but "Active".
  — [Firefly III data importer: Enable Banking](https://docs.firefly-iii.org/tutorials/data-importer/eb/)
- Actual Budget documents the same steps: Production app, redirect URL, link accounts in the EB UI, copy the Application ID, upload the key. Actual flags its integration as "experimental". — [Actual Budget: Enable Banking](https://actualbudget.org/docs/advanced/bank-sync/enable-banking)

### Inferences
- A cron script that reads only your own accounts fits the free "personal use of private individuals" clause.
- Adding a new bank or account means relinking in the Control Panel. This is a manual browser step.
- No published cap on the number of linked accounts was found for restricted mode.

### Gaps
- No published numeric limits were found for restricted mode, such as a maximum number of linked accounts or a maximum number of API calls.
- No statement was found on whether restricted mode can be withdrawn or changed. The Terms only say EB may terminate access for "misuse, security concerns… or other high-risk behaviour" ([Terms](https://enablebanking.com/terms)).

## 2. Auth flow: JWT, /auth, /sessions, consent validity, renewal and SCA

### Takeaway
Each call uses a self-signed RS256 JWT, created with the app's private key and valid for at most 24 hours. The steps are:
1. `POST /auth` with `access.valid_until`. This returns a bank redirect URL.
2. The user completes SCA in a browser.
3. `POST /sessions` with the returned `code`. This returns a `session_id` and the account uids.
4. Read data with `GET /accounts/{id}/transactions`.

Consent lasts until `valid_until`. That is capped by each bank's `maximum_consent_validity`, which is 180 days for almost all IT and FR banks. There is no refresh step: when consent expires, you start a new `/auth` with a new SCA.

### Cited Findings
- JWT header: `typ: JWT`, `alg: RS256`, `kid: <application_id>`. Claims: `iss: "enablebanking.com"`, `aud: "api.enablebanking.com"`, plus `iat` and `exp`. The maximum TTL is 86400 s. — [EB API reference](https://enablebanking.com/docs/api/reference/)
- `access.valid_until` in `POST /auth` "cannot exceed the current time plus the ASPSP's `maximum_consent_validity`" (in seconds, from `GET /aspsps`). — [EB API reference](https://enablebanking.com/docs/api/reference/)
- `POST /sessions` exchanges the authorization code for a session that holds the account details and a `session_id`. `DELETE /sessions/{id}` closes the session and revokes consent where possible. — [EB API reference](https://enablebanking.com/docs/api/reference/)
- FAQ: "the majority of ASPSPs" support 180 days. "The PSU consent only needs to be renewed once the `valid_until` date-time is reached. There is no separate consent 'refresh' process." — [EB FAQ](https://enablebanking.com/docs/faq/)
- Live ASPSP data, 2026-10-04:
  - Italy: 337 of 339 IT ASPSPs report 180 days max consent (one reports 90 days, one 7 days).
  - France: almost all FR ASPSPs report 180 days. The exception is Trade Republic FR at 90 days. — [EB live ASPSP list via BankMCP, 2026-10-04]
- Always send `psu_type` (`personal` or `business`). — [EB API reference](https://enablebanking.com/docs/api/reference/)
- The PSD2 RTS amendment, Commission Delegated Regulation (EU) 2022/2360, does three things:
  - It extends SCA renewal for account information from 90 to 180 days.
  - It makes the AISP exemption mandatory for banks.
  - It applies from 25 July 2023.
  — [Financial Institutions News](https://www.financialinstitutionsnews.com/2022/12/05/sca-amendments-to-apply-from-25-july-2023/); [Vixio](https://vixio.com/insights/pc-90-becomes-180-eba-makes-key-sca-change); [EBA press release](https://www.eba.europa.eu/publications-and-media/press-releases/eba-publishes-final-report-amendment-its-technical-standards)
- Italian specifics:
  - "Many Italian banks enforce one active consent per TPP per user. Initiating a new consent invalidates the previous one."
  - Most banks ask for a codice fiscale or a username before SCA.
  - Intesa Sanpaolo uses redirect with SCA in the Intesa Sanpaolo Mobile app, where the "app switch is supported but not automatic".
  — [EB Italy market docs](https://enablebanking.com/docs/markets/it/)

### Inferences
- Design the script around two phases:
  - (a) An interactive `consent` command that runs about every 180 days. It runs `/auth`, opens the URL, catches the redirect (a localhost HTTPS callback or paste-the-URL), runs `POST /sessions`, and stores `session_id`, `valid_until` and the account uids.
  - (b) An unattended daily `fetch`.
- Alert about 14 days before `valid_until`, and on 401 or invalid-session errors.
- Treat the private key as a secret (chmod 600). It is the only credential, since the JWT is minted locally.
- On Italian banks, re-consenting kills the previous consent. Never run two parallel consents to the same bank. That includes not using the same bank in BankMCP and in a new script at the same time, if both are linked under the same EB application or TPP.
- The redirect URL must be registered in the app. The Actual and enable-actual docs say HTTPS is required ([enable-actual](https://github.com/2manyvcos/enable-actual)).

### Gaps
- Some banks may cut consent early, for example after a password change. The FAQ has a "premature session expiration" entry, but its text was not retrieved.
- Banks may also revoke consent before `valid_until`. No per-bank data on this was found.

## 3. Rate limits, history depth, pagination and data fields

### Takeaway
Plan for at most 4 unattended pulls per account per day. The limit comes from the PSD2 RTS and is enforced by banks. Enable Banking passes it through as HTTP 429 `ASPSP_RATE_LIMIT_EXCEEDED`. A daily cron (or 2 to 4 runs a day) is well within it.

History depth depends on the bank. It is often 1 to 3 years right after first consent, and only 90 days afterwards. So pull the full history immediately after each consent, then fetch incrementally.

The transaction model is ISO 20022-like. It includes booking, value and transaction dates, remittance information, creditor and debtor, bank transaction code, MCC, BOOK or PDNG status, and both `entry_reference` and `transaction_id`. Paginate with `continuation_key`.

### Cited Findings
- FAQ: "many ASPSPs have the limit of 4 times a day for data fetches when PSU (i.e. the end-user) is not online." — [EB FAQ](https://enablebanking.com/docs/faq/)
- Rate-limited calls return HTTP 429 with `ASPSP_RATE_LIMIT_EXCEEDED`. Back off and retry. — [EB API reference](https://enablebanking.com/docs/api/reference/)
- PSU headers such as `Psu-Ip-Address` and `Psu-User-Agent` signal that the user is present. If you omit them, the call counts as background access. Some ASPSPs require certain headers (`required_psu_headers` in the ASPSP metadata; error 422 `PSU_HEADER_NOT_PROVIDED`). — [EB API reference](https://enablebanking.com/docs/api/reference/); [EB FAQ titles](https://enablebanking.com/docs/faq/)
- History depth:
  - "Most ASPSPs provide access to at least one year of transactions… many… two to three years or even longer."
  - "full history is typically available only for a short period of time after the initial authorisation… Once this time has elapsed, many ASPSPs restrict access to transaction data to just the past 90 days."
  — [EB FAQ](https://enablebanking.com/docs/faq/)
- `GET /accounts/{account_id}/transactions` takes `date_from`, `date_to`, `continuation_key`, `transaction_status` and `strategy`. `strategy` is `default` or `longest`; `longest` gets the most history available. — [EB API reference](https://enablebanking.com/docs/api/reference/)
- Transaction fields:
  - Identifiers: `entry_reference`, `transaction_id`.
  - Dates: `booking_date`, `value_date`, `transaction_date`.
  - Amount: `transaction_amount` (amount and currency), with a `credit_debit_indicator`.
  - Counterparty and description: `creditor`, `debtor` (party objects, with accounts held separately), `remittance_information` (an array).
  - Codes: `bank_transaction_code`, `merchant_category_code`.
  - Other: `status` (`BOOK` or `PDNG`), `balance_after_transaction`, `note`.
  — [EB API reference](https://enablebanking.com/docs/api/reference/)
  - Caveat on `credit_debit_indicator`: the intermediate summary gave the values as "CRDT/DRDT". The ISO 20022 standard values are `CRDT` and `DBIT`, so check against the raw schema.
- A per-transaction detail endpoint exists: `GET /accounts/{account_id}/transactions/{transaction_id}`. — [EB FAQ](https://enablebanking.com/docs/faq/)

### Inferences
- Amounts are probably unsigned, with the sign carried by `credit_debit_indicator`. Normalise to signed amounts when you ingest. (The BankMCP server already exposes signed amounts.)
- A suggested cron plan:
  - Once a day, fetch `date_from = last_booking_date - 10 days` to catch late bookings and pending-to-booked changes.
  - Loop until `continuation_key` is null.
  - On the first run after each consent, use `strategy=longest`.
- MCC and bank transaction codes will be sparse and bank-dependent. Do not rely on them for categorisation.
- Balances come from a separate endpoint (`/accounts/{id}/balances`), which counts toward the same daily limit at many banks.

### Gaps
- Whether the 4/day limit is counted per account or per consent, and whether balance and transaction calls count separately, varies by bank. EB does not publish a per-bank count.
- No published rule was found on whether `continuation_key` page requests count toward the 4/day limit.

## 4. Deduplication: unstable IDs, pending to booked

### Takeaway
Use `entry_reference` (not `transaction_id`) as the primary key, for booked transactions only. Many banks provide no entry_reference, or duplicate values, and pending items usually have none. You therefore need a fallback fingerprint plus a policy for replacing pending rows.

### Cited Findings
- FAQ: `entry_reference` is used to "identify and match transactions across multiple retrievals". `transaction_id` "should not be used as a unique reference to identify transactions". — [EB FAQ](https://enablebanking.com/docs/faq/)
- FAQ: "Some do not provide transaction entry references at all, and some provide duplicate values even though they should not." "In most cases, `entry_reference` values are provided only for booked transactions." Pending transactions "should be excluded when matching", unless the bank's pending ID stays the same after booking. — [EB FAQ](https://enablebanking.com/docs/faq/)
- enable-actual: "at some banks (e.g., N26 (DE)), new transactions are deleted after a certain period and recreated with a new ID". The workaround is to ignore the ID and rely on Actual's fuzzy dedup. — [enable-actual](https://github.com/2manyvcos/enable-actual)
- Actual Budget users want existing pending transactions updated once they post. Currently the only workaround is to not import pending transactions. — [Lunch Flow feature request](https://lunchflow.featurebase.app/p/option-to-update-pending-status-of-duplicates-in-actualbudget)

### Inferences
- A suggested approach:
  - Store booked transactions keyed on `(account_uid, entry_reference)` when it is present and unique within the batch.
  - Otherwise, key on a hash of `(account_uid, booking_date, amount, currency, credit_debit_indicator, normalised remittance text, counterparty IBAN)`, plus a sequence counter for identical same-day items.
  - Keep pending transactions in a separate table that you replace on every run, rather than appending.
- Keep the raw JSON payload so you can re-derive keys later.

### Gaps
- No per-bank table of entry_reference reliability for Intesa, UniCredit, Fineco, BNP, Crédit Agricole, Boursorama, Revolut or N26 was found. You will need to observe it empirically.

## 5. Coverage of Italian and French banks, including credit cards

### Takeaway
Every bank named in the brief is in Enable Banking's live catalogue, with 180-day consent. Many are flagged `beta` (Intesa Sanpaolo, BNP Paribas, all Crédit Agricole regional banks). Italian credit cards are a weak point: UniCredit and Mediolanum exclude credit-card accounts from the PSD2 API. American Express is listed in France but not in Italy.

### Cited Findings
- Italy: 339 ASPSPs. Personal ASPSPs with 180 days max consent include:
  - Not beta: FinecoBank, UniCredit, Banca Mediolanum, N26, Revolut, ING, BBVA, PayPal, Wise.
  - Beta: Intesa Sanpaolo, Isybank, Banco BPM, BPER, Banca Sella, Widiba, HYPE, illimity, Credit Agricole Cariparma, Postepay, NEXI.
  - Of BNL, only "BNL Corporate" (business) appeared in the name filter.
  - No American Express entry for IT.
  — [EB live ASPSP list via BankMCP, 2026-10-04]
- France, all 180 days unless noted:
  - Not beta: Boursorama Banque, Revolut, N26, American Express, LCL, Crédit Mutuel, CIC, Hello Bank, Qonto, Wise, PayPal, bunq, Monabanq, Banque Populaire regional banks.
  - Beta: BNP Paribas, all Crédit Agricole regional banks, Société Générale (personal), La Banque Postale, Caisses d'Epargne, Fortuneo.
  - Trade Republic: 90 days, beta.
  — [EB live ASPSP list via BankMCP, 2026-10-04]
- UniCredit (IT) "does not support credit card accounts through PSD2 API", though prepaid cards with an IBAN are accessible. Banca Mediolanum excludes credit-card accounts. — [EB Italy market docs](https://enablebanking.com/docs/markets/it/)
- Crédit Agricole had a major sync outage (1 to 13 Oct 2025) at another aggregator, Powens. This illustrates that the bank-side APIs are fragile. — [Powens status](https://powens.statuspal.eu/incidents/208291)

### Inferences
- Credit cards issued by Italian banks (Intesa, UniCredit, Nexi-issued cards) often appear only as charges on the current account. Alternatively they are visible through the NEXI ASPSP (beta). Plan for a CSV or PDF statement fallback for cards.
- "Beta" means the connector works but is less proven. Expect more ASPSP_ERRORs.

### Gaps
- Per-bank history depth and entry_reference behaviour were not retrieved. The FR market page was not fetched.
- Whether Fineco credit cards, Intesa credit cards and Boursorama deferred-debit cards appear as accounts was not verified.

## 6. Client libraries, sample code and OSS projects using Enable Banking

### Takeaway
There is no official SDK. Enable Banking publishes multi-language samples, including Python and JS, and a CLI. The API is simple REST plus JWT, so `requests`/`httpx` and `PyJWT[crypto]` are enough. Several personal-finance OSS projects already integrate it in restricted mode.

### Cited Findings
- From the Enable Banking GitHub organisation ([GitHub](https://github.com/enablebanking)):
  - `enablebanking/enablebanking-api-samples`: "Code samples C#, Go, JavaScript, PHP, Postman, Python and Ruby". Updated Mar 2026, 68 stars.
  - `enablebanking-cli`: Python, last updated Jun 2024.
  - The older `OpenBankingPythonExamples` and `OpenBankingJSExamples` repos are archived.
- Firefly III Data Importer supports Enable Banking. — [Firefly III docs](https://docs.firefly-iii.org/tutorials/data-importer/eb/)
- Actual Budget has experimental native Enable Banking bank sync. — [Actual docs](https://actualbudget.org/docs/advanced/bank-sync/enable-banking)
- `2manyvcos/enable-actual`: TypeScript, scheduled imports into Actual, with ntfy.sh alerts on session expiry. It recommends one EB application per bank ("Don't add all your banks to a single application!"). — [enable-actual](https://github.com/2manyvcos/enable-actual)
- An Enable Banking MCP server is listed on Glama. — [glama.ai](https://glama.ai/mcp/servers/kr09pflrj3)

### Inferences
- enable-actual's advice to use one app per bank probably isolates restricted-mode linking and key rotation per bank, and avoids the Italian one-consent-per-TPP clash. The minimal Python stack would be:
  - `PyJWT` with `cryptography` to sign RS256 using the PEM key.
  - `httpx` for HTTP.
  - SQLite for storage.
  - A small local HTTPS callback, or a manual "paste redirected URL" step.

### Gaps
- No Home Assistant integration for Enable Banking was found. The search returned only the HA Firefly III integration ([HA Firefly III](https://www.home-assistant.io/integrations/firefly_iii/)).

## 7. Alternatives

### Takeaway
- GoCardless Bank Account Data (Nordigen) has not accepted new accounts since July 2025. Existing users keep working.
- The other aggregators (Tink, Salt Edge, TrueLayer, Yapily, Powens, Bridge, Plaid EU) target businesses. They offer sandbox or free testing, but no documented free production tier for individuals was found.
- For France, Woob scrapers exist but are fragile: Boursorama is reported to be blocking bots since May 2026.
- The robust fallback is manual CSV, OFX or CAMT.053 export, then import.

### Cited Findings
- "From July 2025 onwards, GoCardless has stopped accepting new Bank Account Data accounts". Existing users should continue to work. — [Actual Budget GoCardless docs](https://actualbudget.org/docs/advanced/bank-sync/gocardless/)
  - Conflicting source: a 2026 dev.to article claims GoCardless has a "Free tier still available (with limits)" and covers "2,300+ banks in 31 European countries". This likely applies only to existing accounts. — [dev.to](https://dev.to/johnfrandsen/nordigen-alternatives-in-2026-a-developers-guide-to-european-bank-data-apis-3pie)
  - GoCardless's own /bank-account-data/announcement page only says a legacy product was "discontinued on the 18th of December 2023". — [GoCardless](https://gocardless.com/bank-account-data/announcement)
- From the same dev.to article ([dev.to](https://dev.to/johnfrandsen/nordigen-alternatives-in-2026-a-developers-guide-to-european-bank-data-apis-3pie)):
  - TrueLayer: free sandbox; production priced "around £0.10-0.30 per successful API call"; "1,500+ banks".
  - Tink: "Limited free tier", enterprise-focused, "6,000+ banks globally".
  - Salt Edge: developer sandbox and tiered plans.
  - `open-banking.io`: described as self-hostable with no per-call fees.
- A search-result summary described Enable Banking as "the most self-serve and indie-friendly for account data". — [banq.ai](https://banq.ai/open-banking-apis-europe), via search snippet
- Powens: 1,800+ institutions in 12+ countries plus 200+ investment platforms. Offers a free self-service sandbox. No permanent free tier for individuals was found. — [Powens](https://www.powens.com/products/financial-data-aggregation)
- Woob:
  - It has Boursorama and Crédit Agricole modules.
  - Users report maintenance concerns and frequent breakage.
  - Boursorama is reported to have been blocking automated access since 29 May 2026.
  — [linuxfr forum](https://linuxfr.org/forums/general-cherche-logiciel/posts/automatisation-quittance-et-synchro-banque-sur-mon-serv); [Portfolio Performance forum](https://forum.portfolio-performance.info/t/securities-price-update-not-working-since-may-29/39448); [woob GitLab issue](https://gitlab.com/woob/woob/-/issues/788)

### Inferences
- For an individual in IT and FR in 2026, Enable Banking restricted mode is the only realistic free and compliant PSD2 option.
- Keep a file-import path (CSV, OFX, CAMT.053, or the PDF statements Italian banks commonly provide) as a fallback for:
  - credit cards,
  - beta-connector failures,
  - lapsed consents.
- Woob is a last resort. It needs your bank password stored on disk and likely breaches bank terms of use.

### Gaps
- Bridge (by Bankin'), Yapily and Plaid EU details for individuals were not retrieved. Plaid's EU status in 2026 was not verified.

## 8. Terms of service: personal use, storage, redistribution as OSS

### Takeaway
Personal use of your own accounts is explicitly allowed and free. If the project is published as OSS, each user must create their own Enable Banking application and key. Running a shared hosted app, or shipping one app key for others, would mean making the API "accessible to any third party". That is prohibited without a commercial agreement, and in practice it requires going beyond restricted mode. The Terms do not set data-retention obligations. Storage falls under your own GDPR position as a private individual (the household exemption is a reasonable assumption, not verified here).

### Cited Findings
- "You shall not license, sublicense, sell, resell, market, lease, loan, rent, transfer, assign, distribute, disclose, or make accessible to any third party… the Control Panel and the API." Users must "keep credentials secure, and protect any private keys or tokens". — [Enable Banking Terms](https://enablebanking.com/terms)
- Businesses that register are treated as "solely for evaluation purposes" until a formal agreement is signed. — [Enable Banking Terms](https://enablebanking.com/terms)
- EB's liability is capped at EUR 100. EB may terminate access for misuse or security concerns. — [Enable Banking Terms](https://enablebanking.com/terms)
- The Terms do not explicitly address data retention; this is deferred to the Privacy Notice. — [Enable Banking Terms](https://enablebanking.com/terms)
- The existing OSS integrations (Firefly III importer, Actual Budget, enable-actual) all follow a bring-your-own-app model: each user creates an EB app and uploads their own PEM key. — [Firefly III](https://docs.firefly-iii.org/tutorials/data-importer/eb/); [Actual](https://actualbudget.org/docs/advanced/bank-sync/enable-banking); [enable-actual](https://github.com/2manyvcos/enable-actual)

### Inferences
- Publishing the script's code as OSS is fine. Publishing or hosting a shared application ID and key, or offering the service to others, is not.
- The README should tell users how to register their own restricted app.

### Gaps
- Enable Banking's Privacy Notice and any data-processing terms for restricted mode were not reviewed.
- No EB statement was found specifically on OSS bring-your-own-app projects. The inference rests on the Terms text and established community practice.
