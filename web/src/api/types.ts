// Shapes of the API (see /api/docs). Money is always a decimal STRING ("-12.34").
export type Money = string;

export interface SessionInfo {
  csrf_token: string;
  version: string;
  today: string;
  locale: string;
  allow_remote: boolean;
  coach: { configured: boolean; backend: string; message: string };
  sync_daily_limit: number;
  enable_banking_configured: boolean;
  memory_history: boolean;
  user?: UserInfo | null;
}

export interface AccountMeta { uid: string; label: string; bank: string | null; owner: string | null; purpose: string | null }
export interface EventMeta { id: string; title: string | null; start: string | null; end: string | null; budget: Money | null; status: string | null; note: string | null; source: string }
export interface FiltersMeta {
  accounts: AccountMeta[]; owners: string[]; members: { id: string; name: string; role: string }[]; purposes: string[];
  tags: string[]; sources: string[]; events: EventMeta[]; groups: string[]; today: string;
}
export interface Taxonomy { groups: { id: string; categories: { id: string; description: string }[] }[] }

/** A sentence of the server with its code and raw params (i18n step 4): render it with `tServer` from "@/i18n/server". */
import type { ServerMsg } from "@/i18n/server";
export type { ServerMsg };
export interface BalanceRow { uid: string; label: string; bank: string | null; owner: string | null; purpose: string | null; balance: Money | null; balance_type: string | null; balance_type_label: string | null; booked: boolean; as_of: string | null; age_days: number | null; stale: boolean }
export interface Balances { as_of: string; household_total: Money; n_accounts: number; n_without_balance: number; mixed_types: boolean; non_booked: string[]; accounts: BalanceRow[]; note: string; note_msg?: ServerMsg }

export interface Coverage { rule: string; months: string[]; n_months: number; accounts: { uid: string; label: string }[]; skipped_months: string[]; partial_current_month: string | null; notes: string[]; notes_msg?: (ServerMsg | null)[] }

export interface MonthFlow {
  month: string; income: Money; refunds: Money; spending_gross: Money; spending: Money; one_off_spending: Money; spending_ex_one_offs: Money;
  saved: Money; net: Money; savings_rate: number | null; saved_rate: number | null; uncategorized: Money; n_tx: number; complete: boolean;
  missing_accounts: string[]; debt_service: Money; loan_principal: Money | null; savings_rate_incl_principal: number | null;
  drawn_unconnected: Money; sent_unconnected: Money;
}
export interface FlowTotals {
  n_months: number; income: Money; refunds: Money; spending: Money; one_off_spending: Money; spending_ex_one_offs: Money; saved: Money; net: Money;
  savings_rate: number | null; saved_rate: number | null; debt_service: Money; loan_principal: Money | null; savings_rate_incl_principal: number | null;
  drawn_unconnected: Money; sent_unconnected: Money;
}
export interface Cashflow { as_of: string; household: { accounts: string[]; months: MonthFlow[]; totals_complete: FlowTotals | null; totals_all: FlowTotals | null }; coverage: Coverage }

export interface MonthCategories {
  month: string; partial: boolean; as_of: string; total_spent: Money; household_monthly_avg: Money | null; household_avg_months: number;
  categories: { category: string; group: string; spent: Money; one_off: Money; n_tx: number; monthly_avg: Money | null; avg_months: number; low_confidence: boolean }[];
  groups: { group: string; spent: Money }[]; coverage: Coverage;
}

export interface ForecastPoint { date: string; balance: Money; low: Money; high: Money }
export interface ForecastEvent { date: string; account: string | null; account_label: string; label: string; amount: Money; low: Money; high: Money; source: string; ref: string; certainty: string; overdue: boolean }
export interface AccountForecast {
  account: string | null; label: string; purpose: string | null; owner: string | null; start_balance: Money | null; start_date: string | null;
  variable_monthly: Money; milestones: { days: number; date: string; balance: Money; low: Money; high: Money }[];
  min_balance: Money | null; min_date: string | null; first_negative: string | null; first_at_risk: string | null; flags: string[];
  points: ForecastPoint[]; events: ForecastEvent[];
}
export interface Forecast { as_of: string; horizon_days: number; household: AccountForecast; accounts: AccountForecast[]; assumptions: string[]; coverage: Coverage }

export interface BudgetProgress {
  id: string; target: string; month: string; monthly: Money; carry: Money; available: Money; spent: Money; excluded: Money; remaining: Money;
  percent_used: number | null; projected: Money; projected_percent: number | null; method: string; status: "ok" | "at_risk" | "over"; rollover: boolean;
  owner: string | null; account: string | null; flags: string[]; evidence: string[]; category?: string | null; group?: string | null; note?: string | null; start?: string | null;
}
export interface BudgetStatus {
  as_of: string; month: string; budgets: BudgetProgress[]; counts: Record<string, number>; unbudgeted: { category: string; spent: Money }[];
  coverage: Coverage; unallocated_refunds: Money; warnings: string[]; problems: string[]; problems_msg?: (ServerMsg | null)[];
}
export interface BudgetSuggestion { category: string; suggested: Money; median: Money; mean: Money; n_months: number; months: string[]; accounts: string[]; existing: Money | null; low_confidence: boolean }
export interface GoalProgress { id: string; title: string | null; source: string; source_ref: string; target: Money; target_date: string | null; current: Money | null; percent: number | null; remaining: Money | null; pace: Money | null; pace_basis: string; required_monthly: Money | null; projected_date: string | null; status: string; flags: string[] }

