import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  RadialBarChart,
  RadialBar,
  PolarAngleAxis,
  ResponsiveContainer,
} from "recharts";

/* ============================================================================
 * App registry — declared once, drives status bar / cards / flow diagram.
 * The Vite dev server proxies `/p`, `/m`, `/r` to the three FastAPI services
 * (see vite.config.js), so the browser is same-origin and CORS-free.
 * The `swagger` link points directly to the real port for "open in new tab".
 * ==========================================================================*/
const APPS = {
  provider: {
    key: "provider",
    label: "Provider",
    role: "Parts supplier",
    port: 8001,
    base: "/p",
    swagger: "http://localhost:8001/docs",
    accent: "#22c55e",
    accentVar: "var(--color-provider)",
  },
  manufacturer: {
    key: "manufacturer",
    label: "Manufacturer",
    role: "Factory",
    port: 8002,
    base: "/m",
    swagger: "http://localhost:8002/docs",
    accent: "#3b82f6",
    accentVar: "var(--color-manufacturer)",
  },
  retailer: {
    key: "retailer",
    label: "Retailer",
    role: "Storefront",
    port: 8003,
    base: "/r",
    swagger: "http://localhost:8003/docs",
    accent: "#f97316",
    accentVar: "var(--color-retailer)",
  },
};

/* ============================================================================
 * fetch helper — JSON, with timeout and a unified error shape.
 * Returns null on failure so `Promise.allSettled` callers don't have to
 * branch on rejection.
 * ==========================================================================*/
