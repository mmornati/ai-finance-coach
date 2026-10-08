import { ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Area, Bar, BarChart, CartesianGrid, ComposedChart, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtDate, fmtMoney, fmtMonth, fmtPct, parseMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import { tServer } from "@/i18n/server";
import type { Forecast, MonthFlow, CategoryDetail, NetWorthPoint, RentalMonth } from "@/api/types";

const n = (v: string | null | undefined) => parseMoney(v) ?? 0;
const AXIS = { stroke: "var(--grid)", tick: { fill: "var(--muted)", fontSize: 11 }, tickLine: false } as const;

/** Chart + a "table" view of the same numbers (identity is never colour alone; the data is always reachable). */
export function ChartFrame({ label, children, table, legend }: { label: string; children: ReactNode; table: { head: string[]; rows: ReactNode[][] }; legend?: { color: string; text: string; hatched?: boolean }[] }) {
  const { t } = useTranslation();
  const [asTable, setAsTable] = useState(false);
  return (
    <figure aria-label={label} className="m-0">
      <div className="mb-1 flex items-center justify-between gap-3">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
          {legend?.map((l) => (
            <span key={l.text} className="inline-flex items-center gap-1.5">
              <span aria-hidden className={cn("inline-block size-2.5 rounded-sm", l.hatched && "opacity-50")} style={{ background: l.color }} />
              {l.text}
            </span>
          ))}
        </div>
        <button type="button" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable} className="rounded px-2 py-1 text-xs text-muted hover:bg-surface-2 hover:text-text">
          {asTable ? t("charts.chart") : t("charts.table")}
        </button>
      </div>
      {asTable ? (
        <div className="max-h-72 overflow-auto rounded-lg border border-border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-2 text-left text-muted">
              <tr>
                {table.head.map((h, i) => (
                  <th key={h} scope="col" className={cn("px-3 py-1.5 font-medium", i > 0 && "text-right")}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((r, i) => (
                <tr key={i} className="border-t border-border">
                  {r.map((c, j) => (
                    <td key={j} className={cn("num px-3 py-1.5", j > 0 && "text-right")}>
                      {c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        children
      )}
    </figure>
  );
}

function TipBox({ title, rows, note }: { title: string; rows: { color?: string; label: string; value: string }[]; note?: string }) {
  return (
    <div className="pointer-events-none min-w-40 rounded-lg border border-border-strong bg-surface px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-semibold text-text">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-4 py-0.5">
          <span className="inline-flex items-center gap-1.5 text-muted">
            {r.color && <span aria-hidden className="inline-block size-2 rounded-sm" style={{ background: r.color }} />}
            {r.label}
          </span>
          <span className="num font-medium text-text">{r.value}</span>
        </div>
      ))}
      {note && <div className="mt-1 border-t border-border pt-1 text-faint">{note}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ cash flow: income vs spending per month */
export function CashflowChart({ months }: { months: MonthFlow[] }) {
  const { t } = useTranslation();
  const data = months.map((m) => ({ ...m, key: m.month, income_n: n(m.income), spending_n: n(m.spending), saved_n: n(m.saved) }));
  return (
    <ChartFrame
      label={t("charts.cashflow.label")}
      legend={[
        { color: "var(--s1)", text: t("charts.income") },
        { color: "var(--s2)", text: t("charts.spending") },
      ]}
      table={{
        head: [t("charts.month"), t("charts.income"), t("charts.spending"), t("charts.saved"), t("charts.net"), t("charts.savingsRate")],
        rows: months.map((m) => [fmtMonth(m.month) + (m.complete ? "" : " *"), fmtMoney(m.income), fmtMoney(m.spending), fmtMoney(m.saved), fmtMoney(m.net, { signed: true }), fmtPct(m.savings_rate)]),
      }}
    >
      <div style={{ height: 220 }} role="img" aria-label={t("charts.cashflow.aria")}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 4, left: 0, bottom: 0 }} barGap={2} barCategoryGap="22%">
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="key" tickFormatter={(k) => fmtMonth(k)} {...AXIS} axisLine={{ stroke: "var(--grid)" }} />
            <YAxis tickFormatter={(v) => fmtMoney(v, { compact: true, round: true })} width={52} {...AXIS} axisLine={false} />
            <Tooltip
              cursor={{ fill: "var(--surface-2)", opacity: 0.6 }}
              content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const m: MonthFlow = payload[0].payload;
                return (
                  <TipBox
                    title={fmtMonth(m.month, "long")}
                    rows={[
                      { color: "var(--s1)", label: t("charts.income"), value: fmtMoney(m.income) },
                      { color: "var(--s2)", label: t("charts.spending"), value: fmtMoney(m.spending) },
                      { label: t("charts.saved"), value: fmtMoney(m.saved) },
                      { label: t("charts.net"), value: fmtMoney(m.net, { signed: true }) },
                      { label: t("charts.savingsRate"), value: fmtPct(m.savings_rate) },
                    ]}
                    note={m.complete ? undefined : t("charts.cashflow.incomplete", { accounts: m.missing_accounts.join(", ") || t("charts.cashflow.partialMonth") })}
                  />
                );
              }}
            />
            <Bar dataKey="income_n" fill="var(--s1)" radius={[4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false} shape={(p: any) => <Rect {...p} dim={!p.payload.complete} />} />
            <Bar dataKey="spending_n" fill="var(--s2)" radius={[4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false} shape={(p: any) => <Rect {...p} dim={!p.payload.complete} />} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ E15: rent received vs the property's costs per month */
export function RentalChart({ months }: { months: RentalMonth[] }) {
  const { t } = useTranslation();
  const data = months.map((m) => ({ ...m, key: m.month, rent_n: n(m.rent), costs_n: n(m.costs) }));
  return (
    <ChartFrame
      label={t("charts.rental.label")}
      legend={[
        { color: "var(--s1)", text: t("charts.rental.rentReceived") },
        { color: "var(--s2)", text: t("charts.rental.costsWithLoan") },
      ]}
      table={{
        head: [t("charts.month"), t("charts.rental.rent"), t("charts.rental.costs"), t("charts.net"), t("charts.rental.effort"), t("charts.rental.rentStatus")],
        rows: months.map((m) => [fmtMonth(m.month) + (m.complete ? "" : " *"), fmtMoney(m.rent), fmtMoney(m.costs), fmtMoney(m.net, { signed: true }), fmtMoney(m.effort), m.rent_status.replace(/_/g, " ")]),
      }}
    >
      <div style={{ height: 220 }} role="img" aria-label={t("charts.rental.aria")}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 4, left: 0, bottom: 0 }} barGap={2} barCategoryGap="22%">
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="key" tickFormatter={(k) => fmtMonth(k)} {...AXIS} axisLine={{ stroke: "var(--grid)" }} />
            <YAxis tickFormatter={(v) => fmtMoney(v, { compact: true, round: true })} width={52} {...AXIS} axisLine={false} />
            <Tooltip
              cursor={{ fill: "var(--surface-2)", opacity: 0.6 }}
              content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const m: RentalMonth = payload[0].payload;
                return (
                  <TipBox
                    title={fmtMonth(m.month, "long")}
                    rows={[
                      { color: "var(--s1)", label: t("charts.rental.rent"), value: fmtMoney(m.rent) },
                      { color: "var(--s2)", label: t("charts.rental.costs"), value: fmtMoney(m.costs) },
                      { label: t("charts.net"), value: fmtMoney(m.net, { signed: true }) },
                      { label: t("charts.rental.effort"), value: fmtMoney(m.effort) },
                    ]}
                    note={m.complete ? undefined : t("charts.rental.partialData")}
                  />
                );
              }}
            />
            <Bar dataKey="rent_n" fill="var(--s1)" radius={[4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false} shape={(p: any) => <Rect {...p} dim={!p.payload.complete} />} />
            <Bar dataKey="costs_n" fill="var(--s2)" radius={[4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false} shape={(p: any) => <Rect {...p} dim={!p.payload.complete} />} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartFrame>
  );
}

/** A bar with rounded data end; incomplete months are drawn lighter (coverage-aware: they are not comparable). */
function Rect({ x, y, width, height, fill, dim }: any) {
  if (!(height > 0) || !(width > 0)) return null;
  const r = Math.min(4, width / 2, height);
  return <path d={`M${x},${y + height} V${y + r} Q${x},${y} ${x + r},${y} H${x + width - r} Q${x + width},${y} ${x + width},${y + r} V${y + height} Z`} fill={fill} opacity={dim ? 0.38 : 1} />;
}

/* ------------------------------------------------------------------ forecast with the ~80 % band */
export function ForecastChart({ f, height = 240 }: { f: Forecast["household"]; height?: number }) {
  const { t } = useTranslation();
  const data = f.points.map((p) => ({ date: p.date, balance: n(p.balance), low: n(p.low), high: n(p.high), band: [n(p.low), n(p.high)] as [number, number] }));
  const min = Math.min(0, ...data.map((d) => d.low));
  const first = f.first_negative ?? f.first_at_risk;
  return (
    <ChartFrame
      label={t("charts.forecast.label")}
      legend={[
        { color: "var(--s1)", text: t("charts.forecast.expected") },
        { color: "var(--s1)", text: t("charts.forecast.range"), hatched: true },
      ]}
      table={{
        head: [t("charts.forecast.date"), t("charts.forecast.expectedShort"), t("charts.forecast.low"), t("charts.forecast.high")],
        rows: data.filter((_, i) => i % 7 === 0 || i === data.length - 1).map((d) => [fmtDate(d.date), fmtMoney(d.balance), fmtMoney(d.low), fmtMoney(d.high)]),
      }}
    >
      <div style={{ height }} role="img" aria-label={t("charts.forecast.aria", { amount: fmtMoney(f.min_balance), date: fmtDate(f.min_date) })}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            {min < 0 && <ReferenceArea y1={min} y2={0} fill="var(--neg)" fillOpacity={0.07} />}
            <XAxis dataKey="date" tickFormatter={(d) => fmtDate(d, "dayMonth")} minTickGap={42} {...AXIS} axisLine={{ stroke: "var(--grid)" }} />
            <YAxis tickFormatter={(v) => fmtMoney(v, { compact: true, round: true })} width={56} {...AXIS} axisLine={false} domain={["auto", "auto"]} />
            <ReferenceLine y={0} stroke="var(--neg)" strokeDasharray="4 3" strokeOpacity={0.6} />
            {first && <ReferenceLine x={first} stroke="var(--warn)" strokeDasharray="2 3" label={{ value: f.first_negative ? t("charts.forecast.belowZero") : t("charts.forecast.atRisk"), fill: "var(--warn)", fontSize: 11, position: "insideTopRight" }} />}
            <Tooltip
              content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const d = payload[0].payload;
                return (
                  <TipBox
                    title={fmtDate(d.date, "weekday")}
                    rows={[
                      { color: "var(--s1)", label: t("charts.forecast.expectedShort"), value: fmtMoney(d.balance) },
                      { label: t("charts.forecast.lowEnd"), value: fmtMoney(d.low) },
                      { label: t("charts.forecast.highEnd"), value: fmtMoney(d.high) },
                    ]}
                  />
                );
              }}
            />
            <Area dataKey="band" stroke="none" fill="var(--s1)" fillOpacity={0.16} isAnimationActive={false} activeDot={false} />
            <Line dataKey="balance" stroke="var(--s1)" strokeWidth={2} dot={false} isAnimationActive={false} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ category: monthly bars, one-offs stacked, average line */
export function MonthlyBars({ series, average, height = 240 }: { series: CategoryDetail["series"]; average: number | null; height?: number }) {
  const { t } = useTranslation();
  const data = series.map((s) => ({ ...s, run: n(s.run_rate), one: n(s.one_off) }));
  return (
    <ChartFrame
      label={t("charts.monthly.label")}
      legend={[
        { color: "var(--s1)", text: t("charts.monthly.regular") },
        { color: "var(--s2)", text: t("charts.monthly.oneOffs") },
        ...(average !== null ? [{ color: "var(--text)", text: t("charts.monthly.average") }] : []),
      ]}
      table={{
        head: [t("charts.month"), t("charts.monthly.regular"), t("charts.monthly.oneOffs"), t("charts.monthly.transactions"), t("charts.monthly.coverage")],
        rows: series.map((s) => [fmtMonth(s.month), fmtMoney(s.run_rate), fmtMoney(s.one_off), s.n_tx, s.partial ? t("charts.monthly.inProgress") : s.covered ? t("charts.monthly.complete") : t("charts.monthly.incomplete")]),
      }}
    >
      <div style={{ height }} role="img" aria-label={t("charts.monthly.aria")}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 4, left: 0, bottom: 0 }} barCategoryGap="18%">
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="month" tickFormatter={(k) => fmtMonth(k)} minTickGap={16} {...AXIS} axisLine={{ stroke: "var(--grid)" }} />
            <YAxis tickFormatter={(v) => fmtMoney(v, { compact: true, round: true })} width={52} {...AXIS} axisLine={false} />
            {average !== null && <ReferenceLine y={average} stroke="var(--text)" strokeDasharray="4 3" strokeOpacity={0.7} />}
            <Tooltip
              cursor={{ fill: "var(--surface-2)", opacity: 0.6 }}
              content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const s = payload[0].payload;
                return (
                  <TipBox
                    title={fmtMonth(s.month, "long")}
                    rows={[
                      { color: "var(--s1)", label: t("charts.monthly.regular"), value: fmtMoney(s.run_rate) },
                      { color: "var(--s2)", label: t("charts.monthly.oneOffs"), value: fmtMoney(s.one_off) },
                      { label: t("charts.monthly.transactions"), value: String(s.n_tx) },
                    ]}
                    note={s.partial ? t("charts.monthly.monthInProgress") : s.covered ? undefined : t("charts.monthly.notCovered")}
                  />
                );
              }}
            />
            <Bar dataKey="run" stackId="a" fill="var(--s1)" isAnimationActive={false} maxBarSize={28} shape={(p: any) => <Seg {...p} dim={!p.payload.covered} partial={p.payload.partial} />} />
            <Bar dataKey="one" stackId="a" fill="var(--s2)" isAnimationActive={false} maxBarSize={28} shape={(p: any) => <Seg {...p} dim={!p.payload.covered} partial={p.payload.partial} top />} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartFrame>
  );
}

function Seg({ x, y, width, height, fill, dim, partial, top }: any) {
  if (!(height > 0) || !(width > 0)) return null;
  const gap = 1; // 2px surface gap between stacked fills (1px each side)
  return <rect x={x} y={y + gap} width={width} height={Math.max(0, height - gap)} rx={top ? 3 : 0} fill={fill} opacity={dim ? 0.35 : partial ? 0.7 : 1} stroke={partial ? fill : "none"} strokeDasharray={partial ? "3 2" : undefined} />;
}

/* ------------------------------------------------------------------ sparkline */
export function Sparkline({ values, color = "var(--s1)", height = 28, label }: { values: number[]; color?: string; height?: number; label: string }) {
  const data = values.map((v, i) => ({ i, v }));
  if (data.length < 2) return null;
  return (
    <div style={{ height, width: 96 }} role="img" aria-label={label}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 3, right: 3, bottom: 3, left: 3 }}>
          <Line dataKey="v" stroke={color} strokeWidth={1.75} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Horizontal bar of a share (top categories, merchants): a plain element, faster and clearer than a chart. */
export function ShareBar({ value, max, color = "var(--s1)", marker }: { value: number; max: number; color?: string; marker?: number | null }) {
  const w = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div aria-hidden className="relative h-1.5 w-full rounded-full bg-surface-2">
      <div className="h-full rounded-full" style={{ width: `${w}%`, background: color }} />
      {marker !== null && marker !== undefined && max > 0 && <div className="absolute -inset-y-0.5 w-0.5 rounded bg-text/70" style={{ left: `${Math.min(100, (marker / max) * 100)}%` }} />}
    </div>
  );
}

/* ------------------------------------------------------------------ net worth history (E9-4) */
const NW_CATS: { key: "cash" | "savings" | "investments" | "real_estate" | "vehicles" | "other"; label: ParseKeys; color: string }[] = [
  { key: "cash", label: "charts.netWorth.category.cash", color: "var(--s1)" },
  { key: "savings", label: "charts.netWorth.category.savings", color: "var(--s3)" },
  { key: "investments", label: "charts.netWorth.category.investments", color: "var(--s7)" },
  { key: "real_estate", label: "charts.netWorth.category.real_estate", color: "var(--s4)" },
  { key: "vehicles", label: "charts.netWorth.category.vehicles", color: "var(--s5)" },
  { key: "other", label: "charts.netWorth.category.other", color: "var(--s6)" },
];

/** Monthly net worth: assets stacked by category above the axis, what is owed below it, the net worth as a line. A month with items whose
 *  value is unknown that month is drawn lighter and says how many (it is "the known part only"), never as a complete figure. */
export function NetWorthChart({ points }: { points: NetWorthPoint[] }) {
  const { t } = useTranslation();
  const data = points.map((p) => ({
    ...p, key: p.month, owed: -n(p.liabilities), net: n(p.net_worth),
    ...Object.fromEntries(NW_CATS.map((c) => [c.key, n(p.by_category[c.key])])),
  }));
  const dim = (p: any) => (p.complete ? 1 : 0.42);
  return (
    <ChartFrame
      label={t("charts.netWorth.label")}
      legend={[...NW_CATS.map((c) => ({ color: c.color, text: t(c.label) })), { color: "var(--neg)", text: t("charts.netWorth.owed") }, { color: "var(--text)", text: t("charts.netWorth.title") }, { color: "var(--muted)", text: t("charts.netWorth.lighter"), hatched: true }]}
      table={{
        head: [t("charts.month"), t("charts.netWorth.title"), t("charts.netWorth.assets"), t("charts.netWorth.owed"), t("charts.netWorth.unknownItems"), t("charts.netWorth.source")],
        rows: points.map((p) => [fmtMonth(p.month), fmtMoney(p.net_worth, { round: true }), fmtMoney(p.assets, { round: true }), fmtMoney(p.liabilities, { round: true }), p.n_unknown ? t("charts.netWorth.notCounted", { count: p.n_unknown }) : t("charts.netWorth.none"), (p.source === "snapshot" ? t("charts.netWorth.recorded") : t("charts.netWorth.rebuilt")) + ((p.newly_counted ?? []).length ? t("charts.netWorth.newValueCounted") : "")]),
      }}
    >
      <div style={{ height: 260 }} role="img" aria-label={t("charts.netWorth.aria")}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} stackOffset="sign" margin={{ top: 8, right: 4, left: 0, bottom: 0 }} barCategoryGap="22%">
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="key" tickFormatter={(k) => fmtMonth(k)} {...AXIS} axisLine={{ stroke: "var(--grid)" }} />
            <YAxis tickFormatter={(v) => fmtMoney(v, { compact: true, round: true })} width={56} {...AXIS} axisLine={false} />
            <ReferenceLine y={0} stroke="var(--border-strong)" />
            {data.filter((p) => (p.newly_counted ?? []).length > 0).map((p) => (
              <ReferenceLine key={`nc-${p.key}`} x={p.key} stroke="var(--warn)" strokeDasharray="4 3" label={{ value: t("charts.netWorth.valueKnownFromHere"), position: "insideTopLeft", fill: "var(--warn)", fontSize: 10 }} />
            ))}
            <Tooltip
              cursor={{ fill: "var(--surface-2)", opacity: 0.6 }}
              content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const p: NetWorthPoint = payload[0].payload;
                return (
                  <TipBox
                    title={p.source === "snapshot" ? t("charts.netWorth.tipSnapshot", { month: fmtMonth(p.month, "long"), date: fmtDate(p.as_of, "dayMonth") }) : t("charts.netWorth.tipRebuilt", { month: fmtMonth(p.month, "long") })}
                    rows={[
                      { color: "var(--text)", label: t("charts.netWorth.title"), value: fmtMoney(p.net_worth, { round: true }) },
                      ...NW_CATS.filter((c) => n(p.by_category[c.key]) !== 0).map((c) => ({ color: c.color, label: t(c.label), value: fmtMoney(p.by_category[c.key], { round: true }) })),
                      { color: "var(--neg)", label: t("charts.netWorth.owed"), value: fmtMoney(p.liabilities, { round: true }) },
                    ]}
                    note={[
                      p.complete ? "" : t("charts.netWorth.unknownNote", { count: p.n_unknown, reasons: p.unknown.map((u) => tServer(u.reason_msg, u.reason)).filter((x, i, a) => a.indexOf(x) === i).join("; ") }),
                      (p.newly_counted ?? []).length ? t("charts.netWorth.countedFrom", { items: (p.newly_counted ?? []).map((x) => x.label).join(", ") }) : "",
                      tServer(p.caveat_msg, p.caveat ?? ""),
                    ].filter(Boolean).join(" ") || undefined}
                  />
                );
              }}
            />
            {NW_CATS.map((c) => (
              <Bar key={c.key} dataKey={c.key} stackId="nw" fill={c.color} maxBarSize={26} isAnimationActive={false} shape={(p: any) => <rect x={p.x} y={p.y} width={p.width} height={Math.max(0, p.height)} fill={p.fill} opacity={dim(p.payload)} />} />
            ))}
            <Bar dataKey="owed" stackId="nw" fill="var(--neg)" maxBarSize={26} isAnimationActive={false} shape={(p: any) => <rect x={p.x} y={p.y} width={p.width} height={Math.abs(p.height)} fill={p.fill} opacity={dim(p.payload)} />} />
            <Line dataKey="net" type="monotone" stroke="var(--text)" strokeWidth={2} dot={(p: any) => <circle key={p.key} cx={p.cx} cy={p.cy} r={3} fill={p.payload.complete ? "var(--text)" : "var(--surface)"} stroke="var(--text)" strokeWidth={1.5} />} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </ChartFrame>
  );
}
