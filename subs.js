/* Subscriptions tab: how many people are subscribed, and what it earns.
 *
 * Reads data/subs.json (scripts/subs.py) for counts and data/revenue.json for
 * the money, so both halves of a subscription sit together. Its own file for
 * the same reason revenue.js is: nothing here needs portfolio.json.
 *
 * The two stores are kept apart on purpose, because what is known about each
 * is not the same kind of thing:
 *
 *   * **Google Play** is derived. Google publishes no subscription report in
 *     the reporting bucket for this account - stats/ holds crashes, installs
 *     and store_performance and nothing else, confirmed by a run rather than
 *     assumed - so renewals are counted from charges in the earnings reports.
 *     A charge is a renewal that was paid. It cannot see someone who has
 *     cancelled but not yet lapsed, nor someone in a free trial paying
 *     nothing, and the page says so rather than implying Google reported it.
 *   * **Apple** is absent. It refuses SUBSCRIPTION and SUBSCRIPTION_EVENT for
 *     this account with "Invalid vendor number specified", while the same
 *     vendor number, credentials and code fetch a SALES report in the same
 *     run - a control subs.py performs and logs. So the number of iOS
 *     subscribers is unknown, which is a different fact from zero, and this
 *     tab draws that difference instead of a reassuring 0.
 */
