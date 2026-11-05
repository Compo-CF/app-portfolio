/* Revenue tab: Apple proceeds per fiscal period, by app and by product.
 *
 * Reads data/revenue.json (scripts/revenue.py, from Apple's financial
 * reports). Kept out of index.html's main script on purpose: it needs nothing
 * from portfolio.json, and a separate file means the two can change without
 * stepping on each other.
 *
 * Amounts are what Apple settled, net of its commission. Foreign currencies
 * are converted at the ECB rate on the period's last day (the same rate the
 * Payments tab uses); the native amounts are on hover. AdMob is not here.
 */
(function () {
  const host = document.getElementById("revenue");
  if (!host) return;

  const css = document.createElement("style");
  css.textContent = `
    .rv-grid{display:grid;gap:8px;padding:9px 14px;font-size:13.5px;
      font-variant-numeric:tabular-nums;border-bottom:1px solid var(--line);
      align-items:center}
    .rv-grid:last-child{border-bottom:0}
    .rv-grid > :not(:first-child){text-align:right}
    .rv-grid.head{color:var(--faint);font-size:11px;text-transform:uppercase;
      letter-spacing:.07em;font-weight:700}
    .rv-grid.total{font-weight:700}
    .rv-grid small{display:block;color:var(--faint);font-size:11px;font-weight:400}
    .rv-h{font-size:15px;font-weight:700;margin:26px 2px 10px}
    .rv-bars{background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:14px 16px}
    .rv-bar{display:grid;grid-template-columns:92px 1fr 78px;gap:10px;
      align-items:center;font-size:13px;margin:6px 0;font-variant-numeric:tabular-nums}
    .rv-bar .track{display:flex;height:18px;border-radius:5px;overflow:hidden;
      background:var(--line)}
    .rv-bar .track i{display:block;height:100%}
    .rv-bar b{text-align:right}
    .rv-key{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;
      color:var(--soft);margin-top:10px}
    .rv-key i{display:inline-block;width:10px;height:10px;border-radius:3px;
      margin-right:5px;vertical-align:-1px}
    .rv-share{display:block;height:4px;border-radius:2px;background:var(--accent);
      margin-top:4px;opacity:.55}
    .rv-grid.m{grid-template-columns:var(--lg)}
    .rv-open{color:var(--soft);font-style:italic}
    #revenue .range{flex-wrap:wrap;row-gap:6px}
    @media (max-width:640px){
      .rv-grid.m{grid-template-columns:var(--sm)}
      .rv-grid{font-size:12.5px;padding:8px 10px;gap:6px}
      .rv-bar{grid-template-columns:64px 1fr 64px}
      .rv-hide-sm{display:none}
    }`;
  document.head.appendChild(css);

  const COLORS = ["var(--accent)", "var(--play)", "var(--warn)", "var(--bad)", "var(--soft)"];
  const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const usd = (n) => "$" + n.toLocaleString("en-US",
    {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const day = (iso) => { const [, m, d] = iso.split("-"); return `${MON[+m - 1]} ${+d}`; };
  // A fiscal period is named for the calendar month it mostly covers: the
  // month of its midpoint. Not the end month: FY26 P10 runs Jun 28 - Aug 1
  // and is July's sales.
  const monthName = (p) => {
    const mid = new Date((Date.parse(p.start) + Date.parse(p.end)) / 2);
    return `${MON[mid.getUTCMonth()]} ${mid.getUTCFullYear()}`;
  };
  const fyLabel = (p) => { const [y, n] = p.label.split("-"); return `FY${y.slice(2)} P${n}`; };
  // Native amounts, one figure per currency however many rows carry it.
  const native = (rows) => {
    const by = {};
    for (const r of rows) by[r.currency] = (by[r.currency] || 0) + r.amount;
    return Object.entries(by).sort((a, b) => (a[0] === "USD" ? -1 : b[0] === "USD" ? 1 : a[0] < b[0] ? -1 : 1))
      .map(([c, v]) => `${v.toLocaleString("en-US", {maximumFractionDigits: 2})} ${c}`).join(", ");
  };
  const sum = (rows, f) => rows.reduce((n, r) => n + (f(r) || 0), 0);
  // A converted figure that leaves a currency out must say so.
  const shortBy = (rows) => {
    const c = [...new Set(rows.filter((r) => r.usd == null).map((r) => r.currency))];
    return c.length ? ` (${c.join(", ")} not converted: no ECB rate)` : "";
  };
  const range = (a, b) => (a.slice(0, 7) === b.slice(0, 7)
    ? `${day(a)}–${+b.slice(8)}` : `${day(a)} – ${day(b)}`);

  let DATA = null;
  let PICK = null;

  function render() {
    const periods = DATA.periods || [];
    if (!periods.length) {
      host.innerHTML = `<p class="caveat">No Apple financial reports yet.</p>`;
      return;
    }
    const totalOf = (p) => sum(p.rows, (r) => r.usd);
    const foreignOf = (p) => p.rows.some((r) => r.currency !== "USD");
    const approx = (p) => (foreignOf(p) ? "≈" : "");
    const latest = periods[periods.length - 1];
    const prior = periods[periods.length - 2];

    // Two views beside the settled periods, for the Products picker and the
    // purchase table: the days since the newest financial report (daily sales
    // reports, not settled yet), and every settled period together.
    const od = (DATA.open && DATA.open.days) || {};
    const OPEN = DATA.open && DATA.open.through && Object.values(od).some((rs) => rs.length)
      ? {label: "open", open: true, start: DATA.open.start, end: DATA.open.through,
         missing: DATA.open.missing || [], rows: Object.values(od).flat()}
      : null;
    const ALL = {label: "all", all: true, start: periods[0].start, end: latest.end,
                 rows: periods.flatMap((p) => p.rows)};
    const nameOf = (p) => (p.open ? range(p.start, p.end) : p.all ? "All periods" : monthName(p));
    const views = [...(OPEN ? [OPEN] : []), ...[...periods].reverse(), ALL];
    const pick = views.find((p) => p.label === PICK) || latest;

    // Apps ranked by all-time proceeds; the top four keep a colour, the rest
    // share "Other" so the chart stays readable as the portfolio grows.
    const appTotals = {};
    for (const p of periods) for (const r of p.rows)
      appTotals[r.app] = (appTotals[r.app] || 0) + (r.usd || 0);
    const apps = Object.keys(appTotals).sort((a, b) => appTotals[b] - appTotals[a]);
    const colorOf = (a) => COLORS[Math.min(apps.indexOf(a), COLORS.length - 1)];
    const keyed = apps.slice(0, COLORS.length - 1);
    const keyName = (a) => (keyed.includes(a) ? a : "Other");

    /* ---- headline numbers ---- */
    const lt = totalOf(latest);
    // Dollars, not a percentage: from small bases a percentage is noise
    // (Sep -> Oct 2026 read "+1791%").
    const change = prior
      ? `${monthName(prior)}: ${approx(prior)}${usd(totalOf(prior))}`
      : "first period";
    const all = sum(periods, totalOf);
    const units = sum(latest.rows, (r) => r.units);
    const stats = [
      [`${approx(latest)}${usd(lt)}`, `${monthName(latest)} proceeds`, change],
      [`${usd(all)}`, "All periods", `${periods.length} fiscal periods since ${day(periods[0].start)} ${periods[0].start.slice(0, 4)}`],
      [String(units), `Purchases, ${monthName(latest)}`, `${usd(lt / Math.max(units, 1))} per purchase`],
      [`${((sum(latest.rows.filter((r) => r.app === apps[0]), (r) => r.usd) / lt) * 100).toFixed(0)}%`,
        `${esc(apps[0])} share`, monthName(latest)],
    ];
    let html = `<div class="totals">${stats.map(([v, l, e]) =>
      `<div class="stat"><b>${v}</b><span>${l}</span><em>${e}</em></div>`).join("")}</div>`;

    /* ---- bars per period ---- */
    const max = Math.max(...periods.map(totalOf));
    html += `<div class="rv-h">Proceeds by fiscal period</div><div class="rv-bars">`;
    for (const p of periods) {
      const t = totalOf(p);
      const byKey = {};
      for (const r of p.rows) byKey[keyName(r.app)] = (byKey[keyName(r.app)] || 0) + (r.usd || 0);
      const segs = Object.entries(byKey).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
        `<i style="width:${(v / max) * 100}%;background:${k === "Other" ? COLORS[COLORS.length - 1] : colorOf(k)}"
            title="${esc(k)}: ${usd(v)}"></i>`).join("");
      html += `<div class="rv-bar" title="${fyLabel(p)}, ${day(p.start)} – ${day(p.end)}">
        <span>${monthName(p)}</span><div class="track">${segs}</div><b>${approx(p)}${usd(t)}</b></div>`;
    }
    const keys = [...new Set(apps.map(keyName))];
    html += `<div class="rv-key">${keys.map((k) => `<span><i style="background:${
      k === "Other" ? COLORS[COLORS.length - 1] : colorOf(k)}"></i>${esc(k)}</span>`).join("")}</div></div>`;

    /* ---- app x period table ---- */
    const shown = periods.slice(-6);
    const cols = `grid-template-columns:minmax(0,1.6fr) repeat(${shown.length + 1},minmax(0,1fr))`;
    html += `<div class="rv-h">By app</div><div class="months">
      <div class="rv-grid head" style="${cols}"><span>App</span>${shown.map((p) =>
        `<span title="${fyLabel(p)}, ${day(p.start)} – ${day(p.end)}">${monthName(p).slice(0, 3)} ${monthName(p).slice(-2)}</span>`).join("")}<span>Total</span></div>`;
    for (const a of apps) {
      const cells = shown.map((p) => {
        const rs = p.rows.filter((r) => r.app === a);
        const v = sum(rs, (r) => r.usd);
        return `<span title="${esc(native(rs) || "none")}">${v ? usd(v) : "—"}</span>`;
      });
      html += `<div class="rv-grid" style="${cols}"><span>${esc(a)}</span>${cells.join("")}<b>${usd(appTotals[a])}</b></div>`;
    }
    html += `<div class="rv-grid total" style="${cols}"><span>Total</span>${shown.map((p) =>
      `<span>${approx(p)}${usd(totalOf(p))}</span>`).join("")}<span>${usd(all)}</span></div></div>`;

    /* ---- purchase x period table ---- */
    // Every purchase ever bought, a column per settled period, the open days
    // after them, and the settled total. Ranked by everything it has earned.
    const keyOf = (r) => `${r.app}\u0000${r.product}`;
    const buys = {};
    const entry = (r) => (buys[keyOf(r)] ||= {app: r.app, product: r.product, settled: [], open: []});
    for (const r of ALL.rows) entry(r).settled.push(r);
    for (const r of OPEN ? OPEN.rows : []) entry(r).open.push(r);
    const earned = (b) => sum(b.settled, (r) => r.usd) + sum(b.open, (r) => r.usd);
    const ranked = Object.values(buys).sort((x, y) => earned(y) - earned(x));
    const buyCols = [...shown, ...(OPEN ? [OPEN] : [])];
    // Narrow screens keep the newest settled period, the open days and the total.
    const keepSm = (i) => i >= shown.length - 1;
    const cls = (p, i) => [keepSm(i) ? "" : "rv-hide-sm", p.open ? "rv-open" : ""].join(" ").trim();
    const mcols = `--lg:minmax(0,1.9fr) repeat(${buyCols.length},minmax(0,1fr)) minmax(0,.6fr) minmax(0,1fr);` +
      `--sm:minmax(0,1.7fr) repeat(${buyCols.filter((_, i) => keepSm(i)).length},minmax(0,1fr)) minmax(0,1fr)`;
    const head = (p, i) => (p.open
      ? `<span class="${cls(p, i)}" title="Not settled yet: Apple's daily sales reports, ${day(p.start)} – ${day(p.end)}">${nameOf(p)}</span>`
      : `<span class="${cls(p, i)}" title="${fyLabel(p)}, ${day(p.start)} – ${day(p.end)}">${monthName(p).slice(0, 3)} ${monthName(p).slice(-2)}</span>`);
    html += `<div class="rv-h">By purchase</div><div class="months">
      <div class="rv-grid head m" style="${mcols}"><span>Purchase</span>${buyCols.map(head).join("")}
        <span class="rv-hide-sm" title="Settled periods">Units</span><span title="All settled periods">Settled</span></div>`;
    for (const b of ranked) {
      const units = sum(b.settled, (r) => r.units);
      const total = sum(b.settled, (r) => r.usd);
      const per = units > 0 ? ` · ${usd(total / units)} each` : "";
      const cells = buyCols.map((p, i) => {
        const rs = (p.open ? b.open : b.settled.filter((r) => p.rows.includes(r)));
        const tip = rs.length ? `${sum(rs, (r) => r.units)} sold · ${native(rs)}${shortBy(rs)}` : "none";
        const mark = rs.some((r) => r.currency !== "USD") ? "≈" : "";
        return `<span class="${cls(p, i)}" title="${esc(tip)}">${rs.length ? mark + usd(sum(rs, (r) => r.usd)) : "—"}</span>`;
      });
      html += `<div class="rv-grid m" style="${mcols}">
        <span>${esc(b.product)}<small>${esc(b.app)}${per}</small></span>${cells.join("")}
        <span class="rv-hide-sm">${b.settled.length ? units : "—"}</span>
        <b title="${esc((native(b.settled) || "none") + shortBy(b.settled))}">${b.settled.length ? usd(total) : "—"}</b></div>`;
    }
    html += `<div class="rv-grid total m" style="${mcols}"><span>Total</span>${buyCols.map((p, i) =>
      `<span class="${cls(p, i)}">${approx(p)}${usd(totalOf(p))}</span>`).join("")}
      <span class="rv-hide-sm">${sum(ALL.rows, (r) => r.units)}</span><span>${usd(all)}</span></div></div>`;

    /* ---- products for one period ---- */
    html += `<div class="rv-h">Products</div>
      <div class="range" role="group" aria-label="Period"><span>Period</span>${
      views.map((p) => `<button type="button" data-label="${p.label}"
        aria-pressed="${p === pick}"${p.open ? ' title="Not settled yet"' : ""}>${nameOf(p)}</button>`).join("")}</div>`;
    const groups = {};
    for (const r of pick.rows) (groups[keyOf(r)] ||= []).push(r);
    const prods = Object.values(groups).sort((x, y) => sum(y, (r) => r.usd) - sum(x, (r) => r.usd));
    const pt = totalOf(pick);
    const pcols = "--lg:minmax(0,2fr) minmax(0,.6fr) minmax(0,1.6fr) minmax(0,.9fr);" +
      "--sm:minmax(0,2fr) minmax(0,.6fr) minmax(0,.9fr)";
    html += `<div class="months">
      <div class="rv-grid head m" style="${pcols}"><span>Product</span><span>Units</span>
        <span class="rv-hide-sm">${pick.open ? "Earned in" : "Settled in"}</span><span>USD</span></div>`;
    for (const rs of prods) {
      const v = sum(rs, (r) => r.usd);
      const foreign = rs.some((r) => r.currency !== "USD");
      const share = pt ? v / pt : 0;
      html += `<div class="rv-grid m" style="${pcols}">
        <span>${esc(rs[0].product)}<small>${esc(rs[0].app)} · ${(share * 100).toFixed(1)}%</small>
          <span class="rv-share" style="width:${Math.max(share, 0) * 100}%"></span></span>
        <span>${sum(rs, (r) => r.units)}</span>
        <span class="rv-hide-sm" style="font-size:12px;color:var(--soft)">${esc(native(rs) + shortBy(rs))}</span>
        <b>${foreign ? "≈" : ""}${usd(v)}</b></div>`;
    }
    const pickName = pick.open
      ? `${range(pick.start, pick.end)} · daily sales, not settled yet`
      : pick.all ? `All periods · ${day(pick.start)} ${pick.start.slice(0, 4)} – ${day(pick.end)} ${pick.end.slice(0, 4)}`
      : `${monthName(pick)} · ${fyLabel(pick)}, ${day(pick.start)} – ${day(pick.end)}`;
    html += `<div class="rv-grid total m" style="${pcols}"><span>${pickName}</span>
      <span>${sum(pick.rows, (r) => r.units)}</span>
      <span class="rv-hide-sm"></span><span>${approx(pick)}${usd(pt)}</span></div></div>`;

    html += `<div class="caveat">From Apple's financial reports: what Apple settled for each
      fiscal period, net of its commission, before tax and banking. Apple's fiscal months run
      Sunday to Saturday, so “${monthName(latest)}” is ${day(latest.start)} – ${day(latest.end)}.
      Each region settles in its own currency and Apple converts only when it pays, so amounts
      marked ≈ include foreign currency at the European Central Bank rate on the period's last
      day and will differ from the deposit by a few dollars; hover a cell for the native amounts.
      A region's sales can land a period late, because they appear only in the period Apple settles
      them. AdMob is not included. Updated ${esc((DATA.generated || "").slice(0, 10))}.${OPEN ? `
      <br><br><i>${range(OPEN.start, OPEN.end)}</i>, in italics, is not settled yet: it is read from
      Apple's daily sales reports (Pacific days), converted at each day's ECB rate, and moves into a
      fiscal period when Apple's next financial report closes. Treat it as an estimate.${
      OPEN.missing.length ? ` No report yet for ${OPEN.missing.map(day).join(", ")}, so it is short those days.` : ""}` : ""}</div>`;

    host.innerHTML = html;
    host.querySelectorAll(".range button").forEach((b) =>
      b.addEventListener("click", () => { PICK = b.dataset.label; render(); }));
  }

  fetch("data/revenue.json", {cache: "no-cache"})
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((d) => { DATA = d; render(); })
    .catch(() => {
      host.innerHTML = `<p class="caveat">Revenue data could not be loaded.</p>`;
    });
})();