export interface TxItem {
  tx_key: string; date: string; amount: Money; category: string; source: string; tags: string[]; event: string | null; entity: string;
  merchant: string | null; description: string; account: string; account_label: string; owner: string | null; purpose: string | null;
  type: string | null; split: boolean; overridden: boolean; transfer_linked: boolean; person?: string | null;
}
export interface TxList { items: TxItem[]; totals: { count: number; sum: Money; income: Money; outflow: Money }; limit: number; offset: number; total: number; next_offset: number | null }
/** One step of the decision chain: `step` / `detail` in English, `step_code` (labels.explainStep) and `detail_msg` for the web (i18n step 4d). */
export interface ExplainStep { step: string; step_code?: string; applies: boolean; detail: string; detail_msg?: ServerMsg | null; category: string | null; source: string | null; decides: boolean }
export interface TxDetail {
  transaction: { tx_key: string; date: string; amount: Money; description: string; bank: string | null; account: string | null; account_purpose: string | null };
  parsed: Record<string, string | number | null>;
  steps: ExplainStep[];
  split: { amount: Money; category: string; note: string | null }[] | null;
  memory: { annotations: { id: string; matched: boolean; reason: string; reason_msg?: ServerMsg | null; line: number | null; winner?: boolean }[]; winner: string | null };
  final: { category: string; source: string; tags: string[]; event: string | null; before_memory: { category: string; source: string } };
  consistent: boolean;
  override: { category: string; note: string | null } | null;
  transfer_link: { id: number; out: string; in: string; method: string; confidence: number } | null;
  same_merchant_count: number;
  item: TxItem | null;
}
export interface CategoryChangePreview {
  dry_run: boolean; scope: string; category: string; changed: boolean; diff: string; warnings: string[]; warnings_msg?: (ServerMsg | null)[]; id?: string; change_id?: string | null;
  affected: { count: number; total: Money; from_categories?: { category: string; n: number }[]; blocked?: { reason: string; n: number; kind?: string; value?: string }[]; samples?: { merchant: string; count: number; total: number }[];
    already?: number; matched?: number; tag_changes?: number; applies_to?: number; shadowed?: number; date_min?: string | null; date_max?: string | null; merchant_key?: string; warnings?: string[]; warnings_msg?: (ServerMsg | null)[]; max_account_share?: number };
}
export interface EditResult { dry_run: boolean; changed: boolean; diff: string; change_id: string | null; warnings: string[]; warnings_msg?: (ServerMsg | null)[]; id?: string; file?: string; affected?: CategoryChangePreview["affected"] }

export interface CategoryRow { category: string; group: string; monthly_avg: Money | null; monthly_avg_with_one_offs: Money | null; n_months: number; low_confidence: boolean; lumpy: boolean; this_month: Money; last_month: Money; accounts: string[] }
export interface GroupRow { group: string; monthly_avg: Money | null; n_months: number; low_confidence: boolean; this_month: Money; last_month: Money }
export interface CategoryOverview { as_of: string; categories: CategoryRow[]; groups: GroupRow[]; unavailable: { category: string; reason: string }[]; household_monthly_avg: Money | null; household_months: string[]; coverage: Coverage }
export interface CategoryDetail {
  id: string; is_group: boolean; kind: "spending" | "income" | "transfer"; description: string | null; leaves: { id: string; description: string }[]; group: string; as_of: string;
  series: { month: string; total: Money; run_rate: Money; one_off: Money; n_tx: number; covered: boolean; partial: boolean }[];
  average: { monthly: Money; monthly_with_one_offs: Money; n_months: number; months: string[]; accounts: string[]; ignored_accounts: string[]; lumpy: boolean; low_confidence: boolean } | null;
  trend: { recent_avg: Money; prior_avg: Money; delta: Money; pct: number | null; recent_months: string[]; prior_months: string[] } | null;
  entities: { entity: string; total: Money; one_off: Money; n_tx: number; last_date: string; share: number; accounts: string[]; categories: string[] }[];
  one_offs: { tx_key: string; date: string; amount: Money; entity: string; category: string; tags: string[]; event: string | null; account: string }[];
  notes: string[]; notes_msg?: (ServerMsg | null)[]; coverage: Coverage; totals: { last_12_months: Money; n_tx: number };
}

export interface PriceChange { id: string; series_id: string; entity: string; account_label: string; category: string; cadence: string; date: string; tx_key: string; old: Money; new: Money; delta: Money; pct: number; direction: "increase" | "decrease"; effect: string; yearly_impact: Money; confirmed: boolean; variable: boolean; dismissed: boolean }
export interface Series {
  id: string; direction: string; kind: string; key: string; entity: string; account: string; account_label: string; category: string; cadence: string; amount_mode: string;
  n_occurrences: number; first_date: string; last_date: string; next_expected: string | null; expected_amount: Money; amount_low: Money; amount_high: Money; yearly_cost: Money;
  status: "active" | "ended"; overdue_days: number; confidence: number; confidence_label: string; contract_candidate: boolean; missing_contract: boolean; tags: string[];
  price_steps: number; occurrences: { tx_key: string; date: string; amount: Money }[]; monthly_cost: Money; price_changes: PriceChange[];
  linked: { kind: string; id: string; name: string; detail: string | null; renewal: string | null; keep: boolean | string | null; commitment_end: string | null; share: number }[];
}
export interface Subscriptions { as_of: string; series: Series[]; totals: { active_monthly: Money; active_yearly: Money; n_active: number; n_missing_contract: number }; by_cadence: Record<string, number>; coverage: Coverage }

