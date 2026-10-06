# Household and people (E14)

One household, several people (N adults, M children): each account has an owner, each transaction belongs to a person, every figure can be seen for the whole
household or for one member, the children's money has its own view, shared costs can be split by rule, and each person can have their own login.

Real names stay on this machine. A model sees members only as pseudonyms (`adult-1`, `kid-1`, never a name, an alias, a birth year or a rule's label) and, for age, only a band (`adult`, `under 6`, `6-11`, `teen`); exact birth years stay local (tax).

| Story | What | Where |
|---|---|---|
| E14-1 | members declared in `memory/household.yaml`, shown in the web app, pseudonyms for models | `coach memory member add`, `/household`, `household_overview` |
| E14-2 | each account has an owner (`joint` or one member) and a purpose, edited from the CLI or the web app, used by the analytics | `coach accounts set`, `/household` |
| E14-3 | a transaction belongs to a person: manual reassignment > memory rule > owner of the account; recorded, reversible, explained | `coach household assign / why / log / undo / rule`, the transaction panel |
| E14-4 | household / one-member filter on every analytics endpoint, page and MCP tool | `?member=` on the API, the person switch, the `member` argument of the tools |
| E14-5 | the children's money: pocket money, extra top-ups with their source, spending, balance trend, pocket versus extra | `coach household kids`, `/kids`, `kids_money` |
| E14-6 | weekly / monthly kid budgets and gentle alerts (local only); a read-only kid view with their own data only | `coach household budget`, `/kids`, the `kid_budget` alert, `/me/*` |
| E14-7 | transfers between the household's banks paired and excluded from spending (extends E1-12) | `coach transfers`, `[transfers] cross_bank_window_days` |
| E14-8 | per-person logins (adult: all data, child: own data), per-login preferences, audit of who changed what | `coach users ...`, `coach ui --login-link --user ID`, `/household` |
| E14-9 | shared costs split by rule (50/50, by income, custom percentages): "who pays what" | `coach household allocation / allocate`, `/who-pays`, `who_pays` |

## Members and account owners (E14-1, E14-2)

`household.yaml` (see [memory.md](memory.md)):

```yaml
members:
  - { id: anna, name: Anna Rossi, role: adult, birth_year: 1984, aliases: ["MME ANNA ROSSI"] }
  - { id: mia, name: Mia Rossi, role: child, birth_year: 2012, pocket_money: { amount: 10, period: monthly, day: 5 } }
```

`id` is a lowercase slug; `name` and `aliases` (the spellings a bank prints for a holder) are local. `pocket_money` (children only) is what the child is MEANT to get.

An account's `owner` (`accounts.owner`) is `joint` or a member. `coach accounts set UID --owner "Anna Rossi" --purpose main` (and the Household page) accepts an id, a name or an
alias of a declared member and STORES THE MEMBER ID, so one person never becomes two spellings; once members are declared, any other owner text is refused. Purposes: `main`,
`cards`, `rental`, `kids`, `savings`. `coach memory check` warns about an owner that is not a member and about attribution rules that name an unknown account or member.

## Who a transaction belongs to (E14-3)

The first hit wins:

1. **a manual reassignment** (`coach household assign TX MEMBER|joint`, the "Reassign to" box of the transaction panel). Stored in `tx_person`, every change logged in
   `tx_person_log` (who, when, before, after). `coach household unassign TX` and `coach household undo LOG_ID` (or "Undo my reassignment") put it back; nothing is lost.
2. **an attribution rule** of `household.yaml`, in file order:
   ```yaml
   attribution:
     - { id: noa-prepaid, member: noa, match: { account: nk } }                       # a prepaid card account per child
     - { id: mia-card, member: mia, match: { account: fo, card_last4: "4242" } }      # her card on the shared account, when the bank prints the digits
   ```
   `match` keys (all must match): `account` (uid, label or name), `card_last4`, `merchant_key` (regex), `description` (regex), `direction`, `amount_min` / `amount_max`.
   The last four digits are read only from forms that clearly are a card tail (`X4242`, `****4242`, `CB*4242`, `CARTE N° 4242`); a bare `CB 0210` is a date on many French
   statements and is NOT read as a card.
3. **the owner of the account**: a member, `joint` (the household as a whole), or nobody when the owner is unknown.

A transaction's person is `member id | "joint" | none`. It never changes a category, an amount or a transfer link. `coach household why TX` (and `coach explain TX`, and the "Why this
person?" box) shows the manual line, every rule with whether it matched and why not, the account owner and the card digits.

## Person views (E14-4)

`Dataset.member_view(member)` is the dataset seen as one member: only the transactions attributed to them (on any account), the accounts they own or spent through, and the
balances of the accounts they OWN (a joint account's balance is never a person's balance; a forecast of a member covers the accounts they own). Every analytics function works on it
unchanged. The API takes `?member=<id>` on every analytics READ endpoint (a 404 for an unknown member; a test walks the OpenAPI schema and fails if one is missing), the web app's person switch
sets it, and the MCP tools take a `member` pseudonym (`transactions_search`, `cashflow`, `category_averages`, `recurring`, `price_changes`, `anomalies`, `forecast`, `budget_status`,
`budget_suggestions`, `calendar`, `goals`, `year_review`). Household-wide endpoints take no person filter, on purpose: net worth, loans, contracts, alerts, the review queue, memory,
connections and EVERY write (a write or a household figure computed on one member's view would be wrong: `POST /alerts/check` on a member view would resolve every other alert).

## The children's money (E14-5)

`coach household kids [MEMBER] [--months N] [--json]`, the Kids' money page, the `kids_money` tool. Computed from the transactions attributed to the child and the balances of the
accounts they own, over the last N CLOSED months:

* **inflows**: credits that are transfers or income (a card refund is negative spending). A credit in a transfer link (the parent's debit paired with the child's credit, E14-7) is an
  internal transfer of the household (excluded from the household's income and spending) but IS the child's income here, with its **source** (the member owning the paying account,
  or `joint`); unlinked, a member named in the description gives the source, else `other` (income) or `unknown`.
* **pocket money**: three or more credits of nearly the same amount (within 5 %, at least 0.50 EUR) from the same source at a weekly, fortnightly or monthly rhythm, or a credit of
  about the declared amount. Everything else is an **extra top-up** with its source.
* **ratio**: the pocket-money share and the extra share of all credits.
* **spending** by category and month, this month so far.
* **balance trend**: the balance of the child's own accounts at each month end, rebuilt BACKWARDS from the newest balance with the later transactions (an estimate; a month before the
  first known transaction is left out).

## Kid budgets and gentle alerts (E14-6)

`kid_budgets` in `household.yaml` (`coach household budget set ID --member M --period weekly|monthly --limit 15 [--group food]`): a weekly (Monday to Sunday) or monthly limit on
the child's attributed spending, optionally for one category or group. Status: `ok` below 80 %, `at_risk` from 80 %, `over` above 100 %.

The alerts use the E10 engine as the kind `kid_budget` (limit reached or close, and an unusual payment: at least four times the child's median payment and at least 15 EUR in the last
7 days, with at least eight payments in the last six months). They are **local only**: no external channel (ntfy, e-mail, Telegram, the macOS notification) ever receives them, not even
as a count, and the weekly digest leaves them out. Their wording is gentle and may name the child because it never leaves the local feed.

## Transfers across the household's banks (E14-7)

Extends [E1-12](reference.md) in TWO passes. Pass 1 is the original logic inside `[transfers] window_days` (3), whatever the banks: unambiguous pairs are locked first and never re-opened. Pass 2 looks only at
the legs still unmatched and, across DIFFERENT banks, widens to `cross_bank_window_days` (5: weekends, holidays, instant credits); a gap beyond `window_days` needs STRONG evidence (the other account's IBAN or
holder, own-account types, or a household MEMBER named in a leg who OWNS the account on the other side and not this leg's own account: a parent's debit naming the child, the child's credit naming the parent);
ties go to the smallest gap. Pass 2 can only add pairs, never take one away (a test checks it), so repeated equal amounts a few days apart never turn clear same-day pairs into ambiguous ones. A pair gets
`scope: household` when the two accounts belong to different members, and `to_child` when the credit lands on a child's account. Without strong evidence a pair is only a proposal, as before; card top-ups stay proposals.

## Who pays what (E14-9)

`allocations` in `household.yaml`:

```yaml
allocations:
  - { id: housing, title: Housing, match: { group: housing }, method: income }                       # in proportion to each member's income (last 12 closed months)
  - { id: cinema, match: { category: leisure.cinema_events }, method: custom, shares: { anna: 60, luca: 40 } }
```

`match` takes `category`, `group`, `tag`, `merchant_key` (regex) or `account`; `method` is `equal`, `income` or `custom` (percentages adding up to 100); `among` limits the members (default:
every adult). A payment belongs to the FIRST rule that matches it. Per rule and member: the **fair share** of the whole cost (`owed`), what they **paid** out of their own money (costs
attributed to them), and the **settlement** `net = paid - share x (what the sharing members paid personally)`. Costs paid from the joint account settle nothing (the common pot paid them);
a cost attributed to someone outside the rule, or to nobody, is reported apart. Cents are shared by largest remainder, so the nets add up to exactly zero. It reports a split of the past: it
moves no money, and gives no legal or tax view of a couple's finances.

## Logins, roles, preferences, audit (E14-8)

Logins are created **only in a terminal**:

```
uv run coach users add papa --role adult --member luca
uv run coach users add mia-kid --role child --member mia
uv run coach ui --login-link --user mia-kid        # the one-time link of that login (without --user: the owner's)
uv run coach users list | disable ID | enable ID | set-role ID --role R [--member M] | remove ID | prefs ID --set theme=dark | audit
```

* **adult**: all the data, like the owner login that has always existed (a session without a user IS that owner login).
* **child**: the own data of one member declared with `role: child`, through `/api/v1/me/...` only. The kid page shows their balance, pocket money and top-ups (the source only as
  "a parent" / "family" / "someone else", never a name), spending, their own budgets with a gentle message, and their latest payments. Read-only.

Security (unchanged: one-time link in the URL fragment, single use, 2 minutes, HttpOnly SameSite=Strict cookie, CSRF token on every write, Host and Origin checks, strict CSP):

* the one-time token can carry a login; the cookie names it (`sid.issued.login.signature`, HMAC, so it cannot be edited). The login's **role and member are read from the database on EVERY
  request**: disabling a login, deleting it or changing its role takes effect on the next request, a stale cookie keeps no privilege, and a token for a missing or disabled login is refused.
* **child scope is enforced server-side, deny by default**: the guard middleware (`coach.api.app`) refuses (403 `forbidden_scope`) every request of a child login except an explicit
  allow-list (`CHILD_ALLOWED`: the session, the sign-out, the taxonomy and `/me`, `/me/summary`, `/me/transactions`, `/me/preferences`), whatever the endpoint, the method or whether it exists. A new
  endpoint is closed to a child until someone adds it to that list on purpose. The `/me/*` endpoints take the member from the login, never from a parameter; the snapshot dependency also forces
  a child's member (a second line of defence). `tests/test_api_household.py` calls every route of the application as a child and expects 403 on all but the eight allowed.
* accepting, rejecting and reverting memory proposals stay terminal-only: no web endpoint does it, for any role. Logins cannot be created, promoted or enabled from the web app.
* **audit**: every change made through the web app by a login is recorded in `audit_log` (method, endpoint, status, login: never a payload; previews are not changes), and the memory history
  carries `Source: ui:<login>` for every memory write made from the web app (`coach users audit`, the Household page, `coach memory history`).
* **preferences** are per login (`locale`, `theme`, `default_member`, `landing`; a whitelist, a child cannot set a default member), stored in the database and applied at sign-in.

Honest limits: the owner login (a session without a user) sees everything and is meant for the person at the machine; a child login is as private as the link that opened it (a link is single use and
short-lived, but whoever holds the browser holds the session until it expires, `[ui] session_hours`, or you disable the login); the person attribution of a joint account depends on the rules you
write (a shared card without digits cannot be attributed to a child by itself).

## Privacy summary

| Surface | What it holds |
|---|---|
| Models (MCP tools, coach) | member pseudonyms and roles; counts; amounts, dates, categories; `ref` hashes; never a name, alias, birth year, rule id, budget id or allocation title (scrubbed) |
| External alert channels | never anything about a child or a kid budget (the kind is local-only) |
| Local web app, CLI | real names, owner values, rules (the child's own page: only their own data) |
| Child login | own data only; no other member's name, no account list, no memory, no coach |

Files: `memory/household.yaml` (members, rules, budgets, allocations), database tables `tx_person`, `tx_person_log`, `ui_users`, `audit_log` (migration 0022, additive).
