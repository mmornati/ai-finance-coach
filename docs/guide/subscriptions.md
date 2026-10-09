# Subscriptions & contracts

Every recurring cost in one place: what it costs per year, whether a contract backs it, whether you use it, how to cancel it, whether a
cheaper offer exists, and what your decisions really saved.

<figure class="shot" markdown>
![The subscriptions inventory of the demo household](../assets/screens/subscriptions-light.webp#only-light){ loading=lazy }
![The subscriptions inventory of the demo household](../assets/screens/subscriptions-dark.webp#only-dark){ loading=lazy }
<figcaption>The Inventory tab: totals, a decision the coach proposed, and every service by group.</figcaption>
</figure>

!!! note "Nothing here cancels or sends anything"
    The app keeps the books; you act. It never contacts a provider, fills a website form or sends a letter.

<video class="cast" src="../../assets/video/subscriptions.mp4" poster="../../assets/video/subscriptions.webp" autoplay muted loop playsinline></video>

## The Inventory tab

### Totals

**Per month** and **Per year** for the active services, how many are **Without a contract file**, the **Savings achieved** (confirmed
by the bank data), and **Reminders**. **Show stopped subscriptions** brings back the ones that ended. Loans, rent and taxes are not
subscriptions and are not listed.

### Services by group

Services are grouped (Streaming & media, Software & cloud, Telecom, Memberships, Insurance, Energy & utilities), each group with its
monthly and yearly total. Each card shows the price per month and per year, the next charge, since when you pay it, any price rise
("price up €13.99 → €15.99 on 7 May"), and a row of chips:

| Chip | Meaning |
|---|---|
| **Contract on file** / **No contract file** / **Contract dates expired** | whether a contract file in your memory backs the payment; "expired" means every date on file is in the past: refresh it |
| **Can cancel now** · effective from a date | the rules engine says you can, and when it takes effect |
| **Not yet** · earliest a date | still inside a commitment |
| **Cancellation: more facts needed** | the contract dates are missing |
| **Usage: rarely, last 26 Jul 2026** / **Usage unknown** | what you recorded about using it |
| **Unused for 74 days (your record)** | your own last-used date is more than 60 days old |
| **Cheaper offer: saves €84 /year** | a stored alternative beats the current price |
| **1 outdated offer(s), re-check** | a stored offer is older than 30 days |

**Create contract from this subscription** drafts a contract file from the bank payments (kind, provider, billing amount and period,
first payment seen). It shows the diff first; the dates and terms stay empty and become open questions. **Details** opens the sections below.

### Usage

**How do you use it?** Record **How often** (Daily, Weekly, Monthly, Rarely, Never, I don't know), **Last used** and a note.

!!! info "Why usage is only what you record"
    The bank data shows that a service is paid, not that it is used. So the app infers nothing: a reminder appears only when **your own**
    last-used date is more than 60 days old, or when you marked a service "Never" and it is still paid.

### Can I cancel?

The answer of the rules engine for your country (France or Italy): **Now** (Yes / Not yet / Cannot tell from the dates on file), the
**Earliest effective date**, the **Notice**, the **Method**, the **Early termination** cost when you are inside a commitment, **Give
notice by**, the **Anniversary route** for insurance, and when **Free cancellation opens**.

Each answer names its **Legal basis** with its law, its source and the date the rule was last reviewed: for example Loi Hamon or the
anniversary rule for French insurance, Loi Chatel for telecom, the mutuelle rule, Legge Bersani for Italian contracts.

!!! warning "Verify with your contract"
    The rules table is a general summary of consumer law, not legal advice. Your contract decides; the page always says so.

### Cheaper alternatives

A table of offers stored for this service: **Offer**, **Price**, **Saves (12 months, net)**, **Seen** (the date) and **Source** (an
https link). The fresh offer with the highest net saving is marked **current best**, with how many months it takes to pay back the
switching costs. An offer older than 30 days is flagged **outdated, re-check** and is never the current best.

Offers come from you (**Add an offer**) or from the coach's `find-cheaper` skill in Claude Code, which searches the web with generic,
non-personal queries and stores each offer with its source and date. Either way, **savings are computed by the app, not by a model**:
monthly saving = current price minus the offer, times 12, minus the switching costs.

### Decisions and the savings tracker

**What did you decide?** Record **Cancelled**, **Renegotiated**, **Switched to another provider**, **Downgraded** or **Kept**, with the
monthly cost before and after and the dates. The bank data then checks it:

| Status | When |
|---|---|
| **confirmed by the bank data** | a cancelled service stops being charged; a renegotiated one is charged the new amount |
| **not confirmed yet** | too early to tell (a reminder appears on the check date) |
| **the bank data disagrees** | it is still charged, or at the old amount |

Only confirmed decisions count in **Savings achieved** and on the dashboard. When the coach suggests a decision, it appears at the top as
"The coach proposed to record these decisions": nothing counts until you press **Record it**.

### Cancellation letter

**Prepare cancellation letter** writes the text **on this machine**, from templates and your contract file: no model, no network.
Choose the **Language** (French, Italian, English) and **How you will send it** (Registered letter (LRAR), E-mail, Online form / chat),
then **Copy text** or **Download .txt**. Notes tell you when to send it, what leaving early costs, and what is still a
[placeholder] to complete.

Your postal address, e-mail and phone (**Add your address and e-mail to the letter**) are stored in your household file only. No model
and no finance tool can read them.

## The Detected payments tab

<figure class="shot" markdown>
![Detected recurring payments](../assets/screens/subscriptions-detected-light.webp#only-light){ loading=lazy }
![Detected recurring payments](../assets/screens/subscriptions-detected-dark.webp#only-dark){ loading=lazy }
<figcaption>Every recurring payment detected in the bank history, loans included.</figcaption>
</figure>

Everything the app detected as recurring in your bank history, including loans and taxes: the cadence (monthly, every 2 months...), the
category, the account, the **next** due date, how many payments since when, a sparkline of the last payments, the cost per month and per
year, and what it is linked to (**Loan**, **Contract**, or **No contract on file**). A price change shows as a red chip
("+14.3% on 7 May: €13.99 → €15.99") that you can **dismiss**.

Filter by **Active** / **Ended** / **All**, by cadence, or keep **Subscriptions, insurance and utilities only**.

## From the terminal

```bash
uv run coach subs list                       # the inventory
uv run coach subs show <ref>                 # one service in full
uv run coach subs draft-contracts --dry-run  # contract drafts, nothing created
uv run coach subs usage <ref> --frequency rarely --last-used 2026-07-26
uv run coach subs alternatives list
uv run coach subs letter <contract> --lang fr --channel lrar
uv run coach subs savings
```

In Claude Code, the `subscription-audit`, `contract-check` and `find-cheaper` skills work on the same data. They only propose: a contract
file or a decision the coach drafts waits for you to accept or confirm it.

## See also

- [Subscriptions reference](../subscriptions.md): the inventory, cancellation rules, alternatives, letters and the savings tracker.
- [Insights & alerts](insights-alerts.md): notice deadlines and unused-service reminders.
- [Calendar](calendar.md): renewals and notice deadlines.
