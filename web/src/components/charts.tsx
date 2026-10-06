import { ReactNode, useState } from "react";
import { Area, Bar, BarChart, CartesianGrid, ComposedChart, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtDate, fmtMoney, fmtMonth, fmtPct, parseMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Forecast, MonthFlow, CategoryDetail, NetWorthPoint, RentalMonth } from "@/api/types";

const n = (v: string | null | undefined) => parseMoney(v) ?? 0;
const AXIS = { stroke: "var(--grid)", tick: { fill: "var(--muted)", fontSize: 11 }, tickLine: false } as const;

/** Chart + a "table" view of the same numbers (identity is never colour alone; the data is always reachable). */
export function ChartFrame({ label, children, table, legend }: { label: string; children: ReactNode; table: { head: string[]; rows: ReactNode[][] }; legend?: { color: string; text: string; hatched?: boolean }[] }) {
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
          {asTable ? "Chart" : "Table"}
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
  const data = months.map((m) => ({ ...m, key: m.month, income_n: n(m.income), spending_n: n(m.spending), saved_n: n(m.saved) }));
  return (
    <ChartFrame
      label="Income and spending per month"
      legend={[
        { color: "var(--s1)", text: "Income" },
        { color: "var(--s2)", text: "Spending" },
      ]}
      table={{
        head: ["Month", "Income", "Spending", "Saved", "Net", "Savings rate"],
        rows: months.map((m) => [fmtMonth(m.month) + (m.complete ? "" : " *"), fmtMoney(m.income), fmtMoney(m.spending), fmtMoney(m.saved), fmtMoney(m.net, { signed: true }), fmtPct(m.savings_rate)]),
      }}
    >
      <div style={{ height: 220 }} role="img" aria-label="Bar chart of income and spending for the last months; open the table view for the numbers">
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
                      { color: "var(--s1)", label: "Income", value: fmtMoney(m.income) },
                      { color: "var(--s2)", label: "Spending", value: fmtMoney(m.spending) },
                      { label: "Saved", value: fmtMoney(m.saved) },
                      { label: "Net", value: fmtMoney(m.net, { signed: true }) },
                      { label: "Savings rate", value: fmtPct(m.savings_rate) },
                    ]}
                    note={m.complete ? undefined : `Incomplete: ${m.missing_accounts.join(", ") || "partial month"}`}
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
  const data = months.map((m) => ({ ...m, key: m.month, rent_n: n(m.rent), costs_n: n(m.costs) }));
  return (
    <ChartFrame
      label="Rent and costs of the property per month"
      legend={[
        { color: "var(--s1)", text: "Rent received" },
        { color: "var(--s2)", text: "Costs (loan included)" },
      ]}
      table={{
        head: ["Month", "Rent", "Costs", "Net", "Effort d'épargne", "Rent"],
        rows: months.map((m) => [fmtMonth(m.month) + (m.complete ? "" : " *"), fmtMoney(m.rent), fmtMoney(m.costs), fmtMoney(m.net, { signed: true }), fmtMoney(m.effort), m.rent_status.replace(/_/g, " ")]),
      }}
    >
      <div style={{ height: 220 }} role="img" aria-label="Bar chart of the rent received and the costs of the property for the last months; open the table view for the numbers">
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
                      { color: "var(--s1)", label: "Rent", value: fmtMoney(m.rent) },
                      { color: "var(--s2)", label: "Costs", value: fmtMoney(m.costs) },
                      { label: "Net", value: fmtMoney(m.net, { signed: true }) },
                      { label: "Effort d'épargne", value: fmtMoney(m.effort) },
                    ]}
                    note={m.complete ? undefined : "The account data do not cover the whole month"}
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
  const data = f.points.map((p) => ({ date: p.date, balance: n(p.balance), low: n(p.low), high: n(p.high), band: [n(p.low), n(p.high)] as [number, number] }));
  const min = Math.min(0, ...data.map((d) => d.low));
  const first = f.first_negative ?? f.first_at_risk;
  return (
    <ChartFrame
      label="Projected balance for the next 90 days"
      legend={[
        { color: "var(--s1)", text: "Expected balance" },
        { color: "var(--s1)", text: "Likely range (about 80 %)", hatched: true },
      ]}
      table={{
        head: ["Date", "Expected", "Low", "High"],
        rows: data.filter((_, i) => i % 7 === 0 || i === data.length - 1).map((d) => [fmtDate(d.date), fmtMoney(d.balance), fmtMoney(d.low), fmtMoney(d.high)]),
      }}
    >
      <div style={{ height }} role="img" aria-label={`Projected balance; lowest expected ${fmtMoney(f.min_balance)} on ${fmtDate(f.min_date)}`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            {min < 0 && <ReferenceArea y1={min} y2={0} fill="var(--neg)" fillOpacity={0.07} />}
            <XAxis dataKey="date" tickFormatter={(d) => fmtDate(d, "dayMonth")} minTickGap={42} {...AXIS} axisLine={{ stroke: "var(--grid)" }} />
            <YAxis tickFormatter={(v) => fmtMoney(v, { compact: true, round: true })} width={56} {...AXIS} axisLine={false} domain={["auto", "auto"]} />
            <ReferenceLine y={0} stroke="var(--neg)" strokeDasharray="4 3" strokeOpacity={0.6} />
            {first && <ReferenceLine x={first} stroke="var(--warn)" strokeDasharray="2 3" label={{ value: f.first_negative ? "below 0" : "at risk", fill: "var(--warn)", fontSize: 11, position: "insideTopRight" }} />}
            <Tooltip
              content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const d = payload[0].payload;
                return (
                  <TipBox
                    title={fmtDate(d.date, "weekday")}
                    rows={[
                      { color: "var(--s1)", label: "Expected", value: fmtMoney(d.balance) },
                      { label: "Low end", value: fmtMoney(d.low) },
                      { label: "High end", value: fmtMoney(d.high) },
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
  const data = series.map((s) => ({ ...s, run: n(s.run_rate), one: n(s.one_off) }));
  return (
    <ChartFrame
      label="Monthly amount"
      legend={[
        { color: "var(--s1)", text: "Regular" },
        { color: "var(--s2)", text: "One-offs" },
        ...(average !== null ? [{ color: "var(--text)", text: "Monthly average" }] : []),
      ]}
      table={{
        head: ["Month", "Regular", "One-offs", "Transactions", "Coverage"],
        rows: series.map((s) => [fmtMonth(s.month), fmtMoney(s.run_rate), fmtMoney(s.one_off), s.n_tx, s.partial ? "month in progress" : s.covered ? "complete" : "incomplete"]),
      }}
    >
      <div style={{ height }} role="img" aria-label="Monthly amounts; open the table view for the numbers">
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
                      { color: "var(--s1)", label: "Regular", value: fmtMoney(s.run_rate) },
                      { color: "var(--s2)", label: "One-offs", value: fmtMoney(s.one_off) },
                      { label: "Transactions", value: String(s.n_tx) },
                    ]}
                    note={s.partial ? "Month in progress" : s.covered ? undefined : "Not fully covered by the accounts that carry this category"}
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
const NW_CATS: { key: "cash" | "savings" | "investments" | "real_estate" | "vehicles" | "other"; label: string; color: string }[] = [
  { key: "cash", label: "Cash", color: "var(--s1)" },
  { key: "savings", label: "Savings", color: "var(--s3)" },
  { key: "investments", label: "Investments", color: "var(--s7)" },
  { key: "real_estate", label: "Real estate", color: "var(--s4)" },
  { key: "vehicles", label: "Vehicles", color: "var(--s5)" },
  { key: "other", label: "Other", color: "var(--s6)" },
];

/** Monthly net worth: assets stacked by category above the axis, what is owed below it, the net worth as a line. A month with items whose
 *  value is unknown that month is drawn lighter and says how many (it is "the known part only"), never as a complete figure. */
export function NetWorthChart({ points }: { points: NetWorthPoint[] }) {
  const data = points.map((p) => ({
    ...p, key: p.month, owed: -n(p.liabilities), net: n(p.net_worth),
    ...Object.fromEntries(NW_CATS.map((c) => [c.key, n(p.by_category[c.key])])),
  }));
  const dim = (p: any) => (p.complete ? 1 : 0.42);
  return (
    <ChartFrame
      label="Net worth by month"
      legend={[...NW_CATS.map((c) => ({ color: c.color, text: c.label })), { color: "var(--neg)", text: "Owed" }, { color: "var(--text)", text: "Net worth" }, { color: "var(--muted)", text: "Lighter bars: known part only", hatched: true }]}
      table={{
        head: ["Month", "Net worth", "Assets", "Owed", "Unknown items", "Source"],
        rows: points.map((p) => [fmtMonth(p.month), fmtMoney(p.net_worth, { round: true }), fmtMoney(p.assets, { round: true }), fmtMoney(p.liabilities, { round: true }), p.n_unknown ? `${p.n_unknown} not counted` : "none", (p.source === "snapshot" ? "recorded" : "rebuilt") + ((p.newly_counted ?? []).length ? " (new value counted)" : "")]),
      }}
    >
      <div style={{ height: 260 }} role="img" aria-label="Stacked bars of assets by category and what is owed for each month, with the net worth line; months with unknown items are lighter. Open the table view for the numbers">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} stackOffset="sign" margin={{ top: 8, right: 4, left: 0, bottom: 0 }} barCategoryGap="22%">
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="key" tickFormatter={(k) => fmtMonth(k)} {...AXIS} axisLine={{ stroke: "var(--grid)" }} />
            <YAxis tickFormatter={(v) => fmtMoney(v, { compact: true, round: true })} width={56} {...AXIS} axisLine={false} />
            <ReferenceLine y={0} stroke="var(--border-strong)" />
            {data.filter((p) => (p.newly_counted ?? []).length > 0).map((p) => (
              <ReferenceLine key={`nc-${p.key}`} x={p.key} stroke="var(--warn)" strokeDasharray="4 3" label={{ value: "value known from here", position: "insideTopLeft", fill: "var(--warn)", fontSize: 10 }} />
            ))}
            <Tooltip
              cursor={{ fill: "var(--surface-2)", opacity: 0.6 }}
              content={({ active, payload }: any) => {
                if (!active || !payload?.length) return null;
                const p: NetWorthPoint = payload[0].payload;
                return (
                  <TipBox
                    title={`${fmtMonth(p.month, "long")} (${p.source === "snapshot" ? "recorded " + fmtDate(p.as_of, "dayMonth") : "rebuilt from the data"})`}
                    rows={[
                      { color: "var(--text)", label: "Net worth", value: fmtMoney(p.net_worth, { round: true }) },
                      ...NW_CATS.filter((c) => n(p.by_category[c.key]) !== 0).map((c) => ({ color: c.color, label: c.label, value: fmtMoney(p.by_category[c.key], { round: true }) })),
                      { color: "var(--neg)", label: "Owed", value: fmtMoney(p.liabilities, { round: true }) },
                    ]}
                    note={[
                      p.complete ? "" : `${p.n_unknown} item${p.n_unknown > 1 ? "s" : ""} not known this month and NOT counted: ${p.unknown.map((u) => u.reason).filter((x, i, a) => a.indexOf(x) === i).join("; ")}`,
                      (p.newly_counted ?? []).length ? `Counted from this month: ${(p.newly_counted ?? []).map((x) => x.label).join(", ")} (first recorded value). The jump is not a change in wealth.` : "",
                      p.caveat ?? "",
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