export interface ScheduleYear { year: number; instalments: number; interest: Money; principal: Money; insurance: Money; total: Money; partial: boolean }
export interface ScheduleRow { k: number; due: string; kind: "regular" | "deferral"; interest: Money; principal: Money; insurance: Money; payment: Money; total: Money; balance: Money; made: boolean }
export interface LoanScheduleSummary {
  status: "computed" | "not_computable" | "not_applicable" | "invalid"; mode?: "from_principal" | "from_outstanding" | null; missing?: string[]; missing_msg?: ServerMsg[];
  alternative?: string | null; alternative_msg?: ServerMsg | null; approximate?: boolean;
  payment?: Money | null; remaining_capital?: Money | null; next_due?: string | null; first_due?: string | null; last_due?: string | null; remaining_instalments?: number; payments_made?: number;
  term_instalments?: number | null; total_interest?: Money | null; total_insurance?: Money | null; total_cost?: Money | null; interest_paid?: Money | null; remaining_interest?: Money | null;
  payment_check?: { status: string; hint?: string; hint_msg?: ServerMsg } | null; outstanding_check?: { status: string; declared: Money; computed: Money; as_of: string; hint: string; hint_msg?: ServerMsg } | null;
  assumptions?: string[]; assumptions_msg?: ServerMsg[]; by_year: ScheduleYear[]; rows?: ScheduleRow[]; rows_count?: number;
}
export interface LoanAlert { id: string; type: "missed_payment" | "amount_changed" | "extra_payment" | "wrong_account"; severity: "high" | "medium" | "low"; loan: string; title: string; body: string; title_msg?: ServerMsg | null; body_msg?: ServerMsg | null; date: string; amount: Money | null; expected_date: string | null; expected_amount: Money | null; evidence: string[] }
export interface InferredField { field: string; value: string | number; confidence: "low" | "medium" | "high"; method: string; method_msg?: ServerMsg; note?: string | null; note_msg?: ServerMsg | null }
export interface LeaseStatus {
  id: string; kind: string; missing: string[]; checklist: string[]; checklist_msg?: ServerMsg[];
  end: { end_date: string | null; known: boolean; days_left?: number; ended?: boolean; reminder_date?: string; reminder_active?: boolean; reminder_months?: number };
  decision: { residual_value: Money | null; needs?: string[]; needs_msg?: ServerMsg[]; note?: string; note_msg?: ServerMsg; market_value?: Money; market_minus_option_price?: Money; reading?: string; reading_msg?: ServerMsg;
    other_factors?: string[]; other_factors_msg?: ServerMsg[] };
  mileage: { limit_km: number | null; excess_km_fee: Money | null; readings: number; status: string; needs: string[]; needs_msg?: ServerMsg[]; latest?: { date: string; km: number; age_days: number }; pace?: { km_per_year: number; km_per_month: number; since: string };
    projected_odometer_at_end?: number; projected_contract_km?: number; excess_km?: number; excess_cost?: Money; allowed_km_per_year?: number; basis?: string; basis_msg?: ServerMsg };
}
export interface Liability {
  id: string; file: string; kind: string; lender: string | null; asset: string | null; holder?: string | null; start_date: string | null; end_date: string | null; first_payment_date?: string | null;
  term_months?: number | null; payment_day?: number | null; principal: Money | null;
  outstanding: Money | null; outstanding_as_of: string | null; outstanding_stale: boolean;
  rate: { type: string | null; nominal: number | null; taeg: number | null; index?: string | null; margin?: number | null; cap?: number | null } | null;
  monthly_payment: Money | null; insurance: { provider: string | null; monthly: Money | null; delegated?: boolean | null; rate_pct?: number | null; basis?: string | null } | null;
  deferral?: { months: number; kind: "partial" | "total" } | null; debited_account: string | null; debited_account_label: string | null;
  payment_match: string | null; early_repayment_penalty: string | number | null; first_payment?: Money | null; residual_value: Money | null; mileage_limit_km: number | null; excess_km_fee?: Money | null; initial_km?: number | null;
  odometer?: { date: string; km: number }[]; notes: string | null;
  /** English labels of the missing fields; `missing_codes` (same order) are the codes the web translates (labels.loanField). */
  missing: string[]; missing_codes?: string[]; open_questions: string[]; payments: { series_id: string; last_date: string; next_expected: string | null; amount: Money; status: string } | null;
  schedule: LoanScheduleSummary; remaining_capital: Money | null; remaining_capital_source: "schedule" | "declared" | null; alerts: LoanAlert[]; payments_seen: number; lease: LeaseStatus | null; inferred: InferredField[];
}
export interface LoanDetail {
  id: string; kind: string; schedule: LoanScheduleSummary; lease: LeaseStatus | null; alerts: LoanAlert[];
  payments: { count: number; first: string | null; last: string | null; last_amount: Money | null; median_amount: Money | null; recent: { date: string; amount: Money; account: string }[] };
  inference: { status: string; fields?: InferredField[]; notes?: string[]; notes_msg?: ServerMsg[]; missing?: string[]; missing_msg?: ServerMsg[] };
}
export interface ScenarioOption { mode: "keep_payment" | "keep_term"; label: string; new_instalment: Money; monthly_change: Money; months_saved: number; new_last_instalment: string | null; interest_saved: Money; insurance_saved: Money; penalty: Money; net_saving: Money; break_even_months: number | null; verdict: string; verdict_code?: string }
export interface ScenarioResult {
  status: "computed" | "needs_fields" | "payoff" | "nothing_left"; missing?: string[]; missing_msg?: ServerMsg[]; alternative?: string | null; alternative_msg?: ServerMsg | null; note?: string; note_msg?: ServerMsg;
  // prepay
  date?: string; amount?: Money; capital_before?: Money; capital_after?: Money; remaining_instalments?: number; instalment?: Money; penalty?: Money; penalty_basis?: string; penalty_basis_msg?: ServerMsg; options?: ScenarioOption[]; notes?: string[]; notes_msg?: ServerMsg[];
  /** a key of GET /meta/disclaimers (the wording lives in the server's disclaimers.py) */
  disclaimer_key?: "loan";
  // renegotiate / insurance
  variant?: string; country?: string; current_rate_pct?: number; new_rate_pct?: number; current_payment?: Money; new_payment?: Money; monthly_saving?: Money; gross_interest_saving?: Money; total_costs?: Money; net_saving?: Money;
  break_even_months?: number | null; verdict?: string; current_monthly?: Money; alternative_monthly?: Money; remaining_months?: number; total_saving?: Money; basis?: Record<string, unknown>;
}
export interface Asset { id: string; kind: string; provider: string | null; holder: string | null; value: Money | null; as_of: string | null; stale: boolean; unknown_value: boolean; liquidity: string | null; connected: boolean | null; contribution_monthly: Money | null; description: string | null; counted?: boolean; note?: string; note_msg?: ServerMsg }
export type NwCategory = "cash" | "savings" | "investments" | "real_estate" | "vehicles" | "other";
export interface NetWorthPoint { month: string; as_of: string; source: "snapshot" | "backfill"; net_worth: Money; assets: Money; liabilities: Money; by_category: Record<NwCategory, Money>; n_unknown: number; complete: boolean; unknown: { type: string; id: string; label: string; reason: string; reason_msg?: ServerMsg }[];
  newly_counted?: { type: string; id: string; label: string; reason_before: string; reason_before_msg?: ServerMsg | null }[]; non_booked_accounts?: number; caveat?: string | null; caveat_msg?: ServerMsg | null }