async function fetchJson(url, { timeoutMs = 4000 } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

/* ============================================================================
 * useDashboardData — single source of truth for the whole dashboard.
 * Fetches everything in parallel with Promise.allSettled. Each app has its
 * own `up` flag so a single dead service doesn't hide the rest of the UI.
 * ==========================================================================*/
function useDashboardData() {
  const [data, setData] = useState({
    provider: { up: null, day: null, stock: [], orders: [], catalog: [] },
    manufacturer: {
      up: null,
      day: null,
      stock: [],
      orders: [],
      catalog: [],
      capacity: null,
      production: null,
      inventory: [],
    },
    retailer: {
      up: null,
      day: null,
      stock: [],
      orders: [],
      catalog: [],
      backorders: [],
    },
    lastUpdated: null,
    loading: true,
  });

  const refetch = useCallback(async () => {
    setData((d) => ({ ...d, loading: true }));

    const wrap = (p) => p.catch(() => null);

    const [
      // provider
      pDay, pStock, pOrders, pCatalog,
      // manufacturer
      mDay, mStock, mOrders, mCatalog, mCapacity, mProduction, mInventory,
      // retailer
      rDay, rStock, rOrders, rCatalog, rBackorders,
    ] = await Promise.all([
      wrap(fetchJson(`${APPS.provider.base}/api/day/current`)),
      wrap(fetchJson(`${APPS.provider.base}/api/stock`)),
      wrap(fetchJson(`${APPS.provider.base}/api/orders`)),
      wrap(fetchJson(`${APPS.provider.base}/api/catalog`)),

      wrap(fetchJson(`${APPS.manufacturer.base}/api/day/current`)),
      wrap(fetchJson(`${APPS.manufacturer.base}/api/stock`)),
      wrap(fetchJson(`${APPS.manufacturer.base}/api/orders`)),
      wrap(fetchJson(`${APPS.manufacturer.base}/api/catalog`)),
      wrap(fetchJson(`${APPS.manufacturer.base}/api/capacity`)),
      wrap(fetchJson(`${APPS.manufacturer.base}/api/production/status`)),
      wrap(fetchJson(`${APPS.manufacturer.base}/inventory`)),

      wrap(fetchJson(`${APPS.retailer.base}/api/day/current`)),
      wrap(fetchJson(`${APPS.retailer.base}/api/stock`)),
      wrap(fetchJson(`${APPS.retailer.base}/api/orders`)),
      wrap(fetchJson(`${APPS.retailer.base}/api/catalog`)),
      wrap(fetchJson(`${APPS.retailer.base}/api/orders?status=backordered`)),
    ]);

    // An app counts as UP iff its day endpoint replied. The other endpoints
    // can independently fail (e.g. empty seed) without taking the app down.
    const providerUp = pDay !== null;
    const manufacturerUp = mDay !== null;
    const retailerUp = rDay !== null;

    setData({
      provider: {
        up: providerUp,
        day: pDay?.current_day ?? null,
        stock: Array.isArray(pStock) ? pStock : [],
        orders: Array.isArray(pOrders) ? pOrders : [],
        catalog: Array.isArray(pCatalog) ? pCatalog : [],
      },
      manufacturer: {
        up: manufacturerUp,
        day: mDay?.current_day ?? null,
        stock: Array.isArray(mStock) ? mStock : [],
        orders: Array.isArray(mOrders) ? mOrders : [],
        catalog: Array.isArray(mCatalog) ? mCatalog : [],
        capacity: mCapacity ?? null,
        production: mProduction ?? null,
        inventory: Array.isArray(mInventory) ? mInventory : [],
      },
      retailer: {
        up: retailerUp,
        day: rDay?.current_day ?? null,
        stock: Array.isArray(rStock) ? rStock : [],
        orders: Array.isArray(rOrders) ? rOrders : [],
        catalog: Array.isArray(rCatalog) ? rCatalog : [],
        backorders: Array.isArray(rBackorders) ? rBackorders : [],
      },
      lastUpdated: new Date(),
      loading: false,
    });
  }, []);

  return [data, refetch];
}

/* ============================================================================
 * Visual helpers
 * ==========================================================================*/

// Map a percentage 0..100 to a semantic color.
function stockColor(pct) {
  if (pct >= 50) return "var(--color-ok)";
  if (pct >= 20) return "var(--color-warn)";
  return "var(--color-danger)";
}

function fmtNum(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString();
}

/* Wraps a value-derived render and briefly flashes whenever the tracked
 * `value` prop differs from the previous one. The first ever render does
 * NOT flash (otherwise initial paint would highlight everything). */
function Pulse({ value, children, className = "" }) {
  const prev = useRef(value);
  const seeded = useRef(false);
  const [flashing, setFlashing] = useState(false);

  useEffect(() => {
    if (!seeded.current) {
      seeded.current = true;
      prev.current = value;
      return;
    }
    if (prev.current !== value) {
      prev.current = value;
      setFlashing(true);
      const t = setTimeout(() => setFlashing(false), 1400);
      return () => clearTimeout(t);
    }
  }, [value]);

  return (
    <span className={`${className} ${flashing ? "value-flash" : ""}`}>
      {children}
    </span>
  );
}

/* ============================================================================
 * Header
 * ==========================================================================*/
function Header({ currentDay, onRefresh, loading }) {
  return (
    <header className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-4">
      <div className="flex items-baseline gap-4">
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-[var(--color-ok)] live-dot" />
          <h1 className="text-base font-semibold tracking-[0.18em] uppercase">
            Supply Chain Monitor
          </h1>
        </div>
        <div className="text-xs uppercase tracking-widest text-neutral-500">
          v0.1 · 3D Printer Sim
        </div>
      </div>

      <div className="flex items-center gap-6">
        <div className="flex items-baseline gap-2">
          <span className="text-[10px] uppercase tracking-[0.2em] text-neutral-500">
            Sim Day
          </span>
          <Pulse value={currentDay} className="mono text-2xl font-semibold text-neutral-100 tabular-nums">
            {currentDay === null ? "—" : String(currentDay).padStart(3, "0")}
          </Pulse>
        </div>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-sm border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-1.5 text-xs uppercase tracking-widest text-neutral-300 transition hover:border-neutral-500 hover:text-white disabled:opacity-40"
        >
          <ReloadIcon spinning={loading} />
          <span>Refresh</span>
        </button>
      </div>
    </header>
  );
}

function ReloadIcon({ spinning = false, size = 14 }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={spinning ? "animate-spin" : undefined}
    >
      <path d="M3 12a9 9 0 0 1 15.5-6.36L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15.5 6.36L3 16" />
      <path d="M3 21v-5h5" />
    </svg>
  );
}

/* ============================================================================
 * Status bar — three pills, click opens swagger
 * ==========================================================================*/
function StatusBar({ data }) {
  return (
    <div className="grid grid-cols-3 gap-3 border-b border-[var(--color-border)] px-6 py-3">
      {Object.values(APPS).map((app) => {
        const s = data[app.key];
        return (
          <StatusPill
            key={app.key}
            app={app}
            up={s.up}
            loading={s.up === null}
          />
        );
      })}
    </div>
  );
}

