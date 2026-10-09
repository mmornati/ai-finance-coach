# Transactions

Every operation across your accounts, with the totals of what you filtered, and a side panel that says why each one has its category.

<figure class="shot" markdown>
![The transactions list of the demo household](../assets/screens/transactions-light.webp#only-light){ loading=lazy }
![The transactions list of the demo household](../assets/screens/transactions-dark.webp#only-dark){ loading=lazy }
<figcaption>Transactions grouped by day, with the category, the person, the account and the amount.</figcaption>
</figure>

## What you see

Each row shows the clean merchant name with the raw bank description under it, the **category** chip, the person it is attributed to
when it is not the account's owner, a **transfer** chip for internal transfers, the account and the signed amount (money out is red).
Rows are grouped by day.

Above the list, the totals bar sums the **whole filtered set**, not just the rows on screen:

| Field | Meaning |
|---|---|
| **Matching** | how many transactions match the filters |
| **Net** | money in minus money out |
| **In** / **Out** | the two sides apart |

The list loads more rows as you scroll (infinite scroll); a **Load more** button at the bottom does the same by hand.

## Filtering

Type in **Search merchant, description, tag…** for a quick text search, or use the presets **This month**, **Last month**, **90 days** and
**This year**. **Filters** opens every other criterion:

| Filter | Use it for |
|---|---|
| **From** / **To** | a custom date range |
| **Account**, **Person**, **Account purpose** | one account, one member, or the accounts used for one purpose |
| **Category** | one category, or a whole group ("All Food") |
| **Merchant contains** | every variant of a merchant |
| **Direction** | **Money out**, **Money in**, or both |
| **Amount from** / **Amount up to** | a range in EUR |
| **Tag**, **Event** | what you annotated (a trip, works, a one-off) |
| **Decided by** | the source of the category: your label, a rule, the AI, a similar merchant... |
| **Hide internal transfers** | keep only real income and spending |
| **Sort** | **Newest first**, **Oldest first**, **Largest first**, **Smallest first** |

**Clear filters** resets them all.

!!! tip "Export exactly what you see"
    **Export CSV** downloads the filtered set, with the same filters. In French formatting the file uses `;` and decimal commas, with a
    UTF-8 BOM so a spreadsheet opens it correctly. Cells that look like formulas are neutralised.

## The transaction panel

Click a row to open the panel on the right.

<video class="cast" src="../../assets/video/transaction.mp4" poster="../../assets/video/transaction.webp" autoplay muted loop playsinline></video>

<figure class="shot" markdown>
![The side panel of one transaction](../assets/screens/transaction-panel-light.webp#only-light){ loading=lazy }
![The side panel of one transaction](../assets/screens/transaction-panel-dark.webp#only-dark){ loading=lazy }
<figcaption>The panel: the category and its source, whose transaction it is, how to change the category, tags, event and note.</figcaption>
</figure>

### Why this category?

The panel shows the category and **who decided it**: your override, your label for this merchant, a built-in rule, a memory annotation,
an automatic label from a similar merchant, or an automatic label from the AI. Open **Why this category?** to see the full **decision
chain**: every step that applied, the one that **decides**, and the ones that applied but were **outranked**. It is the same chain as
`coach explain`.

### Whose transaction

"Belongs to **Joint** · the owner of the account" (or a member, through an attribution rule, or because you reassigned it). Pick a member in
**Reassign to** and press **Reassign** to attribute it by hand; the change is recorded and **Undo my reassignment** brings it back. **Why
this person?** lists the account owner, every attribution rule and the history.

### Change the category

Choose the **New category**, then where it applies:

| Option | Effect |
|---|---|
| **This transaction only** | A one-off exception; the merchant keeps its category. |
| **Every "<merchant>" transaction** | Teaches the app this merchant, for every transaction with the same key, now and in future syncs. |
| **Remember with a memory annotation** | Written to your household memory with a visible diff; it can carry tags and a note. |

Before anything is written, the panel shows a **preview**: "**3** transactions will change to **Groceries** (total €...)", how many already
are, and what would still win (an override, a transfer link). The preview runs the real classifier on the change, so it cannot disagree
with the result. Press **Apply** to write it.

### Tags, event and note

Tag a transaction with the ready-made tags (`one_off`, `reimbursable`, `capital`, `savings`, `investment`, `exclude_from_averages`) or a
**custom tag**, link it to an **Event** from your memory (a trip, works, a one-off project), and add a **Note**. **Save to memory** writes a
memory annotation. A note alone is not saved: add a tag or an event as well.

!!! note "Why tags matter"
    `one_off` and `exclude_from_averages` keep a big unusual payment out of your usual month, so the averages, the budgets and the
    forecast stay honest. `savings` and `investment` feed the savings rate.

The panel also shows when a transaction is **overridden**, **split over categories**, or **linked as an internal transfer**, each with a
button to remove it.

## Good to know

- Transaction descriptions are written by third parties. The app shows them as text; the coach treats them as data, never as instructions.
- The `tx_key` that identifies a transaction appears only in URLs, never as text. IBANs are masked to their last 4 digits.
- Every write goes through the memory store: validated, recorded in the history, and reversible from the terminal.

## From the terminal

```bash
uv run coach explain "STREAMBOX"                          # the decision chain of one transaction
uv run coach classify correct "<merchant_key>" food.groceries --name "Fresh Market"
uv run coach memory annotate --merchant-key '<regex>' --tags one_off --note "why" --dry-run
uv run coach split --help                                 # split a transaction over categories
```

## See also

- [Categories & review](categories.md): the classification pipeline and the review queue.
- [Household & kids](household.md): attribution rules.
- [Web app reference](../ui.md) and [Memory reference](../memory.md).