export interface NetWorth {
  as_of: string; net_worth: Money; complete: boolean; unknown: { kind: string; id: string; label: string; reason: string; reason_msg?: ServerMsg | null }[]; stale: { kind: string; id: string }[];
  bank: { total: Money; accounts: { uid: string; label: string; bank: string | null; owner: string | null; purpose: string | null; balance: Money | null; as_of: string | null; balance_type?: string | null; stale?: boolean }[] };
  assets: { total: Money; items: (Asset & { category?: NwCategory })[]; connected_not_counted: number };
  liabilities: { total: Money; items: { id: string; kind: string; lender: string | null; outstanding: Money | null; as_of: string | null; stale: boolean; counted: boolean; source?: string | null; excluded?: boolean; note?: string | null; note_msg?: ServerMsg | null }[] };
  by_category: Record<NwCategory | "liabilities", Money>; by_owner: Record<string, { assets: Money; liabilities: Money; net_worth: Money; n_unknown: number }>; n_unknown: number;
  history?: NetWorthPoint[]; note: string; note_msg?: ServerMsg;
}

export interface CalendarItem { date: string; days_until: number; source: string; kind: string; title: string; amount: Money | null; ref: string; certainty: string; account_label: string | null; note: string | null }
export interface CalendarResult { as_of: string; days: number; items: CalendarItem[]; counts: Record<string, number>; month?: string; coverage: Coverage }

export interface Anomaly { id: string; type: string; severity: string; subject: string; period: string; amount: Money; baseline: Money | null; message: string; message_msg?: ServerMsg | null; evidence: string[]; accounts: string[]; dismissed: boolean }
export interface InsightCard { id: string; kind: "anomaly" | "price_change" | "forecast" | "budget" | "subscription" | "loan" | "rental"; subtype: string; severity: "high" | "medium" | "low"; title: string; body: string; title_msg?: ServerMsg | null; body_msg?: ServerMsg | null; disclaimer?: "tax_short"; amount: Money | null; date: string | null; subject: string; evidence: string[]; persist: string; snoozed_until?: string | null }
export interface CoachInsight { id: string; created: string; kind: string; title: string; body: string; findings: unknown[]; evidence: string[]; skill: string | null; backend: string | null; model: string | null; usage_ref: number | null; status: "new" | "read" | "dismissed" | "done" | "snoozed"; snoozed_until: string | null; unverified_numbers: string[]; suspicious: boolean; question: string | null; ai_generated?: boolean; ai_label?: string | null; ai_label_short?: string | null; compliance?: string[]; compliance_banner?: string | null }
/** E11-5: the AI-generated label and the investment-advice check of a coach answer. */
/** GET /meta/disclaimers: the legal labels in one language (their wording lives only in src/coach/disclaimers.py). */
export interface Disclaimers { lang: string; texts: { ai_label: string; ai_label_short: string; contract?: string; contract_verify?: string; general_advice?: string; loan?: string; tax?: string; tax_short?: string } }
/** A key of GET /meta/disclaimers (the wording lives only in src/coach/disclaimers.py). */
export type DisclaimerKey = keyof Disclaimers["texts"];
export interface Compliance { label: string; label_short: string; lang: string; flagged: boolean; codes: string[]; banner: string }
export interface Insights { as_of: string; cards: InsightCard[]; hidden: number; alerts?: { open: number; high: number }; counts: Record<string, number>; coach: { configured: boolean; items: CoachInsight[]; hidden: number; message: string } }
export interface CoachStatus { configured: boolean; backend: string; model: string; message: string; max_tool_calls: number; timeout_seconds: number; busy: boolean; current_job: string | null; tools: string[] }
export interface ResolvedRef { kind: "transaction" | "series" | "anomaly" | "price_change"; tx_key?: string; date?: string; amount?: string; category?: string; merchant?: string; link?: string }

export interface Question { id: string; status: "open" | "answered" | "dismissed"; topic: string; question: string; context?: string; evidence?: Record<string, unknown>; suggested_target?: { file: string; field?: string }; created?: string; answered?: string; answer?: string; note?: string; stake?: Money; origin: string }
export interface Questions { questions: Question[]; counts: Record<string, number> }
export interface Member { id: string; name: string; role: "adult" | "child"; birth_year: number | null; aliases: string[] }
export interface Proposal {
  id: string; accept_command: string; status: string; created: string; file: string; reason: string; source: string; sealed: boolean; applicable: boolean; error: string | null;
  diff: string; changes: { op: string; path: string | null; old: unknown; new: unknown; snippet?: string | null; suspicious?: boolean; conflicts_with?: unknown }[]; suspicious_paths: string[];
}
export interface MemoryOverview { files: string[]; history_enabled: boolean; counts: Record<string, number>; check: { errors: number; warnings: number; info: number; by_code: Record<string, number> } }
export interface CheckIssue { level: "error" | "warning" | "info"; code: string; file: string; path: string; message: string; message_msg?: ServerMsg | null; line?: number }
export interface Change { id: string; date: string; subject: string; files: string[]; reason: string | null; source: string | null }
export interface Annotation { id: string; category?: string; tags?: string[]; event?: string; note?: string; match: Record<string, unknown>; matched: number; applies_to: number; total: Money }

export interface ConnAccount { uid: string; bank: string | null; label: string | null; name: string | null; iban_last4: string; currency: string | null; owner: string | null; purpose: string | null; excluded: boolean; source: string; session_status: string | null; needs_review: boolean; tx_count: number; syncs_left_today: number | null }
export interface Consent { session_id: string; bank: string; country: string; valid_until: string | null; days_left: number | null; status: string; live_status: string | null; live_checked_at: string | null; accounts: number }
export interface HealthAccount { uid: string; label: string; bank: string; source: string; level: "green" | "amber" | "red"; problems: string[]; last_ok_sync: string | null; syncs_today: number; daily_limit: number; syncs_left_today: number; consent_days_left: number | null; tx_count: number; stale: boolean; excluded: boolean; last_import: string | null }
export interface HealthBank { bank: string; country: string; session_id: string | null; consent_status: string | null; consent_days_left: number | null; valid_until: string | null; level: "green" | "amber" | "red"; accounts: HealthAccount[] }
export interface RunStep { step: string; status: "ok" | "warn" | "error" | "skipped"; ms: number; counts: Record<string, number>; error?: string | null }
export interface LastRun { run: string; started: string | null; ended: string | null; outcome: "ok" | "failed" | "running"; duration_s: number | null; steps: RunStep[]; failed: string[]; warned: string[] }
export interface Health { generated_at: string; banks: HealthBank[]; ok: boolean; level: "green" | "amber" | "red"; memory?: { errors: number; warnings: number; info: number }; last_run?: LastRun | null }