function StatusPill({ app, up, loading }) {
  const dotColor = loading
    ? "#737373"
    : up
    ? "var(--color-ok)"
    : "var(--color-danger)";
  const label = loading ? "CONNECTING" : up ? "UP" : "DOWN";

  return (
    <a
      href={app.swagger}
      target="_blank"
      rel="noreferrer"
      className="group flex items-center justify-between rounded-sm border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2 transition hover:border-neutral-500"
      title={`Open Swagger UI · ${app.swagger}`}
    >
      <div className="flex items-center gap-3">
        <span
          className={`inline-block h-2.5 w-2.5 rounded-full ${
            !loading && up ? "live-dot" : ""
          }`}
          style={{ background: dotColor }}
        />
        <div className="flex flex-col">
          <span
            className="text-[11px] font-semibold uppercase tracking-[0.2em]"
            style={{ color: app.accent }}
          >
            {app.label}
          </span>
          <span className="mono text-[10px] text-neutral-500">
            localhost:{app.port}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span
          className="mono text-[11px] font-semibold tracking-[0.2em]"
          style={{ color: dotColor }}
        >
          {label}
        </span>
        <ExternalIcon />
      </div>
    </a>
  );
}

function ExternalIcon() {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="text-neutral-500 transition group-hover:text-neutral-200"
    >
      <path d="M7 17 17 7" />
      <path d="M7 7h10v10" />
    </svg>
  );
}

/* ============================================================================
 * Day timeline — visualises drift between the three apps' current_day
 * ==========================================================================*/
