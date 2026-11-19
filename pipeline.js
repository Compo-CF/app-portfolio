/* Pipeline tab: where every app stands in the stores, and what is next.
 *
 * Sources, joined by App Store app id:
 *   * data/portfolio.json (fetch.py)    - iOS versions + review state + latest build, launched apps
 *   * data/pipeline.json  (pipeline.py) - Google Play release tracks, plus iOS versions/builds for
 *     pre-launch apps that fetch.py leaves out (`ios` key)
 *   * data/roadmap.json   (hand-kept by the Store Ops hub) - dated next steps + which apps build
 *     in CI. Public subset only: no names, clients or budgets.
 *
 * Layout: every table is one CSS grid whose rows are subgrids, so a column lines up across
 * all rows whatever is in it (each row used to size its own columns). Below 720px the rows
 * turn into labelled cards. pl- class prefix throughout.
 */
(function () {
  const host = document.getElementById("pipeline");
  if (!host) return;

  const css = document.createElement("style");
  css.textContent = `
    .pl-sec{margin:28px 0 0}
    .pl-sec:first-child{margin-top:6px}
    .pl-sech{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:0 2px 8px}
    .pl-h{font-size:15px;font-weight:700;margin:0}
    .pl-note{color:var(--soft);font-size:12.5px;margin:0 2px 10px;max-width:880px;line-height:1.45}
    .pl-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:6px 0 4px}
    .pl-kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
    .pl-kpi b{display:block;font-size:24px;font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums}
    .pl-kpi span{display:block;font-size:12px;color:var(--soft);margin-top:3px}
    .pl-card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
    .pl-grid{display:grid}
    .pl-grid.pl-stores{grid-template-columns:minmax(150px,1.5fr) 84px minmax(170px,1.3fr) 128px minmax(170px,1.5fr) 140px}
    .pl-grid.pl-play4{grid-template-columns:minmax(150px,2fr) repeat(3,minmax(110px,1fr))}
    .pl-grid.pl-road{grid-template-columns:64px minmax(110px,.9fr) minmax(0,3fr) 104px}
    .pl-row{display:grid;grid-template-columns:subgrid;grid-column:1 / -1;column-gap:14px;
      padding:10px 16px;border-top:1px solid var(--line);font-size:13.5px;align-items:center}
    .pl-row:hover{background:color-mix(in srgb,var(--line) 28%,transparent)}
    .pl-row.pl-head{border-top:0;font-size:11px;font-weight:700;color:var(--soft);text-transform:uppercase;
      letter-spacing:.06em;padding-top:9px;padding-bottom:9px;background:color-mix(in srgb,var(--line) 35%,transparent)}
    .pl-row.pl-head:hover{background:color-mix(in srgb,var(--line) 35%,transparent)}
    .pl-grp{grid-column:1 / -1;padding:8px 16px 6px;border-top:1px solid var(--line);font-size:11.5px;font-weight:700;
      color:var(--soft);text-transform:uppercase;letter-spacing:.06em;display:flex;gap:8px;align-items:center}
    .pl-grp .pl-n{font-family:var(--mono);font-weight:600;color:var(--faint)}
    .pl-r{text-align:right;justify-self:end}
    .pl-app{font-weight:600;min-width:0;overflow-wrap:anywhere}
    .pl-mono{font-family:var(--mono);font-size:12.5px;font-variant-numeric:tabular-nums}
    .pl-b{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;font-weight:600;
      border:1px solid currentColor;white-space:nowrap;line-height:1.5;vertical-align:middle}
    .pl-good{color:var(--good)} .pl-warn{color:var(--warn)} .pl-bad{color:var(--bad)}
    .pl-faint{color:var(--faint)} .pl-acc{color:var(--accent)} .pl-play{color:var(--play)}
    .pl-soft{color:var(--soft)}
    .pl-ci{display:flex;gap:4px;justify-content:flex-end;flex-wrap:wrap}
    .pl-sub{display:block;color:var(--soft);font-size:11.5px;margin-top:1px}
    .pl-flight{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
    .pl-delta{font-size:11.5px;margin-left:6px;white-space:nowrap}
    .pl-date{font-family:var(--mono);font-size:12px;color:var(--soft)}
    .pl-who-app{font-weight:600;min-width:0;overflow-wrap:anywhere}
    .pl-what{min-width:0;line-height:1.4}
    .pl-done .pl-what,.pl-done .pl-who-app{color:var(--faint)}
    .pl-done .pl-what{text-decoration:line-through}
    .pl-empty{padding:14px 16px;color:var(--faint);font-size:13px;border-top:1px solid var(--line)}
    .pl-tabs{display:flex;gap:5px;flex-wrap:wrap}
    .pl-tabs button{appearance:none;background:none;border:1px solid var(--line);color:var(--soft);border-radius:8px;
      padding:4px 10px;font:inherit;font-size:12px;font-weight:600;cursor:pointer}
    .pl-tabs button:hover{color:var(--ink)}
    .pl-tabs button[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
    .pl-tabs button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
    .pl-tabs .pl-n{font-family:var(--mono);opacity:.8;margin-left:4px}
    .pl-more{grid-column:1 / -1;border-top:1px solid var(--line);padding:8px 16px}
    .pl-more button{appearance:none;background:none;border:0;color:var(--accent);font:inherit;font-size:12.5px;
      font-weight:600;cursor:pointer;padding:0}
    @media (max-width:900px){.pl-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media (max-width:720px){
      .pl-grid{display:block}
      .pl-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;padding:12px 14px}
      .pl-row.pl-head{display:none}
      .pl-row > .pl-col{grid-column:1 / -1;justify-self:start;text-align:left}
      .pl-row > .pl-col[data-l]::before{content:attr(data-l);display:inline-block;min-width:92px;color:var(--soft);font-size:11.5px}
      .pl-row > .pl-ci{grid-row:1;grid-column:2}
      .pl-grid.pl-road .pl-row{grid-template-columns:52px minmax(0,1fr) auto}
      .pl-grid.pl-road .pl-who-app{grid-column:2;grid-row:1}
      .pl-grid.pl-road .pl-what{grid-column:2 / -1;grid-row:2}
      .pl-grid.pl-road .pl-owner{grid-column:3;grid-row:1}
      .pl-grp,.pl-more,.pl-empty{padding-left:14px;padding-right:14px}
      .pl-row .pl-sub{display:inline;margin-left:8px}
    }`;
  document.head.appendChild(css);

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // Display names that differ from the data (fetch.py keeps "Pupduko" as its key), and apps
  // the installs tabs leave out because they have not launched.
  const NAMES = { "6797896315": "Pupdoku", "6796323432": "Houston BBQ Guide", "6836013729": "Loop & Bloom", "6836925909": "Wanderdeck" };
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
  const n = (x) => `<span class="pl-n">${x}</span>`;

  async function load(path) {
    try { const r = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" }); return r.ok ? r.json() : null; }
    catch { return null; }
  }

  /* ---------- Release pipeline ---------- */

  function iosCells(app) {
    const v = app?.versions || [];
    const live = v.find((x) => x.state === "READY_FOR_SALE");
    const top = v[0];
    const flight = top && top.state !== "READY_FOR_SALE" ? top : null;
    const b = (app?.builds || [])[0];
    return {
      live: live ? `<span class="pl-mono">${esc(live.version)}</span>` : '<span class="pl-faint">not live</span>',
      flight: flight ? `<span class="pl-flight"><span class="pl-mono">${esc(flight.version)}</span>${badge(STATE[flight.state] || [flight.state, "pl-faint"])}</span>`
                     : '<span class="pl-faint">nothing in flight</span>',
      build: b ? `<span class="pl-mono">${esc(b.version)}</span><span class="pl-sub">${esc(b.uploaded)}</span>`
               : '<span class="pl-faint">no build yet</span>',
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
    return (parts.join(' <span class="pl-faint">·</span> ') || '<span class="pl-faint">no releases</span>') + rev;
  }

  function ciCell(ci) {
    if (!ci) return '<span class="pl-ci"></span>';
    const one = (on, txt) => on === null || on === undefined ? "" :
      `<span class="pl-b ${on ? "pl-good" : "pl-faint"}" title="${on ? "builds and uploads from GitHub Actions" : "still built by hand"}">${txt}</span>`;
    return `<span class="pl-ci">${one(ci.ios, "iOS CI")}${one(ci.android, "Play CI")}</span>`;
  }

  // Which group an app sits in; order of GROUPS is the order on the page.
  const GROUPS = [
    ["Needs attention", (s) => /REJECT|INVALID/.test(s) || s === "PENDING_DEVELOPER_RELEASE"],
    ["In review", (s) => /REVIEW|PROCESS|PENDING_APPLE/.test(s)],
    ["Pre-launch", (s, live) => !live],
    ["Live", () => true],
  ];

  function storeModel(portfolio, pipeline, roadmap) {
    const byId = Object.fromEntries((portfolio?.apps || []).map((a) => [a.id, a]));
    for (const [id, a] of Object.entries(pipeline?.ios || {})) if (!byId[id]) byId[id] = { id, ...a };
    const ids = new Set([...Object.keys(byId), ...Object.keys(roadmap?.ci || {})]);
    const name = (id) => NAMES[id] || byId[id]?.name || id;
    const groups = GROUPS.map(([title]) => [title, []]);
    for (const id of ids) {
      const v = byId[id]?.versions || [];
      const live = v.some((x) => x.state === "READY_FOR_SALE");
      const top = v[0]?.state || "";
      const gi = GROUPS.findIndex(([, test]) => test(top, live));
      groups[gi][1].push(id);
    }
    for (const g of groups) g[1].sort((a, b) => name(a).localeCompare(name(b)));
    return { byId, name, groups };
  }

  function storesHtml(model, pipeline, roadmap) {
    const head = `<div class="pl-row pl-head"><span>App</span><span>iOS live</span><span>In flight</span>
      <span>Latest build</span><span>Google Play</span><span class="pl-r">CI</span></div>`;
    const body = model.groups.filter(([, ids]) => ids.length).map(([title, ids]) =>
      `<div class="pl-grp">${esc(title)} ${n(ids.length)}</div>` + ids.map((id) => {
        const c = iosCells(model.byId[id]);
        return `<div class="pl-row"><span class="pl-app">${esc(model.name(id))}</span>
          <span class="pl-col" data-l="iOS live">${c.live}</span>
          <span class="pl-col" data-l="In flight">${c.flight}</span>
          <span class="pl-col" data-l="Latest build">${c.build}</span>
          <span class="pl-col" data-l="Google Play">${playCell(pipeline?.play?.[id])}</span>
          ${ciCell(roadmap?.ci?.[id])}</div>`;
      }).join("")).join("");
    return `<div class="pl-card"><div class="pl-grid pl-stores">${head}${body}</div></div>`;
  }

  /* ---------- Google Play, last 28 days ---------- */

  // The Console home's three numbers, from the same bulk reports the Installs tab charts.
  // Window = the 28 days ending at the last date Google has published (it lags ~4-8 days).
  function playHtml(portfolio, names) {
    const apps = (portfolio?.apps || []).filter((a) => a.android && Object.keys(a.android.installs || {}).length);
    if (!apps.length) return '<div class="pl-card"><div class="pl-empty">No Google Play report data yet.</div></div>';
    const shift = (iso, k) => { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + k); return d.toISOString().slice(0, 10); };
    const sum = (obj, from, to) => Object.entries(obj || {}).reduce((t, [d, v]) => d >= from && d <= to ? t + (+v || 0) : t, 0);
    const through = portfolio?.play?.through || apps.map((a) => Object.keys(a.android.installs).sort().pop()).sort().pop();
    const revThrough = portfolio?.play?.revenueThrough || through;
    const from = shift(through, -27), prevFrom = shift(through, -55), prevTo = shift(through, -28);
    const rFrom = shift(revThrough, -27);
    const delta = (now, before) => {
      if (!before) return "";
      const pct = Math.round((now - before) / before * 100);
      return pct ? `<span class="pl-delta" style="color:var(--${pct > 0 ? "good" : "bad"})">${pct > 0 ? "▲" : "▼"} ${Math.abs(pct)}%</span>` : "";
    };
    const lastVal = (obj, upto) => { const d = Object.keys(obj || {}).filter((k) => k <= upto).sort().pop(); return d ? +obj[d] || 0 : null; };
    const money = (by) => {
      const parts = Object.entries(by || {}).map(([cur, days]) => [cur, sum(days, rFrom, revThrough)]).filter(([, v]) => v > 0);
      return parts.length ? `<span class="pl-mono">${parts.map(([c, v]) => v.toLocaleString(undefined, { style: "currency", currency: c })).join(" + ")}</span>`
                          : '<span class="pl-faint">–</span>';
    };
    const rows = apps.map((a) => {
      const an = a.android;
      const acq = sum(an.installs, from, through), acqPrev = sum(an.installs, prevFrom, prevTo);
      const act = lastVal(an.active, through), actPrev = lastVal(an.active, prevTo);
      return { name: NAMES[a.id] || names[a.id] || a.name, acq, acqPrev, act, actPrev, rev: money(an.revenue) };
    }).sort((x, y) => y.acq - x.acq || x.name.localeCompare(y.name));
    const fmt = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
    return `<div class="pl-note">28 days through ${esc(fmt(through))}, compared with the 28 days before. Google publishes these
        reports a few days late. Revenue is Play earnings through ${esc(fmt(revThrough))}. "Active devices" is Play's active
        device installs on the last day, which runs higher than the Console's "monthly active devices".</div>
      <div class="pl-card"><div class="pl-grid pl-play4">
        <div class="pl-row pl-head"><span>App</span><span class="pl-r">Acquisitions</span><span class="pl-r">Active devices</span><span class="pl-r">Revenue</span></div>
        ${rows.map((r) => `<div class="pl-row"><span class="pl-app">${esc(r.name)}</span>
          <span class="pl-col pl-r" data-l="Acquisitions"><span class="pl-mono">${r.acq}</span>${delta(r.acq, r.acqPrev)}</span>
          <span class="pl-col pl-r" data-l="Active devices"><span class="pl-mono">${r.act ?? "–"}</span>${r.act != null ? delta(r.act, r.actPrev) : ""}</span>
          <span class="pl-col pl-r" data-l="Revenue">${r.rev}</span></div>`).join("")}
      </div></div>`;
  }

  /* ---------- Roadmap ---------- */

  const LIMIT = 6;          // rows shown per group before "Show all"
  const VIEW_KEY = "portfolio.pipeline.view";
  let view = "open";
  try { view = localStorage.getItem(VIEW_KEY) || "open"; } catch { /* storage blocked */ }
  const expanded = new Set();

  function roadmapGroups(roadmap) {
    const items = roadmap?.items || [];
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const day = (s) => { if (!s) return null; const d = new Date(s + "T00:00:00"); return isNaN(d) ? null : d; };
    const plus = (k) => { const d = new Date(today); d.setDate(d.getDate() + k); return d; };
    const in7 = plus(7), in21 = plus(21), ago14 = plus(-14);
    const defs = {
      open: [
        ["Overdue", (it, d) => it.status === "todo" && d && d < today],
        ["This week", (it, d) => it.status === "todo" && d && d >= today && d < in7],
        ["Next two weeks", (it, d) => it.status === "todo" && d && d >= in7 && d < in21],
        ["Later", (it, d) => it.status === "todo" && (!d || d >= in21)],
      ],
      blocked: [["Blocked", (it) => it.status === "blocked"]],
      done: [["Done in the last two weeks", (it, d) => it.status === "done" && (!d || d >= ago14)]],
    };
    const out = {};
    for (const [key, groups] of Object.entries(defs)) {
      out[key] = groups.map(([title, test]) => [title, items.map((it) => [it, day(it.date)]).filter(([it, d]) => test(it, d))
        .sort((x, y) => key === "done" ? (y[1] || 0) - (x[1] || 0) : (x[1] || 0) - (y[1] || 0))]);
    }
    return out;
  }

  function roadmapHtml(groups, names) {
    const fmt = (d) => d ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "—";
    const count = (k) => groups[k].reduce((t, [, rows]) => t + rows.length, 0);
    const tabs = [["open", "Open"], ["blocked", "Blocked"], ["done", "Done"]].map(([k, label]) =>
      `<button type="button" data-view="${k}" aria-pressed="${view === k}">${label}${n(count(k))}</button>`).join("");
    const head = `<div class="pl-row pl-head"><span>Date</span><span>App</span><span>Task</span><span class="pl-r">Owner</span></div>`;
    const body = groups[view].filter(([title, rows]) => rows.length || title === "This week").map(([title, rows]) => {
      const open = expanded.has(title);
      const shown = open ? rows : rows.slice(0, LIMIT);
      const list = shown.map(([it, d]) => `
        <div class="pl-row${it.status === "done" ? " pl-done" : ""}">
          <span class="pl-date">${fmt(d)}</span>
          <span class="pl-who-app">${it.app ? esc(names[it.app] || it.app) : '<span class="pl-faint">All apps</span>'}</span>
          <span class="pl-what">${esc(it.title)}</span>
          <span class="pl-owner pl-r">${badge(WHO[it.who] || [it.who, "pl-faint"])}</span>
        </div>`).join("") || '<div class="pl-empty">Nothing due.</div>';
      const more = rows.length > LIMIT
        ? `<div class="pl-more"><button type="button" data-more="${esc(title)}">${open ? "Show fewer" : `Show all ${rows.length}`}</button></div>` : "";
      const flag = title === "Overdue" && rows.length ? ` <span class="pl-b pl-bad">${rows.length}</span>` : n(rows.length);
      return `<div class="pl-grp">${esc(title)} ${flag}</div>${list}${more}`;
    }).join("") || '<div class="pl-empty">Nothing here.</div>';
    return { tabs, table: `<div class="pl-card"><div class="pl-grid pl-road">${head}${body}</div></div>` };
  }

  /* ---------- Page ---------- */

  (async () => {
    const [portfolio, pipeline, roadmap] = await Promise.all([
      load("data/portfolio.json"), load("data/pipeline.json"), load("data/roadmap.json")]);
    const names = { ...Object.fromEntries((portfolio?.apps || []).map((a) => [a.id, a.name])),
                    ...Object.fromEntries(Object.entries(pipeline?.ios || {}).map(([id, a]) => [id, a.name])), ...NAMES };
    const model = storeModel(portfolio, pipeline, roadmap);
    const groups = roadmapGroups(roadmap);
    const size = (title) => (model.groups.find(([t]) => t === title)?.[1] || []).length;
    const live = model.groups.filter(([t]) => t !== "Pre-launch").reduce((t, [, ids]) =>
      t + ids.filter((id) => (model.byId[id]?.versions || []).some((v) => v.state === "READY_FOR_SALE")).length, 0);
    const overdue = groups.open[0][1].length, week = groups.open[1][1].length;
    const playAge = pipeline?.generated ? new Date(pipeline.generated).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "not fetched yet";

    const kpis = `<div class="pl-kpis">
      <div class="pl-kpi"><b>${live}</b><span>Live on the App Store</span></div>
      <div class="pl-kpi"><b class="${size("In review") ? "pl-warn" : ""}">${size("In review") + size("Needs attention")}</b><span>In review or awaiting release</span></div>
      <div class="pl-kpi"><b>${size("Pre-launch")}</b><span>Pre-launch</span></div>
      <div class="pl-kpi"><b class="${overdue ? "pl-bad" : ""}">${overdue ? overdue : week}</b><span>${overdue ? "Overdue tasks" : "Tasks due this week"}</span></div>
    </div>`;

    const render = () => {
      const rm = roadmapHtml(groups, names);
      host.innerHTML = `
        <div class="pl-sec">${kpis}</div>
        <div class="pl-sec">
          <div class="pl-sech"><div class="pl-h">Release pipeline</div></div>
          <div class="pl-note">App Store state from App Store Connect; Google Play tracks as of ${esc(playAge)}.
            CI badges mean the app builds and uploads from GitHub Actions.</div>
          ${storesHtml(model, pipeline, roadmap)}
        </div>
        <div class="pl-sec">
          <div class="pl-sech"><div class="pl-h">Google Play, last 28 days</div></div>
          ${playHtml(portfolio, names)}
        </div>
        <div class="pl-sec" id="pl-roadmap">
          <div class="pl-sech"><div class="pl-h">Roadmap</div><div class="pl-tabs" role="group" aria-label="Roadmap view">${rm.tabs}</div></div>
          <div class="pl-note">Updated ${esc(roadmap?.updated || "—")}. "Automated" items are done by the Store Ops automation; "You" needs a person.</div>
          ${rm.table}
        </div>`;
    };
    render();

    host.addEventListener("click", (e) => {
      const v = e.target.closest("[data-view]"), m = e.target.closest("[data-more]");
      if (!v && !m) return;
      if (v) { view = v.dataset.view; try { localStorage.setItem(VIEW_KEY, view); } catch { /* storage blocked */ } }
      if (m) { const t = m.dataset.more; expanded.has(t) ? expanded.delete(t) : expanded.add(t); }
      const y = window.scrollY;
      render();
      window.scrollTo(0, y);
    });
  })();
})();