/* ------------------------------------------------------------------ quality (E12): the gold set and the LLM usage */
export interface GoldItem { tx_key: string; date: string; amount: number; description: string; account: string; merchant_key: string; merchant: string | null; category: string; source: string; confidence: number | null; type: string | null }
export interface EvalRun { id: number; ts: string; kind: "classify" | "models" | "coach"; label: string | null; backend: string | null; model: string | null; n: number; cost_usd: number | null; duration_s: number | null; summary: Record<string, number | string | null> }
export interface GoldCounts { total: number; by_origin: Record<string, number>; by_labeled_by: Record<string, number>; categories: number }
export interface GoldSummary { counts: GoldCounts; latest: EvalRun | null; runs: EvalRun[]; sample: GoldItem[]; strategy: "money" | "stratified"; seed: number }
export interface UsageLine { job: string; purpose: string; backend: string; model: string | null; calls: number; tokens_in: number; tokens_out: number; cache_read_tokens: number; cache_write_tokens: number; cost_usd: number | null; cost_unknown_calls: number; notional: boolean; duration_s: number; avg_duration_s: number }
export interface UsageDay { date: string; calls: number; tokens_in: number; tokens_out: number; cost_usd: number }
export interface UsageDestination { kind: string; host: string; purpose: string; calls: number; bytes: number; denied: number; web: boolean; last: string }
export interface UsageMonth { month: string; cost_usd: number; threshold_usd: number | null; ratio: number | null; level: "medium" | "high" | null; unknown_cost_calls: number; include_notional: boolean }
export interface UsageSummary {
  days: number; lines: UsageLine[]; by_job: { job: string; calls: number; tokens_in: number; tokens_out: number; cost_usd: number; duration_s: number }[]; by_day: UsageDay[];
  destinations: UsageDestination[]; journal_available: boolean; month: UsageMonth; note: string;
  totals: { calls: number; tokens_in: number; tokens_out: number; cost_usd: number; notional_cost_usd: number; estimated_cost_usd: number; unknown_cost_calls: number; duration_s: number };
}
export interface JobState { kind: string; state: "idle" | "running" | "done" | "failed"; started_at: string | null; finished_at: string | null; message: string | null; results: { uid: string; bank?: string; status: string; new?: number; note?: string }[]; log: string[]; url: string | null }
export interface Connections {
  accounts: ConnAccount[]; consents: Consent[]; health: Health; purposes: string[]; sync: JobState & { daily_limit: number }; enable_banking_configured: boolean; connect: JobState;
}
export interface Transfers { links: { id: number; amount: Money; confidence: number; method: string; out_account: string; in_account: string; out_date: string; in_date: string; out_key: string; in_key: string }[]; proposals: { debit: { tx_key: string; date: string; amount: Money; account: string; description: string }; credit: { tx_key: string; date: string; amount: Money; account: string; description: string }; confidence: number; days: number; topup: boolean }[]; ambiguous: number }

export interface OnboardingStep {
  id: "household" | "accounts" | "loans" | "contracts" | "preferences" | "budgets" | "questions";
  heading: string;
  status: "done" | "partial" | "todo";
  have: Record<string, unknown>;
  missing?: (string | { account: string; label?: string; missing: string[] })[];
  liabilities?: { id: string; kind: string; missing: string[]; matched_in_bank_data: boolean }[];
  loan_payments_without_file?: string[];
  recurring_without_contract?: string[];
  contracts_with_empty_fields?: { id: string; missing: string[] }[];
}
/** E13-2: the first-run wizard (`coach setup`), read-only: the steps run in a terminal, with a typed consent before anything leaves the machine. */
export interface WizardStep { id: string; title: string; status: "done" | "partial" | "todo" | "skipped" | "blocked"; detail: string; command: string; optional: boolean }
export interface WizardStatus { steps: WizardStep[]; progress: { done: number; total: number; next_step: string | null }; container: boolean; command: string; commands: { setup: string; enablebanking: string } }

export interface Onboarding {
  as_of: string;
  progress: { done: number; total: number; next_step: string | null };
  steps: OnboardingStep[];
  next_actions: { step: string; do: string; command: string }[];
  how: string[];
  note: string;
  declared?: { employers: string[]; places: string[]; schools: string[] };
}