function DayTimeline({ data }) {
  const days = Object.values(APPS).map((app) => ({
    app,
    day: data[app.key].day,
    up: data[app.key].up,
  }));

  const known = days.map((d) => d.day).filter((v) => v !== null);
  const min = known.length ? Math.min(...known) : 0;
  const max = known.length ? Math.max(...known) : 0;

  // Add at least 4 days of context on each side so the dots aren't pinned to the edge.
  const span = Math.max(max - min, 1);
  const pad = Math.max(2, Math.round(span * 0.5));
  const lo = Math.max(0, min - pad);
  const hi = max + pad;
  const total = Math.max(hi - lo, 1);

  const drift = max - min;

  return (
    <section className="border-b border-[var(--color-border)] px-6 py-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-[0.2em] text-neutral-500">
          Day Timeline · per-app simulation clock
        </span>
        <span
          className={`mono text-[10px] uppercase tracking-[0.2em] ${
            drift > 0 ? "text-[var(--color-warn)]" : "text-neutral-500"
          }`}
        >
          {drift === 0 ? "in sync" : `drift Δ${drift}d`}
        </span>
      </div>

      <div className="relative h-12">
        {/* baseline */}
        <div className="absolute left-0 right-0 top-1/2 h-px -translate-y-1/2 bg-[var(--color-border-strong)]" />

        {/* tick marks every 1 day if span is small, else every 5 */}
        {Array.from({ length: total + 1 }).map((_, i) => {
          const day = lo + i;
          const isMajor = total < 12 || day % 5 === 0;
          if (!isMajor) return null;
          const left = (i / total) * 100;
          return (
            <div
              key={day}
              className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2"
              style={{ left: `${left}%` }}
            >
              <div className="h-2 w-px bg-[var(--color-border-strong)]" />
              <div className="mono mt-1 text-[9px] tracking-widest text-neutral-600">
                {day}
              </div>
            </div>
          );
        })}

        {/* per-app markers */}
        {days.map(({ app, day, up }, idx) => {
          if (day === null || !up) return null;
          const left = ((day - lo) / total) * 100;
          // small vertical offset so overlapping dots remain visible
          const top = 50 + (idx - 1) * 14;
          return (
            <div
              key={app.key}
              className="absolute -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${left}%`, top: `${top}%` }}
              title={`${app.label} · day ${day}`}
            >
              <div
                className="flex items-center gap-1.5 rounded-sm border bg-[var(--color-surface)] px-2 py-0.5"
                style={{ borderColor: app.accent }}
              >
                <span
                  className="inline-block h-1.5 w-1.5 rounded-full"
                  style={{ background: app.accent }}
                />
                <span
                  className="mono text-[10px] tracking-widest"
                  style={{ color: app.accent }}
                >
                  {app.label.slice(0, 1)}·{day}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/* ============================================================================
 * Stock bars — per-item horizontal progress bars with semantic colours.
 * Items are normalised to { name, quantity, capacity } from each app's
 * particular stock shape.
 * ==========================================================================*/
function StockBars({ items, accent, emptyLabel = "no stock data" }) {
  if (!items || items.length === 0) {
    return (
      <div className="mono py-3 text-[11px] uppercase tracking-widest text-neutral-600">
        {emptyLabel}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {items.map((it) => {
        const cap = it.capacity ?? Math.max(it.quantity, 10);
        const pct = cap > 0 ? Math.min(100, (it.quantity / cap) * 100) : 0;
        const color = it.deficit ? "var(--color-danger)" : stockColor(pct);
        return (
          <div key={it.name}>
            <div className="flex items-baseline justify-between">
              <span className="truncate text-[11px] text-neutral-300" title={it.name}>
                {it.name}
              </span>
              <span className="mono text-[11px] tabular-nums text-neutral-200">
                {fmtNum(it.quantity)}
                {it.capacity != null && (
                  <span className="text-neutral-600"> / {fmtNum(it.capacity)}</span>
                )}
              </span>
            </div>
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded-sm bg-[var(--color-surface-2)]">
              <div
                className="h-full transition-[width] duration-500"
                style={{ width: `${pct}%`, background: color }}
              />
            </div>
            {it.deficit ? (
              <div
                className="mono mt-0.5 text-[9px] uppercase tracking-widest"
                style={{ color: "var(--color-danger)" }}
              >
                deficit · {fmtNum(it.deficit)}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/* ============================================================================
 * Capacity gauge (Recharts RadialBarChart)
 * ==========================================================================*/
function CapacityGauge({ capacity }) {
  const cap = capacity?.capacity_per_day ?? 0;
  const used = capacity?.utilization_estimate ?? 0;
  const pct = cap > 0 ? Math.min(100, (used / cap) * 100) : 0;

  const color =
    pct >= 90 ? "var(--color-danger)"
      : pct >= 70 ? "var(--color-warn)"
      : "var(--color-manufacturer)";

  const chartData = [{ name: "util", value: pct, fill: color }];

  return (
    <div className="relative flex h-32 items-center justify-center">
      <ResponsiveContainer width="100%" height="100%">
        <RadialBarChart
          cx="50%"
          cy="50%"
          innerRadius="78%"
          outerRadius="100%"
          barSize={10}
          data={chartData}
          startAngle={90}
          endAngle={-270}
        >
          <PolarAngleAxis
            type="number"
            domain={[0, 100]}
            angleAxisId={0}
            tick={false}
          />
          <RadialBar
            background={{ fill: "var(--color-surface-2)" }}
            dataKey="value"
            cornerRadius={2}
            isAnimationActive={false}
          />
        </RadialBarChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <Pulse value={Math.round(pct)} className="mono text-2xl font-semibold tabular-nums" >
          <span style={{ color }}>{Math.round(pct)}%</span>
        </Pulse>
        <span className="mono text-[9px] uppercase tracking-[0.2em] text-neutral-500">
          <Pulse value={used}>{fmtNum(used)}</Pulse> / {fmtNum(cap)} units
        </span>
      </div>
    </div>
  );
}

/* ============================================================================
 * App card — one column per app
 * ==========================================================================*/
function AppCard({ app, state, children }) {
  const offline = state.up === false;
  const connecting = state.up === null;

  return (
    <article
      className={`flex flex-col rounded-sm border bg-[var(--color-surface)] ${
        offline ? "opacity-60" : ""
      }`}
      style={{ borderColor: offline ? "var(--color-border)" : app.accent + "33" }}
    >
      <header
        className="flex items-center justify-between border-b px-4 py-2"
        style={{ borderColor: "var(--color-border)" }}
      >
        <div className="flex items-center gap-2">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{
              background: offline
                ? "var(--color-danger)"
                : connecting
                ? "#737373"
                : app.accent,
            }}
          />
          <span
            className="text-[11px] font-semibold uppercase tracking-[0.2em]"
            style={{ color: offline ? "var(--color-muted)" : app.accent }}
          >
            {app.label}
          </span>
          <span className="mono text-[10px] text-neutral-500">:{app.port}</span>
        </div>
        <span className="mono text-[10px] uppercase tracking-widest text-neutral-500">
          {app.role}
        </span>
      </header>

      <div className="flex-1 px-4 py-3">
        {offline ? (
          <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-2 text-center">
            <span className="mono text-xs uppercase tracking-[0.3em] text-[var(--color-danger)]">
              ● Offline
            </span>
            <span className="text-[11px] text-neutral-500">
              No response from {app.base} ({app.swagger.replace("/docs", "")})
            </span>
          </div>
        ) : (
          children
        )}
      </div>
    </article>
  );
}

function CardSection({ title, right, children }) {
  return (
    <section className="mb-4 last:mb-0">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-[10px] font-semibold uppercase tracking-[0.2em] text-neutral-400">
          {title}
        </h3>
        {right}
      </div>
      {children}
    </section>
  );
}

function PendingBadge({ count, label = "pending", tone = "warn" }) {
  const color =
    tone === "danger" ? "var(--color-danger)"
      : tone === "ok" ? "var(--color-ok)"
      : "var(--color-warn)";
  return (
    <div className="flex items-center justify-between rounded-sm border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2">
      <span className="text-[11px] uppercase tracking-widest text-neutral-400">
        {label}
      </span>
      <Pulse value={count} className="mono text-lg font-semibold tabular-nums">
        <span style={{ color }}>{fmtNum(count)}</span>
      </Pulse>
    </div>
  );
}

/* ============================================================================
 * Main 3-column grid
 * ==========================================================================*/
function MainGrid({ data }) {
  // Provider stock: { product_id, product_name, quantity }
  const providerStock = data.provider.stock.slice(0, 6).map((s) => ({
    name: s.product_name ?? s.name ?? s.product_id ?? "?",
    quantity: s.quantity ?? 0,
    // Provider doesn't expose a max capacity → derive a soft baseline from observed values.
  }));
  const providerStockBaseline = Math.max(
    10,
    ...data.provider.stock.map((s) => s.quantity ?? 0),
  );
  const providerStockWithCap = providerStock.map((it) => ({
    ...it,
    capacity: providerStockBaseline,
  }));

  const providerPending = data.provider.orders.filter(
    (o) => !["delivered", "cancelled"].includes(o.status),
  ).length;

  // Manufacturer parts (richer than finished printers).
  const mfgInventory = data.manufacturer.inventory.slice(0, 6).map((p) => ({
    name: p.name,
    quantity: p.current_stock,
    capacity: p.storage_size > 0 ? p.storage_size : null,
    deficit: p.deficit > 0 ? p.deficit : 0,
  }));
  const mfgFinished = data.manufacturer.stock.slice(0, 3).map((s) => ({
    name: s.model ?? s.name ?? "?",
    quantity: s.quantity ?? 0,
  }));
  const mfgFinishedBaseline = Math.max(
    5,
    ...data.manufacturer.stock.map((s) => s.quantity ?? 0),
  );
  const mfgFinishedWithCap = mfgFinished.map((it) => ({
    ...it,
    capacity: mfgFinishedBaseline,
  }));
  const mfgPending = data.manufacturer.orders.filter(
    (o) => !["delivered", "cancelled"].includes(o.status),
  ).length;

  // Retailer
  const retailerStock = data.retailer.stock.slice(0, 4).map((s) => ({
    name: s.model ?? s.name ?? "?",
    quantity: s.quantity ?? 0,
  }));
  const retailerStockBaseline = Math.max(
    5,
    ...data.retailer.stock.map((s) => s.quantity ?? 0),
  );
  const retailerStockWithCap = retailerStock.map((it) => ({
    ...it,
    capacity: retailerStockBaseline,
  }));
  const retailerBackorders = data.retailer.backorders?.length ?? 0;
  const retailerPending = data.retailer.orders.filter(
    (o) => o.status === "pending" || o.status === "backordered",
  ).length;

  return (
    <section className="grid grid-cols-1 gap-3 px-6 py-4 lg:grid-cols-3">
      {/* Provider */}
      <AppCard app={APPS.provider} state={data.provider}>
        <CardSection title="Parts stock">
          <StockBars
            items={providerStockWithCap}
            accent={APPS.provider.accent}
            emptyLabel="catalog empty"
          />
        </CardSection>
        <CardSection title="Open orders">
          <PendingBadge count={providerPending} label="not delivered" tone="warn" />
        </CardSection>
      </AppCard>

      {/* Manufacturer */}
      <AppCard app={APPS.manufacturer} state={data.manufacturer}>
        <CardSection title="Daily capacity">
          <CapacityGauge capacity={data.manufacturer.capacity} />
        </CardSection>
        <CardSection title="Parts inventory">
          <StockBars
            items={mfgInventory}
            accent={APPS.manufacturer.accent}
            emptyLabel="no parts loaded"
          />
        </CardSection>
        <CardSection title="Finished printers">
          <StockBars
            items={mfgFinishedWithCap}
            accent={APPS.manufacturer.accent}
            emptyLabel="0 finished"
          />
        </CardSection>
        <CardSection title="Sales orders">
          <PendingBadge count={mfgPending} label="not delivered" tone="warn" />
        </CardSection>
      </AppCard>

      {/* Retailer */}
      <AppCard app={APPS.retailer} state={data.retailer}>
        <CardSection title="Showroom stock">
          <StockBars
            items={retailerStockWithCap}
            accent={APPS.retailer.accent}
            emptyLabel="empty showroom"
          />
        </CardSection>
        <CardSection title="Customer queue">
          <div className="grid grid-cols-2 gap-2">
            <PendingBadge count={retailerPending} label="pending" tone="warn" />
            <PendingBadge
              count={retailerBackorders}
              label="backordered"
              tone={retailerBackorders > 0 ? "danger" : "ok"}
            />
          </div>
        </CardSection>
      </AppCard>
    </section>
  );
}

/* ============================================================================
 * Supply chain flow — provider → manufacturer → retailer → customer
 * ==========================================================================*/
function SupplyChainFlow({ data }) {
  // "in-flight" between nodes = orders not yet delivered
  const partsInFlight = data.provider.up
    ? data.provider.orders.filter(
        (o) => !["delivered", "cancelled"].includes(o.status),
      ).length
    : 0;
  const printersInFlight = data.manufacturer.up
    ? data.manufacturer.orders.filter(
        (o) => !["delivered", "cancelled"].includes(o.status),
      ).length
    : 0;
  const customerWaiting = data.retailer.up
    ? (data.retailer.backorders?.length ??
        data.retailer.orders.filter((o) => o.status === "backordered").length)
    : 0;

  const nodes = [
    { app: APPS.provider, label: "Provider", sub: "parts" },
    { app: APPS.manufacturer, label: "Manufacturer", sub: "factory" },
    { app: APPS.retailer, label: "Retailer", sub: "store" },
    {
      app: { accent: "#a3a3a3", label: "Customer" },
      label: "Customer",
      sub: "demand",
    },
  ];

  const edges = [
    { count: partsInFlight, label: "parts" },
    { count: printersInFlight, label: "printers" },
    { count: customerWaiting, label: "fulfill" },
  ];

  return (
    <section className="border-t border-[var(--color-border)] px-6 py-6">
      <div className="mb-4 text-[10px] uppercase tracking-[0.2em] text-neutral-500">
        Supply chain flow · active in-transit orders between nodes
      </div>

      <div className="grid grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr] items-center gap-2">
        {nodes.map((n, i) => (
          <React.Fragment key={n.label}>
            <FlowNode node={n} />
            {i < edges.length && <FlowEdge edge={edges[i]} />}
          </React.Fragment>
        ))}
      </div>
    </section>
  );
}

function FlowNode({ node }) {
  const { app } = node;
  return (
    <div
      className="flex flex-col items-center justify-center rounded-sm border bg-[var(--color-surface)] px-4 py-4"
      style={{ borderColor: app.accent + "55" }}
    >
      <span
        className="text-[11px] font-semibold uppercase tracking-[0.2em]"
        style={{ color: app.accent }}
      >
        {node.label}
      </span>
      <span className="mono mt-0.5 text-[9px] uppercase tracking-widest text-neutral-500">
        {node.sub}
      </span>
    </div>
  );
}

function FlowEdge({ edge }) {
  const active = edge.count > 0;
  return (
    <div className="relative flex h-12 w-full min-w-[80px] flex-col items-center justify-center">
      <svg
        viewBox="0 0 100 24"
        preserveAspectRatio="none"
        className="absolute inset-0 h-full w-full"
      >
        <line
          x1="0"
          y1="12"
          x2="100"
          y2="12"
          stroke={active ? "#a3a3a3" : "var(--color-border-strong)"}
          strokeWidth="1.4"
          className={active ? "flow-active" : ""}
          vectorEffect="non-scaling-stroke"
        />
        <polygon
          points="96,8 100,12 96,16"
          fill={active ? "#a3a3a3" : "var(--color-border-strong)"}
        />
      </svg>
      <div
        className="relative mono rounded-sm border px-1.5 py-0.5 text-[10px] tabular-nums"
        style={{
          background: "var(--color-bg)",
          borderColor: active ? "#404040" : "var(--color-border)",
          color: active ? "#e5e5e5" : "var(--color-muted)",
        }}
      >
        <Pulse value={edge.count}>{edge.count}</Pulse>
        <span className="ml-1 text-neutral-600">{edge.label}</span>
      </div>
    </div>
  );
}

/* ============================================================================
 * Event feed — synthesised from the orders data we already have
 * ==========================================================================*/
const EVENT_TYPES = {
  ORDER:    { code: "OR", color: "var(--color-warn)",         label: "ORDER" },
  RELEASE:  { code: "PR", color: "var(--color-manufacturer)", label: "PROD"  },
  SHIPPED:  { code: "SH", color: "#06b6d4",                   label: "SHIP"  },
  DELIV:    { code: "DL", color: "var(--color-ok)",           label: "DELIV" },
  PRICE:    { code: "$$", color: "#a855f7",                   label: "PRICE" },
};

function buildEvents(data) {
  const events = [];
  const todayMax = Math.max(
    data.provider.day ?? 0,
    data.manufacturer.day ?? 0,
    data.retailer.day ?? 0,
  );

  // Provider orders -> placed/shipped/delivered events
  for (const o of data.provider.orders) {
    if (o.placed_day != null) {
      events.push({
        day: o.placed_day,
        app: APPS.provider,
        type: EVENT_TYPES.ORDER,
        message: `Provider order placed · ${o.quantity}× ${o.product_id} → ${o.buyer}`,
        id: `p:${o.id}:placed`,
      });
    }
    if (o.shipped_day != null) {
      events.push({
        day: o.shipped_day,
        app: APPS.provider,
        type: EVENT_TYPES.SHIPPED,
        message: `Provider shipped · ${o.quantity}× ${o.product_id} → ${o.buyer}`,
        id: `p:${o.id}:shipped`,
      });
    }
    if (o.delivered_day != null) {
      events.push({
        day: o.delivered_day,
        app: APPS.provider,
        type: EVENT_TYPES.DELIV,
        message: `Provider delivered · ${o.quantity}× ${o.product_id} → ${o.buyer}`,
        id: `p:${o.id}:delivered`,
      });
    }
  }

  // Manufacturer sales orders -> placed/released/shipped/delivered
  for (const o of data.manufacturer.orders) {
    if (o.placed_day != null) {
      events.push({
        day: o.placed_day,
        app: APPS.manufacturer,
        type: EVENT_TYPES.ORDER,
        message: `Sales order · ${o.quantity}× ${o.model} from ${o.retailer_name}`,
        id: `m:${o.id}:placed`,
      });
    }
    if (o.released_day != null) {
      events.push({
        day: o.released_day,
        app: APPS.manufacturer,
        type: EVENT_TYPES.RELEASE,
        message: `Production released · ${o.quantity}× ${o.model}`,
        id: `m:${o.id}:released`,
      });
    }
    if (o.shipped_day != null) {
      events.push({
        day: o.shipped_day,
        app: APPS.manufacturer,
        type: EVENT_TYPES.SHIPPED,
        message: `Manufacturer shipped · ${o.quantity}× ${o.model} → ${o.retailer_name}`,
        id: `m:${o.id}:shipped`,
      });
    }
    if (o.delivered_day != null) {
      events.push({
        day: o.delivered_day,
        app: APPS.manufacturer,
        type: EVENT_TYPES.DELIV,
        message: `Sales order fulfilled · ${o.quantity}× ${o.model} → ${o.retailer_name}`,
        id: `m:${o.id}:delivered`,
      });
    }
  }

  // Retailer customer orders (no day fields → use retailer current day; fall back to global max)
  const rDay = data.retailer.day ?? todayMax;
  for (const o of data.retailer.orders) {
    const day = rDay; // best-effort: customer orders carry only ISO timestamps
    if (o.status === "fulfilled") {
      events.push({
        day,
        app: APPS.retailer,
        type: EVENT_TYPES.DELIV,
        message: `Customer fulfilled · ${o.quantity}× ${o.model} → ${o.customer}`,
        id: `r:${o.id}:fulfilled`,
      });
    } else {
      events.push({
        day,
        app: APPS.retailer,
        type: EVENT_TYPES.ORDER,
        message: `Customer order ${o.status} · ${o.quantity}× ${o.model} from ${o.customer}`,
        id: `r:${o.id}:placed`,
      });
    }
  }

  // Sort: most recent day first, then by event-type priority within the day
  // (DELIV > SHIP > RELEASE > ORDER) so the latest action surfaces.
  const priority = { DELIV: 4, SHIP: 3, PROD: 2, ORDER: 1, PRICE: 0 };
  events.sort((a, b) => {
    if (b.day !== a.day) return b.day - a.day;
    return (priority[b.type.label] ?? 0) - (priority[a.type.label] ?? 0);
  });

  return events.slice(0, 20);
}

function EventFeed({ data }) {
  const events = useMemo(() => buildEvents(data), [data]);

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-[var(--color-border)] bg-[var(--color-surface)]">
      <header className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
        <span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-neutral-300">
          Event feed
        </span>
        <span className="mono text-[10px] uppercase tracking-widest text-neutral-500">
          last {events.length} · max 20
        </span>
      </header>
      <div className="feed-scroll flex-1 overflow-y-auto">
        {events.length === 0 ? (
          <div className="px-4 py-6 text-center text-[11px] text-neutral-600">
            No events yet
          </div>
        ) : (
          <ul className="divide-y divide-[var(--color-border)]">
            {events.map((e) => (
              <li key={e.id} className="flex items-start gap-3 px-4 py-2.5">
                <span
                  className="mono mt-0.5 inline-flex w-7 shrink-0 items-center justify-center rounded-sm border px-1 py-0.5 text-[9px] font-bold tracking-widest"
                  style={{ borderColor: e.type.color, color: e.type.color }}
                  title={e.type.label}
                >
                  {e.type.code}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-2">
                    <span
                      className="mono text-[10px] tracking-widest"
                      style={{ color: e.app.accent }}
                    >
                      D{String(e.day).padStart(3, "0")}
                    </span>
                    <span
                      className="text-[9px] uppercase tracking-[0.2em]"
                      style={{ color: e.app.accent }}
                    >
                      {e.app.label}
                    </span>
                  </div>
                  <div className="mt-0.5 truncate text-[11px] text-neutral-300" title={e.message}>
                    {e.message}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

/* ============================================================================
 * Auto-refresh footer
 * ==========================================================================*/
function AutoRefreshBar({ intervalMs, setIntervalMs, lastUpdated, onRefresh, loading }) {
  const options = [
    { id: 10000, label: "10s" },
    { id: 30000, label: "30s" },
    { id: null,  label: "Off" },
  ];
  return (
    <footer className="flex items-center justify-between border-t border-[var(--color-border)] px-6 py-3">
      <div className="flex items-center gap-3">
        <span className="text-[10px] uppercase tracking-[0.2em] text-neutral-500">
          Auto-refresh
        </span>
        <div className="flex items-center rounded-sm border border-[var(--color-border-strong)] overflow-hidden">
          {options.map((opt) => {
            const active = intervalMs === opt.id;
            return (
              <button
                key={String(opt.id)}
                onClick={() => setIntervalMs(opt.id)}
                className={`mono px-3 py-1 text-[10px] uppercase tracking-widest transition ${
                  active
                    ? "bg-[var(--color-surface-2)] text-neutral-100"
                    : "text-neutral-500 hover:text-neutral-200"
                }`}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex items-center gap-4 mono text-[10px] uppercase tracking-widest text-neutral-500">
        <span className="hidden md:inline">
          shortcuts: <kbd className="rounded-sm border border-[var(--color-border-strong)] px-1 text-neutral-300">R</kbd> refresh ·{" "}
          <kbd className="rounded-sm border border-[var(--color-border-strong)] px-1 text-neutral-300">space</kbd> auto
        </span>
        {loading ? (
          <span className="text-neutral-300">refreshing…</span>
        ) : lastUpdated ? (
          <span>
            last update ·{" "}
            <span className="text-neutral-300 tabular-nums">
              {lastUpdated.toLocaleTimeString()}
            </span>
          </span>
        ) : (
          <span>idle</span>
        )}
      </div>
    </footer>
  );
}

/* ============================================================================
 * Root
 * ==========================================================================*/
export default function Dashboard() {
  const [data, refetch] = useDashboardData();
  const [intervalMs, setIntervalMs] = useState(10000);
  const intervalRef = useRef(null);

  // initial mount
  useEffect(() => {
    refetch();
  }, [refetch]);

  // auto-refresh
  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (intervalMs !== null) {
      intervalRef.current = setInterval(() => {
        refetch();
      }, intervalMs);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [intervalMs, refetch]);

  // Keyboard shortcuts: `r` refreshes immediately, space toggles auto-refresh.
  useEffect(() => {
    const onKey = (e) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "r" || e.key === "R") {
        e.preventDefault();
        refetch();
      } else if (e.key === " ") {
        e.preventDefault();
        setIntervalMs((curr) => (curr === null ? 10000 : null));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [refetch]);

  const headerDay = useMemo(() => {
    const known = [data.provider.day, data.manufacturer.day, data.retailer.day]
      .filter((v) => v !== null && v !== undefined);
    return known.length ? Math.max(...known) : null;
  }, [data]);

  return (
    <div className="grid h-screen grid-cols-[1fr_360px] grid-rows-[auto_1fr] bg-[var(--color-bg)] text-neutral-200">
      {/* main column spans top & body, feed sits on the right column */}
      <div className="col-start-1 row-start-1 flex flex-col overflow-y-auto">
        <Header
          currentDay={headerDay}
          onRefresh={refetch}
          loading={data.loading}
        />
        <StatusBar data={data} />
        <DayTimeline data={data} />
        <MainGrid data={data} />
        <SupplyChainFlow data={data} />
        <div className="mt-auto">
          <AutoRefreshBar
            intervalMs={intervalMs}
            setIntervalMs={setIntervalMs}
            lastUpdated={data.lastUpdated}
            onRefresh={refetch}
            loading={data.loading}
          />
        </div>
      </div>

      <div className="col-start-2 row-start-1 row-span-2 min-h-0">
        <EventFeed data={data} />
      </div>
    </div>
  );
}