(function () {
  const host = document.getElementById("subs");
  if (!host) return;

  const css = document.createElement("style");
  css.textContent = `
    /* sb- throughout: revenue.js injects rv- rules globally, and an
       unprefixed class earlier in this project silently restyled the map's
       legend swatches. */
    .sb-h{font-size:15px;font-weight:700;margin:26px 2px 10px}
    .sb-card{background:var(--card);border:1px solid var(--line);
      border-radius:12px;padding:16px 18px}
    .sb-grid{display:grid;gap:8px;padding:9px 14px;font-size:13.5px;
      font-variant-numeric:tabular-nums;border-bottom:1px solid var(--line);
      align-items:center;grid-template-columns:minmax(0,1.8fr) repeat(3,minmax(0,1fr))}
    .sb-grid:last-child{border-bottom:0}
    .sb-grid > :not(:first-child){text-align:right}
    .sb-grid.head{color:var(--faint);font-size:11px;text-transform:uppercase;
      letter-spacing:.07em;font-weight:700}
    .sb-grid small{display:block;color:var(--faint);font-size:11px;font-weight:400}
    .sb-bars{display:grid;gap:7px}
    .sb-bar{display:grid;grid-template-columns:76px 1fr 52px;gap:10px;
      align-items:center;font-size:13px;font-variant-numeric:tabular-nums}
    .sb-bar .t{height:16px;border-radius:5px;background:var(--line)}
    .sb-bar .t i{display:block;height:100%;border-radius:5px;background:var(--play)}
    .sb-bar b{text-align:right}
    /* The unknown case, which must never look like a measured zero. */
    .sb-none{border-style:dashed}
    .sb-none b{font-style:italic}
    @media (max-width:640px){
      .sb-grid{font-size:12.5px;padding:8px 10px;gap:6px}
      .sb-bar{grid-template-columns:62px 1fr 44px}
    }`;
  document.head.appendChild(css);

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const usd = (n) => "$" + n.toLocaleString("en-US",
    {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const monthLabel = (ym) => `${MON[+ym.split("-")[1] - 1]} ${ym.split("-")[0]}`;

  function render(subs, rev) {
    const play = subs.play || {};
    const months = play.months || {};
    const keys = Object.keys(months).sort();
    const names = play.names || {};

    // Money per subscription from the Revenue tab's own source, so the two
    // tabs cannot end up disagreeing about what a subscription earned.
    const paid = {};
    for (const r of ((rev || {}).play || {}).products || []) {
      if (!/subscription/i.test(r.kind || "")) continue;
      paid[r.sku] = Object.values(r.currency || {})
        .reduce((n, days) => n + Object.values(days).reduce((m, v) => m + v, 0), 0);
    }

    const latest = keys[keys.length - 1];
    const live = Object.values(months[latest] || {})
      .reduce((n, v) => n + (v.charges || 0), 0);
    const everCharges = keys.reduce((n, k) => n + Object.values(months[k])
      .reduce((m, v) => m + (v.charges || 0), 0), 0);
    const everPaid = Object.values(paid).reduce((n, v) => n + v, 0);
    const skus = new Set(keys.flatMap((k) => Object.keys(months[k])));

    let html = `<div class="totals">
      <div class="stat"><b>${live}</b><span>Paying in ${
        latest ? esc(monthLabel(latest)) : "—"}</span>
        <em>Google Play, counted from charges</em></div>
      <div class="stat"><b>${everCharges}</b><span>Charges, all time</span>
        <em>${skus.size} subscription${skus.size === 1 ? "" : "s"} across ${
          keys.length} month${keys.length === 1 ? "" : "s"}</em></div>
      <div class="stat"><b>${usd(everPaid)}</b><span>Earned, net of fee</span>
        <em>After Google's cut</em></div>
      <div class="stat sb-none"><b>unknown</b><span>App Store subscribers</span>
        <em>Apple serves no report for this account</em></div>
    </div>`;

    /* ---- Google Play ---- */
    html += `<div class="sb-h">Google Play</div><div class="months">
      <div class="sb-grid head"><span>Month</span><span>Subscription</span>
        <span>Charges</span><span>Earned</span></div>`;
    for (const k of [...keys].reverse()) {
      for (const [sku, v] of Object.entries(months[k])) {
        html += `<div class="sb-grid">
          <span>${esc(monthLabel(k))}<small>${esc(names[sku] || sku)}</small></span>
          <span style="font-size:12px;color:var(--soft)" title="${esc(sku)}">${esc(sku)}</span>
          <span title="${esc((v.days || []).join(", "))}">${v.charges}</span>
          <span>${paid[sku] === undefined ? "—" : usd(paid[sku])}</span>
        </div>`;
      }
    }
    html += `</div>`;

    if (keys.length > 1) {
      const peak = Math.max(...keys.map((k) => Object.values(months[k])
        .reduce((n, v) => n + (v.charges || 0), 0)), 1);
      html += `<div class="sb-h">Charges by month</div>
        <div class="sb-card"><div class="sb-bars">`;
      for (const k of keys) {
        const c = Object.values(months[k]).reduce((n, v) => n + (v.charges || 0), 0);
        html += `<div class="sb-bar"><span>${esc(monthLabel(k))}</span>
          <div class="t"><i style="width:${(c / peak) * 100}%"></i></div>
          <b>${c}</b></div>`;
      }
      html += `</div></div>`;
    }

    html += `<div class="caveat"><b>Derived, not reported.</b> Google
      publishes no subscription report in the reporting bucket for this
      account &mdash; <code>stats/</code> holds crashes, installs and
      store_performance only, which a run confirmed rather than assumed. These
      counts come from charges in the monthly earnings reports, so a charge is
      a renewal that was paid. Someone who has cancelled but not yet lapsed
      still appears until their last paid period ends, and someone in a free
      trial does not appear at all. Covered through
      <b>${esc(play.through || "—")}</b>; earnings are filed monthly and in
      arrears, so the current month appears only once Google publishes it.</div>`;

    /* ---- Apple ---- */
    const missing = (subs.apple || {}).unavailable || [];
    html += `<div class="sb-h">App Store</div>
      <div class="sb-card sb-none">
        <b style="font-size:22px;display:block;color:var(--soft)">No report available</b>
        ${missing.length ? `<p style="margin:.5em 0 0;color:var(--soft)">Apple
          refuses ${missing.map((m) => `<code>${esc(m)}</code>`).join(" and ")}
          for this account, answering <i>&ldquo;Invalid vendor number
          specified&rdquo;</i>. That error names the wrong thing: the same
          vendor number, credentials and code fetch a SALES report
          successfully in the same run, which <code>subs.py</code> re-checks
          and logs as a control every time.</p>`
          : `<p style="margin:.5em 0 0;color:var(--soft)">Apple has published no
             subscription report for this account.</p>`}
        <p style="margin:.6em 0 0;color:var(--soft)">No iOS subscription has
        ever sold &mdash; no subscription SKU appears in any Apple financial
        report &mdash; which is the likeliest reason the report type does not
        exist for this vendor. The number of App Store subscribers is therefore
        <b>unknown</b>, which is not the same as zero, and nothing here draws it
        as one.</p>
      </div>`;

    host.innerHTML = html;
  }

  Promise.all([
    fetch("data/subs.json", {cache: "no-cache"}).then((r) => (r.ok ? r.json() : null)),
    fetch("data/revenue.json", {cache: "no-cache"}).then((r) => (r.ok ? r.json() : null)),
  ]).then(([subs, rev]) => {
    if (!subs) throw new Error("no subscription data file");
    render(subs, rev);
  }).catch((e) => {
    host.innerHTML = `<p class="caveat">Subscription data could not be loaded. ${esc(e.message)}</p>`;
  });
})();