/* ------------------------------------------------------------------ E8: subscriptions & contracts optimizer */
export type UsageFrequency = "daily" | "weekly" | "monthly" | "rarely" | "never" | "unknown";
export interface LegalRule { id: string; name: string; law: string; source: string; last_reviewed: string; name_msg?: ServerMsg }
export interface InvCancellation {
  country: string; family: string; can_cancel_now: boolean | null; earliest_effective_date: string | null; notice_period_days: number | null; method: string;
  conditions: string[]; unknown: string[]; legal_basis: LegalRule[]; last_reviewed: string | null; verify: string; disclaimer: string;
  /** i18n 4e: the sentences as codes (docs/i18n.md "Server text"); the two disclaimers by key (`useDisclaimers()`). */
  method_msg?: ServerMsg; conditions_msg?: ServerMsg[]; unknown_msg?: ServerMsg[]; disclaimer_key?: "contract"; verify_key?: "contract_verify";
  early_termination_cost: { basis: string; share_pct: number | null; remaining_months: number; free_exit_date: string; amount: number | null; needs?: string } | null;
  anniversary_route: { effective: string; send_notice_by: string; months_notice: number; notice_still_possible: boolean } | null;
  contract_notice_deadline: { renewal: string; send_notice_by: string; notice_period_days: number; days_left: number } | null;
  first_request_date: string | null; send_notice_by: string | null;
}
export interface InvAlternative {
  id: string; provider: string; offer: string; monthly_price: Money; switching_costs: Money; features: string | null; source_url: string | null; retrieved_at: string;
  age_days: number; method: string; source: string; status: "current" | "outdated"; label: string; stale: boolean;
  savings: { monthly: Money; yearly: Money; net_12m: Money; break_even_months: number | null; verdict: string; computed_by: string; stale_warning: string | null; stale_warning_msg?: ServerMsg } | null;
}
export interface InvDecision {
  id: string; decision: string; decided_on: string; effective_on: string; before_monthly: Money; after_monthly: Money; monthly_saving: Money; months_counted: number;
  since_decision: Money; note: string | null; source: string; status: "verified" | "pending" | "contradicted" | "not_applicable" | "ambiguous"; reason: string; reason_msg?: ServerMsg; check_on: string | null; reminder: boolean;
}
export interface InvRow {
  ref: string; name: string; entity: string; group: string; group_label: string; category: string | null; kind: string; cost_source: "series" | "contract" | "unknown";
  cadence: string | null; expected_amount: Money | null; monthly: Money | null; yearly: Money | null; status: "active" | "ended" | "contract_only"; first_seen: string | null;
  last_payment: string | null; next_charge: string | null; overdue_days: number; account: string | null;
  price_history: { from: string; amount: Money }[];
  price_changes: { id: string; date: string; old: Money; new: Money; direction: string; pct: number; yearly_impact: Money; confirmed: boolean }[];
  contract: { status: "on_file" | "missing" | "expired"; id: string | null; provider?: string | null; renewal?: string | null; commitment_end?: string | null; notice_period_days?: number | null; expired_on?: string | null; keep?: boolean | string | null; contract_number_on_file?: boolean };
  series_id: string | null; series: string[]; contract_id: string | null; linked_series: string[]; draftable: boolean;
  usage: { frequency: UsageFrequency; last_used: string | null; note: string | null; recorded: boolean; measurable: boolean; question_asked: boolean; note_not_measurable: string | null; note_not_measurable_msg?: ServerMsg | null;
           signals: { kind: "unused_60_days" | "paid_but_never_used"; days?: number; last_used?: string; last_payment?: string; measurable: string; measurable_msg?: ServerMsg }[] };
  cancellation: InvCancellation;
  alternatives: { count: number; current: number; outdated: number; best: InvAlternative | null; items: InvAlternative[]; note: string; note_msg?: ServerMsg };
  decision: InvDecision | null; proposed_decisions: string[];
}
export interface SavingsSummary { realised_monthly: Money; realised_since_decisions: Money; realised_yearly_run_rate: Money; verified: number; pending: number; contradicted: number; claimed_monthly_unverified: Money }
export interface Inventory {
  as_of: string; country: string; rows: InvRow[]; groups_meta: { id: string; label: string }[];
  groups: Record<string, { label: string; count: number; monthly: Money; yearly: Money; without_contract: number; unknown_cost: number }>;
  totals: { services: number; monthly: Money; yearly: Money; without_contract: number; expired_contracts: number; with_cancellation_rule: number; cancellation_decidable: number; usage_unknown: number; outdated_alternatives: number; reminders: number };
  savings: SavingsSummary; notes: string[]; notes_msg?: ServerMsg[];
}
export interface SavingsView extends SavingsSummary {
  as_of: string; decisions: (InvDecision & { contract: string | null; series: string | null; name: string | null; state: string })[];
  proposed: { id: string; decision: string; name: string | null; source: string; before_monthly: number; after_monthly: number; note: string | null }[]; reminders: string[]; note: string; note_msg?: ServerMsg;
}
export interface Letter {
  contract: string; lang: "fr" | "it" | "en"; channel: "lrar" | "email" | "online"; subject: string; text: string; filename: string; placeholders: string[];
  legal_basis: LegalRule[]; can_cancel_now: boolean | null; send_on_or_after: string | null; send_by: string | null; notes: string[]; pdf: null; pdf_note: string; sent: false; country: string;
}
export interface ContactInfo { contact: { address?: string; email?: string; phone?: string }; set: boolean; local_only: boolean; members: { id: string; name: string }[] }
export interface DraftPreview extends EditResult { contract: { series: string; contract_id: string; file: string; kind: string; provider: string; missing: string[]; yearly: Money; warnings: string[]; warnings_msg?: ServerMsg[]; value: Record<string, unknown> } }

/* ------------------------------------------------------------------ alerts (E10) */
export type AlertSeverity = "high" | "medium" | "low";
export type AlertStatus = "new" | "sent" | "acked" | "snoozed" | "suppressed";
export interface AlertEvent { id: string; kind: string; severity: AlertSeverity; created: string; updated: string; last_seen: string; resolved: boolean; resolved_at: string | null; title: string; body: string; title_msg?: ServerMsg | null; body_msg?: ServerMsg | null; payload: Record<string, unknown>; status: AlertStatus; snoozed_until: string | null; acked_at: string | null; channels_sent: Record<string, { at: string; severity: string }>; escalations: number }
export interface AlertKindRow { kind: string; label: string; muted: boolean; snoozed_until: string | null; disabled_in_config: boolean; digest_only: boolean }
export interface AlertCounts { open: number; new: number; high: number; snoozed?: number; acked?: number; suppressed?: number }
export interface Alerts { ready: boolean; enabled?: boolean; items: AlertEvent[]; counts: AlertCounts; kinds: AlertKindRow[]; settings?: { min_severity: string; external_detail: string; quiet_hours: string; max_per_week: number }; message?: string }
export interface AlertSummary { ready: boolean; open: number; new: number; high: number }
export interface AlertChannel { channel: "macos" | "ntfy" | "email" | "telegram"; enabled: boolean; ready: boolean; problems: string[]; external: boolean; target: string; sent_last_7_days: number }
export interface AlertChannels { in_app: { enabled: boolean; note: string }; channels: AlertChannel[]; external_detail: string; min_severity: string; quiet_hours: string; max_per_week: number; weekly_digest: boolean; weekly_digest_to_channels: boolean; recent_deliveries: { sent_at: string; channel: string; what: string; ok: boolean; error: string | null }[]; how_to_enable: string }
export interface AlertChannelTest { dry_run: true; sent: false; channel: string; enabled: boolean; ready: boolean; problems: string[]; detail: string; note: string | null; message: { title: string; body: string }; text: string; sample: string }
export interface AlertDigest { week_start: string; week_end: string; markdown: string }

