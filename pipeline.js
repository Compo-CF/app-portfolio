/* Pipeline tab: where every app stands in the stores, and what is next.
 *
 * Three sources, joined by App Store app id:
 *   * data/portfolio.json (fetch.py)  - iOS versions + review state + latest build
 *   * data/pipeline.json  (pipeline.py) - Google Play release tracks
 *   * data/roadmap.json   (hand-kept by the Store Ops hub) - dated next steps + which
 *     apps build in CI. Public subset only: no names, clients or budgets.
 * Its own file for the same reason revenue.js and subs.js are. pl- class prefix throughout.
 */
(function () {
  const host = document.getElementById("pipeline");
  if (!host) return;

  const css = document.createElement("style");
  css.textContent = `
    .pl-h{font-size:15px;font-weight:700;margin:26px 2px 10px}
    .pl-h:first-child{margin-top:6px}
    .pl-note{color:var(--soft);font-size:12.5px;margin:-4px 2px 10px}
    .pl-card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
    .pl-row{display:grid;gap:6px 12px;padding:10px 14px;border-bottom:1px solid var(--line);
      font-size:13.5px;align-items:center;
      grid-template-columns:minmax(0,1.5fr) minmax(0,.8fr) minmax(0,1.4fr) minmax(0,.9fr) minmax(0,1.4fr) auto}
    .pl-row:last-child{border-bottom:0}
    .pl-row.pl-play4{grid-template-columns:minmax(0,1.5fr) repeat(3,minmax(0,1fr))}
    @media (max-width:720px){.pl-row.pl-play4{grid-template-columns:minmax(0,1fr)}}
    .pl-row.pl-head{font-size:11.5px;color:var(--soft);text-transform:uppercase;letter-spacing:.04em}
    .pl-app{font-weight:600;min-width:0;overflow-wrap:anywhere}
    .pl-mono{font-family:var(--mono);font-size:12.5px}
    .pl-b{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11.5px;font-weight:600;
      border:1px solid currentColor;white-space:nowrap;line-height:1.4}
    .pl-good{color:var(--good)} .pl-warn{color:var(--warn)} .pl-bad{color:var(--bad)}
    .pl-faint{color:var(--faint)} .pl-acc{color:var(--accent)} .pl-play{color:var(--play)}
    .pl-soft{color:var(--soft)}
    .pl-ci{display:flex;gap:4px;justify-content:flex-end;flex-wrap:wrap}
    .pl-sub{display:block;color:var(--soft);font-size:12px;margin-top:2px}
    .pl-item{display:grid;grid-template-columns:72px minmax(0,1fr) auto;gap:4px 12px;padding:9px 14px;
      border-bottom:1px solid var(--line);font-size:13.5px;align-items:baseline}
    .pl-item:last-child{border-bottom:0}
    .pl-date{font-family:var(--mono);font-size:12px;color:var(--soft)}
    .pl-item .pl-what{min-width:0}
    .pl-item .pl-what b{font-weight:600}
    .pl-done .pl-what{color:var(--faint);text-decoration:line-through}
    .pl-empty{padding:12px 14px;color:var(--faint);font-size:13px}
    @media (max-width:720px){
      .pl-row{grid-template-columns:minmax(0,1fr) auto}
      .pl-row.pl-head{display:none}
      .pl-row > .pl-col{grid-column:1 / -1}
      .pl-row > .pl-col::before{content:attr(data-l) " · ";color:var(--soft);font-size:11.5px}
      .pl-row > .pl-ci{grid-row:1;grid-column:2}
      .pl-item{grid-template-columns:60px minmax(0,1fr)}
      .pl-item .pl-who{grid-column:2}
    }`;
  document.head.appendChild(css);

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const NAMES = { "6796323432": "Houston BBQ Guide" };   // apps the installs tabs leave out
  const STATE = {
    READY_FOR_SALE: ["Live", "pl-good"], WAITING_FOR_REVIEW: ["Waiting for review", "pl-warn"],
    IN_REVIEW: ["In review", "pl-warn"], PENDING_DEVELOPER_RELEASE: ["Approved, release it", "pl-acc"],
    PENDING_APPLE_RELEASE: ["Releasing", "pl-acc"], PROCESSING_FOR_APP_STORE: ["Processing", "pl-acc"],
    PREPARE_FOR_SUBMISSION: ["Draft", "pl-faint"], DEVELOPER_REJECTED: ["Pulled", "pl-faint"],
    REJECTED: ["Rejected", "pl-bad"], METADATA_REJECTED: ["Metadata rejected", "pl-bad"],
    INVALID_BINARY: ["Invalid binary", "pl-bad"],
  };
  const WHO = { auto: ["Automated", "pl-acc"], you: ["You", "pl-warn"], apple: ["Apple", "pl-faint"], google: ["Google", "pl-play"] };
  const badge = ([label, cls]) => `<span class="pl-b ${cls}">${esc(label)}</span>`;

  async function load(path) {
    try { const r = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" }); return r.ok ? r.json() : null; }
    catch { return null; }
  }

  function iosCells(app) {
    const v = app?.versions || [];
    const live = v.find((x) => x.state === "READY_FOR_SALE");
    const top = v[0];
    const flight = top && top.state !== "READY_FOR_SALE" ? top : null;
    const b = (app?.builds || [])[0];
    return {
      live: live ? `<span class="pl-mono">${esc(live.version)}</span>` : '<span class="pl-faint">not live</span>',
      flight: flight ? `<span class="pl-mono">${esc(flight.version)}</span> ${badge(STATE[flight.state] || [flight.state, "pl-faint"])}`
                     : '<span class="pl-faint">nothing in flight</span>',
      build: b ? `<span class="pl-mono">${esc(b.version)}</span><span class="pl-sub">${esc(b.uploaded)}</span>` : "-",
    };
  }

  function playCell(p) {
    if (!p) return '<span class="pl-faint">iOS only</span>';
    if (p.error) return `<span class="pl-faint">${esc(p.error)}</span>`;
    const order = ["production", "beta", "alpha", "internal"];
    const label = { production: "Prod", beta: "Open", alpha: "Closed", internal: "Internal" };
    const parts = order.filter((t) => p.tracks?.[t]).map((t) => {
      const r = p.tracks[t];
      const pct = r.status === "inProgress" && r.fraction ? ` ${Math.round(r.fraction * 100)}%` : "";
      return `<span class="${t === "production" ? "pl-play" : "pl-soft"}">${label[t]} <span class="pl-mono">${esc(r.versionCode)}</span>${pct}</span>`;
    });
    const rev = p.unanswered ? ` <span class="pl-b pl-warn">${p.unanswered} to reply</span>` : "";
    return (parts.join(" · ") || '<span class="pl-faint">no releases</span>') + rev;
  }

  function ciCell(ci) {
    if (!ci) return '<span class="pl-ci"></span>';
    const one = (on, txt) => on === null || on === undefined ? "" :
      `<span class="pl-b ${on ? "pl-good" : "pl-faint"}" title="${on ? "builds and uploads from GitHub Actions" : "still built by hand"}">${txt}</span>`;
    return `<span class="pl-ci">${one(ci.ios, "iOS CI")}${one(ci.android, "Play CI")}</span>`;
  }

  function stores(portfolio, pipeline, roadmap) {
    const ids = new Set([...(portfolio?.apps || []).map((a) => a.id), ...Object.keys(roadmap?.ci || {})]);
    const byId = Object.fromEntries((portfolio?.apps || []).map((a) => [a.id, a]));
    const name = (id) => byId[id]?.name || NAMES[id] || id;
    const rank = (id) => {   // things needing attention first
      const s = byId[id]?.versions?.[0]?.state || "";
      return /REJECT|INVALID/.test(s) ? 0 : s === "PENDING_DEVELOPER_RELEASE" ? 1
        : /REVIEW|PROCESS|PENDING/.test(s) ? 2 : 3;
    };
    const rows = [...ids].sort((a, b) => rank(a) - rank(b) || name(a).localeCompare(name(b)));
    const head = `<div class="pl-row pl-head"><span>App</span><span>iOS live</span><span>In flight</span>
      <span>Latest build</span><span>Google Play</span><span></span></div>`;
    return head + rows.map((id) => {
      const c = iosCells(byId[id]);
      return `<div class="pl-row"><span class="pl-app">${esc(name(id))}</span>
        <span class="pl-col" data-l="iOS live">${c.live}</span>
        <span class="pl-col" data-l="In flight">${c.flight}</span>
        <span class="pl-col" data-l="Build">${c.build}</span>
        <span class="pl-col" data-l="Play">${playCell(pipeline?.play?.[id])}</span>
        ${ciCell(roadmap?.ci?.[id])}</div>`;
    }).join("");
  }

  // Google Play, last 28 days: the Console home's three numbers, from the same bulk
  // reports the Installs tab charts. Window = the 28 days ending at the last date Google
  // has published (it lags ~4-8 days), so the figures say "through <date>", not "today".
  function playHtml(portfolio, names) {
    const apps = (portfolio?.apps || []).filter((a) => a.android && Object.keys(a.android.installs || {}).length);
    if (!apps.length) return '<div class="pl-empty">No Google Play report data yet.</div>';
    const shift = (iso, n) => { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
    const sum = (obj, from, to) => Object.entries(obj || {}).reduce((t, [d, v]) => d >= from && d <= to ? t + (+v || 0) : t, 0);
    const through = portfolio?.play?.through || apps.map((a) => Object.keys(a.android.installs).sort().pop()).sort().pop();
    const revThrough = portfolio?.play?.revenueThrough || through;
    const from = shift(through, -27), prevFrom = shift(through, -55), prevTo = shift(through, -28);
    const rFrom = shift(revThrough, -27);
    const delta = (now, before) => {
      if (!before) return "";
      const pct = Math.round((now - before) / before * 100);
      return pct ? ` <span class="pl-sub" style="display:inline;color:var(--${pct > 0 ? "good" : "bad"})">${pct > 0 ? "▲" : "▼"} ${Math.abs(pct)}%</span>` : "";
    };
    const lastVal = (obj, upto) => { const d = Object.keys(obj || {}).filter((k) => k <= upto).sort().pop(); return d ? +obj[d] || 0 : null; };
    const money = (by) => {
      const parts = Object.entries(by || {}).map(([cur, days]) => [cur, sum(days, rFrom, revThrough)]).filter(([, v]) => v > 0);
      return parts.length ? parts.map(([c, v]) => v.toLocaleString(undefined, { style: "currency", currency: c })).join(" + ")
                          : '<span class="pl-faint">-</span>';
    };
    const rows = apps.map((a) => {
      const an = a.android;
      const acq = sum(an.installs, from, through), acqPrev = sum(an.installs, prevFrom, prevTo);
      const act = lastVal(an.active, through), actPrev = lastVal(an.active, prevTo);
      return { name: names[a.id] || a.name, acq, acqPrev, act, actPrev, rev: money(an.revenue) };
    }).sort((x, y) => y.acq - x.acq || x.name.localeCompare(y.name));
    const fmt = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
    return `<div class="pl-note">28 days through ${esc(fmt(through))} (Google publishes these reports a few days late);
        change vs the 28 days before. Revenue is Play earnings through ${esc(fmt(revThrough))}.
        "Active devices" is Play's active device installs on the last day, which runs higher than the Console's "monthly active devices".</div>
      <div class="pl-card">
        <div class="pl-row pl-head pl-play4"><span>App</span><span>Acquisitions</span><span>Active devices</span><span>Revenue</span></div>
        ${rows.map((r) => `<div class="pl-row pl-play4"><span class="pl-app">${esc(r.name)}</span>
          <span class="pl-col" data-l="Acquisitions"><span class="pl-mono">${r.acq}</span>${delta(r.acq, r.acqPrev)}</span>
          <span class="pl-col" data-l="Active devices"><span class="pl-mono">${r.act ?? "-"}</span>${r.act != null ? delta(r.act, r.actPrev) : ""}</span>
          <span class="pl-col" data-l="Revenue"><span class="pl-mono">${r.rev}</span></span></div>`).join("")}
      </div>`;
  }

  function roadmapHtml(roadmap, names) {
    const items = roadmap?.items || [];
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const day = (s) => { if (!s) return null; const d = new Date(s + "T00:00:00"); return isNaN(d) ? null : d; };
    const plus = (n) => { const d = new Date(today); d.setDate(d.getDate() + n); return d; };
    const in7 = plus(7), in21 = plus(21), ago14 = plus(-14);
    const groups = [
      ["Overdue", (it, d) => it.status === "todo" && d && d < today],
      ["This week", (it, d) => it.status === "todo" && d && d >= today && d < in7],
      ["Next two weeks", (it, d) => it.status === "todo" && d && d >= in7 && d < in21],
      ["Later", (it, d) => it.status === "todo" && (!d || d >= in21)],
      ["Blocked", (it) => it.status === "blocked"],
      ["Done recently", (it, d) => it.status === "done" && (!d || d >= ago14)],
    ];
    const fmt = (d) => d ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "—";
    return groups.map(([title, test]) => {
      const rows = items.map((it) => [it, day(it.date)]).filter(([it, d]) => test(it, d))
        .sort((x, y) => (x[1] || 0) - (y[1] || 0));
      if (!rows.length && title !== "This week") return "";
      const body = rows.length ? rows.map(([it, d]) => `
        <div class="pl-item${it.status === "done" ? " pl-done" : ""}">
          <span class="pl-date">${fmt(d)}</span>
          <span class="pl-what">${it.app ? `<b>${esc(names[it.app] || it.app)}</b> · ` : ""}${esc(it.title)}</span>
          <span class="pl-who">${badge(WHO[it.who] || [it.who, "pl-faint"])}</span>
        </div>`).join("") : '<div class="pl-empty">Nothing due.</div>';
      return `<div class="pl-h">${title}${title === "Overdue" ? ` <span class="pl-b pl-bad">${rows.length}</span>` : ""}</div>
        <div class="pl-card">${body}</div>`;
    }).join("");
  }

  (async () => {
    const [portfolio, pipeline, roadmap] = await Promise.all([
      load("data/portfolio.json"), load("data/pipeline.json"), load("data/roadmap.json")]);
    const names = { ...NAMES, ...Object.fromEntries((portfolio?.apps || []).map((a) => [a.id, a.name])) };
    const playAge = pipeline?.generated ? new Date(pipeline.generated).toLocaleString() : "not fetched yet";
    host.innerHTML = `
      <div class="pl-h">Release pipeline</div>
      <div class="pl-note">App Store state from App Store Connect; Google Play tracks as of ${esc(playAge)}.
        "CI" means the app builds and uploads from GitHub Actions.</div>
      <div class="pl-card">${stores(portfolio, pipeline, roadmap)}</div>
      <div class="pl-h">Google Play, last 28 days</div>
      ${playHtml(portfolio, names)}
      <div class="pl-h">Roadmap</div>
      <div class="pl-note">Updated ${esc(roadmap?.updated || "—")}. "Automated" items are done by the Store Ops automation; "You" needs a person.</div>
      ${roadmapHtml(roadmap, names)}`;
  })();
})();