/* ------------------------------------------------------------------ household & people (E14) */
export interface UserInfo { id: string; role: "adult" | "child"; member_id: string | null; created_at: string | null; disabled: boolean; prefs: Record<string, string> }
export interface HouseholdMember { id: string; name: string; role: "adult" | "child"; birth_year: number | null; aliases: string[]; pocket_money: { amount: number; period: "weekly" | "monthly"; day?: number } | null; accounts: string[]; attributed_transactions: number }
export interface HouseholdAccount { uid: string; label: string; bank: string | null; owner: string | null; owner_member: string | null; joint: boolean; owner_known: boolean; purpose: string | null; attributed: Record<string, number> }
export interface AttributionRule { id: string; member: string; match: { account?: string; card_last4?: string; merchant_key?: string; description?: string; direction?: "in" | "out"; amount_min?: number; amount_max?: number }; note?: string }
export interface KidBudgetDef { id: string; member: string; period: "weekly" | "monthly"; limit: number; category?: string; group?: string; note?: string }
export interface AllocationDef { id: string; title?: string; match: Record<string, string>; method: "equal" | "income" | "custom"; among?: string[]; shares?: Record<string, number>; note?: string }
export interface HouseholdOverview {
  as_of: string; members: HouseholdMember[]; accounts: HouseholdAccount[]; purposes: string[]; attribution: { counts: Record<string, number>; manual: number };
  rules: AttributionRule[]; kid_budgets: KidBudgetDef[]; allocations: AllocationDef[]; users: UserInfo[]; warnings: string[];
}
export interface PocketSeries { id: string; source: string; amount: Money; cadence: string; count: number; first: string; last: string; next_expected: string; day: number }
export interface KidReport {
  member: string; as_of: string; window: { months: string[]; from: string; to: string };
  accounts: { account: string; label: string; balance: Money | null; balance_as_of: string | null }[];
  balance: { current: Money | null; as_of: string | null; trend: { month: string; end_balance: Money }[]; unknown: string[]; estimated: boolean };
  pocket_money: { series: PocketSeries[]; declared: { amount: Money; period: string; day: number | null; matches: number } | null; total: Money; monthly_equivalent: Money };
  extra_topups: { total: Money; count: number; by_source: Record<string, Money>; items: { tx_key?: string; date: string; amount: Money; source: string; linked: boolean; category: string }[] };
  inflow: { total: Money; pocket: Money; extra: Money };
  ratio: { pocket_share: number | null; extra_share: number | null; pocket_to_extra: number | null };
  spending: { total: Money; monthly_avg: Money; this_month_to_date: Money; by_category: { category: string; total: Money; n: number; share: number | null }[]; by_month: { month: string; total: Money }[] };
  notes: string[];
}
export interface KidBudgetStatus { id: string; member?: string; period: "weekly" | "monthly"; category: string | null; group: string | null; limit: Money; spent: Money; remaining: Money; ratio: number; status: "ok" | "at_risk" | "over"; period_start: string; period_end: string; days_left: number; projected: Money; note: string | null }
export interface AllocationRuleResult { id: string; title: string | null; method: string; among: string[]; members: { member: string; share_pct: number; owed: Money; paid: Money; joint_share: Money; net: Money }[]; total: Money; joint_paid: Money; personal_paid: Money; unattributed: Money; n: number; notes: string[] }
export interface AllocationReport { as_of: string; window: { months: string[] }; rules: AllocationRuleResult[]; by_member: { member: string; owed: Money; paid: Money; net: Money }[]; notes: string[] }
export interface AttributionWhy {
  tx_key: string; person: string | null; source: "manual" | "rule" | "account" | "none"; rule: string | null; reason: string;
  manual: { member: string; set_at: string; set_by: string; note: string | null } | null;
  rules: { id: string; member: string; matched: boolean; why_not: string | null; why_not_msg?: ServerMsg | null }[];
  account: string | null; account_owner: string | null; account_owner_member: string | null; card_last4: string | null;
  history: { id: number; action: string; old_member: string | null; new_member: string | null; at: string; by: string }[];
}
export interface MeSummary { member: string; name: string; report: Omit<KidReport, "accounts">; budgets: KidBudgetStatus[] }
export interface MeTransactions { total: number; limit: number; offset: number; items: { date: string; amount: Money; category: string; merchant: string }[] }
export interface AuditRows { requests: { at: string; actor: string; method: string; path: string; status: number }[]; memory: { id: string; date: string; source: string; subject: string; files: string[] }[] }

/* ------------------------------------------------------------------ E15: rental property under a tax-incentive scheme */
export interface RentalMonth {
  month: string; rent: Money; loan: Money; charges: Money; fees: Money; taxes: Money; insurance: Money; works: Money; other: Money; costs: Money; net: Money; effort: Money;
  owner_in: Money; owner_out: Money; complete: boolean; rent_status: "received" | "partial" | "late_paid" | "declared_vacancy" | "missing" | "unknown" | "not_let_yet";
  expected_rent: Money | null; rent_evidence: string[]; n_tx: number;
}
export interface RentalVacancy { missing_months: string[]; declared_months: string[]; late_paid_months: string[]; unknown_months: string[]; n_missing: number; n_declared: number; months_since_let: number; occupancy_rate: number | null }
export interface RentalCashflow {
  months: RentalMonth[]; rent: { expected: Money | null; source: "declared" | "observed_median" | "unknown"; first_month: string | null; usual_day: number | null };
  n_months: number; n_complete: number; average?: { rent: Money; costs: Money; net: Money; effort: Money }; vacancy: RentalVacancy;
}
export interface RentalPnl {
  year: number; totals: Record<"rent" | "loan" | "charges" | "fees" | "taxes" | "insurance" | "works" | "other" | "costs" | "net" | "effort" | "owner_in" | "owner_out", Money>;
  n_months: number; months_expected: number; months_incomplete: string[]; months_missing_data: string[]; year_in_progress: boolean; complete: boolean;
  monthly_average_effort: Money | null; vacancy: RentalVacancy; loan_split: { interest: Money; insurance: Money; principal: Money; partial: boolean; source: string } | null;
  economic?: { result: Money; note: string }; gross_yield_pct?: number;
}
export interface RentalScheme {
  declared: boolean; scheme: string | null; start_date: string | null; years: number | null; end_date: string | null; end_source: string | null; effective_end_date: string | null;
  state: "unknown" | "not_started" | "active" | "ended"; progress_pct?: number; days_left?: number; months_left?: number; decision_needed?: boolean; next_reminder_date?: string | null;
  reminders?: { months_before: number; date: string }[]; warnings?: string[]; warnings_msg?: ServerMsgs; end_source_msg?: ServerMsg;
  extension: { decision: "undecided" | "extend" | "not_extend"; years?: number | null; additional_rate_pct?: number | null; decided_on?: string | null };
  rent_cap: { cap: Money | null; cap_basis: string | null; rent: Money | null; rent_basis: string | null; status: "unknown" | "within_cap" | "above_cap"; gap?: Money; cap_basis_msg?: ServerMsg | null; rent_basis_msg?: ServerMsg | null };
  tenant_income: { limit: Money | null; tenant_income: Money | null; status: "unknown" | "within_limit" | "above_limit"; headroom?: Money };
  missing: { field: string; needed_for: string; needed_for_msg?: ServerMsg }[];
}
/** A list of server sentences' messages, in the order of their English list (an entry may be null: no message, the English is shown). */
export type ServerMsgs = (ServerMsg | null)[];
/** A reading of the indicators: a server sentence followed, when `disclaimer` is set, by that disclaimer (GET /meta/disclaimers). */
export type ReadingMsg = ServerMsg & { disclaimer?: DisclaimerKey };
export interface RentalAsset {
  id: string; kind: string; account: string | null; loan: string | null; scheme: string | null; value: number | null; as_of: string | null; rent_monthly: number | null;
  purchase_price: number | null; purchase_date: string | null; commitment: Record<string, unknown> | null; market_rate: { rate_pct: number; as_of?: string; source?: string } | null;
  vacancies: { start: string; end?: string; note?: string }[];
}
export interface RentalDetail {
  id: string; kind: string; links: { account: string; accounts: number; loan: string; loans: number; notes: string[]; notes_msg?: ServerMsgs }; cashflow: RentalCashflow;
  current_month: { month: string; rent: Money; expected: Money | null; status: string }; pnl: RentalPnl; scheme: RentalScheme; years: number[]; flows_to_label: number; asset: RentalAsset; tag: string;
}
export interface RentalRow {
  id: string; account_link: string; loan_link: string; n_loans: number; expected_rent: Money | null; rent_source: string;
  last_month: { month: string; rent: Money; net: Money; effort: Money; complete: boolean; rent_status: string } | null; vacancy: RentalVacancy;
  scheme: { declared: boolean; scheme: string | null; state: string; end_date: string | null; effective_end_date: string | null; months_left: number | null; decision_needed: boolean | null };
  net_equity: Money | null; missing: string[];
}
export interface RentalList { as_of: string; properties: RentalRow[]; unlinked_rental_accounts: { uid: string; label: string }[]; property_categories: string[] }
export interface RentalTax {
  year: number; country: string; status: string; note?: string; note_msg?: ServerMsg; disclaimer: string; disclaimer_key?: DisclaimerKey; income_year_note?: string; income_year_note_msg?: ServerMsg;
  gross_rents?: Money; months_counted?: number; months_incomplete?: string[]; months_missing_data?: string[]; complete?: boolean;
  micro_foncier?: { gross_rents: Money; household_gross_rents: Money; ceiling: Money; within_ceiling: boolean; abatement_pct: number; abatement: Money; taxable: Money };
  reel?: { gross_rents: Money; deductible: { item: string; amount: Money; source: string; bound: string; item_msg?: ServerMsg; source_msg?: ServerMsg; bound_msg?: ServerMsg }[]; total_deductible: Money; net: Money;
    unknown: string[]; unknown_msg?: ServerMsgs; not_deductible: string[]; not_deductible_msg?: ServerMsgs; net_is_upper_bound?: boolean };
  difference?: Money; lower_taxable_candidate?: "reel" | "micro_foncier";
  scheme_reduction?: { status: string; missing?: string[]; missing_msg?: ServerMsgs; base?: Money; total?: Money; annual?: Money; candidate?: Money; rate_pct?: number; years?: number; first_year?: number; last_year?: number; in_window?: boolean; notes?: string[]; notes_msg?: ServerMsgs };
  documents?: { id: string; item: string; from: string }[]; notes?: string[]; notes_msg?: ServerMsgs;
}
export interface RentalIndicators {
  as_of: string; disclaimer: string; disclaimer_key?: DisclaimerKey; trailing_effort: Money | null; signals: { id: string; reading: string; reading_msg?: ReadingMsg }[]; scenarios: string[]; scenarios_msg?: ServerMsgs;
  market_rate: { status: "missing" | "given"; note?: string; note_msg?: ServerMsg; rate_pct?: number; date?: string; basis?: string; age_days?: number; warning?: string; warning_msg?: ServerMsg };
  loan_rate: { status: string; loan_rate_pct?: number; market_rate_pct?: number; gap_pts?: number; threshold_pts?: number; reading?: string; reading_msg?: ReadingMsg; missing?: string[]; missing_msg?: ServerMsgs;
    renegotiation_note?: string; renegotiation_note_msg?: ServerMsg;
    renegotiation?: { status: string; current_payment?: string; new_payment?: string; monthly_saving?: string; total_costs?: string; penalty?: string; net_saving?: string; break_even_months?: number | null; verdict?: string; notes?: string[] } };
  commitment: { state: string; start_date: string | null; years: number | null; end_date: string | null; effective_end_date: string | null; days_left?: number; months_left?: number; decision_needed?: boolean; extension: { decision: string } };
  equity: { status: string; value?: Money; value_as_of?: string; value_warning?: string; value_warning_msg?: ServerMsg; outstanding?: Money; net_equity?: Money; loan_to_value_pct?: number; equity_share_pct?: number;
    missing?: string[]; missing_msg?: ServerMsgs; notes?: string[]; notes_msg?: ServerMsgs; approximate?: boolean };
}
