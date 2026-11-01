// Boron license admin portal. Talks only to the same-origin admin API with a
// bearer token held in sessionStorage; every value from the API is rendered
// with textContent, never as HTML.
"use strict";

(function () {
  const TOKEN_KEY = "boron-license-admin-token";
  const $ = (id) => document.getElementById(id);
  const state = { tiers: [], current: null, issuedId: null };

  const ERRORS = {
    unauthorized: "The admin token was rejected. Sign in again.",
    license_not_found: "That license no longer exists.",
    unknown_tier: "Unknown tier.",
    invalid_tier_name: "Tier names use lowercase letters, digits and dashes.",
    invalid_max_accounts: "The account limit must be at least 1.",
    invalid_customer_name: "Enter the customer name.",
    invalid_customer_email: "Enter a valid customer email.",
    invalid_channel: "Unknown update channel.",
    invalid_ip: "That is not a valid IP address.",
    invalid_status: "Unknown status.",
  };

  // --- Token and API -------------------------------------------------------

  function getToken() {
    try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return memoryToken; }
  }
  let memoryToken = "";
  function setToken(value) {
    memoryToken = value;
    try {
      if (value) sessionStorage.setItem(TOKEN_KEY, value); else sessionStorage.removeItem(TOKEN_KEY);
    } catch (e) { /* storage blocked: keep the token in memory for this page only */ }
  }

  class ApiError extends Error {
    constructor(code, status) { super(ERRORS[code] || code || ("HTTP " + status)); this.code = code; this.status = status; }
  }

  async function api(method, path, body) {
    const headers = { "Authorization": "Bearer " + getToken(), "Accept": "application/json" };
    const init = { method, headers, cache: "no-store", credentials: "omit", redirect: "error" };
    if (body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    let response;
    try { response = await fetch(path, init); } catch (e) { throw new ApiError("Network error. Try again.", 0); }
    let data = null;
    try { data = await response.json(); } catch (e) { data = null; }
    if (!response.ok) {
      let code = data && typeof data.error === "string" ? data.error : "";
      if (!code && data && Array.isArray(data.detail)) {
        code = "Check the form: " + data.detail.map((d) => (d.loc || []).slice(-1)[0]).filter(Boolean).join(", ");
      }
      const error = new ApiError(code, response.status);
      if (response.status === 401) { signOut(error.message); }
      throw error;
    }
    return data;
  }

  // --- Small DOM helpers --------------------------------------------------

  function el(tag, opts, children) {
    const node = document.createElement(tag);
    if (opts) {
      if (opts.text !== undefined && opts.text !== null) node.textContent = String(opts.text);
      if (opts.cls) node.className = opts.cls;
      if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
    }
    for (const child of children || []) if (child) node.appendChild(child);
    return node;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? String(iso) : d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  }
  function fmtDay(iso) { return iso ? String(iso).slice(0, 10) : ""; }
  function endOfDay(day) { return day ? day + "T23:59:59Z" : null; }
  function badge(status) { return el("span", { text: status, cls: "badge " + status }); }

  let toastTimer = 0;
  function toast(message, isError) {
    const node = $("toast");
    node.textContent = message;
    node.className = "toast" + (isError ? " error" : "");
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, isError ? 6000 : 3000);
  }

  // Disable a form's buttons while a request runs; report failures.
  async function busy(container, task) {
    const buttons = Array.from(container.querySelectorAll("button"));
    buttons.forEach((b) => { b.disabled = true; });
    try { return await task(); } catch (e) { if (e.status !== 401) toast(e.message, true); return undefined; }
    finally { buttons.forEach((b) => { b.disabled = false; }); }
  }

  // --- Session ------------------------------------------------------------

  function signOut(message) {
    setToken("");
    state.current = null;
    $("app").hidden = true;
    $("session").hidden = true;
    $("login").hidden = false;
    $("token").value = "";
    $("token").focus();
    if (message) toast(message, true);
  }

  async function connect() {
    const me = await api("GET", "/v1/admin/me");
    $("session-name").textContent = "Token: " + me.token;
    $("session").hidden = false;
    $("login").hidden = true;
    $("app").hidden = false;
    await loadTiers();
    show("licenses");
    await searchLicenses();
  }

  // --- Views --------------------------------------------------------------

  function show(view) {
    for (const name of ["licenses", "detail", "issue", "tiers"]) $("view-" + name).hidden = name !== view;
    const tabView = view === "detail" ? "licenses" : view;
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.setAttribute("aria-selected", String(tab.dataset.view === tabView));
    });
    if (view === "issue") $("i-name").focus();
  }

  function fillTierSelect(select, selected) {
    clear(select);
    for (const tier of state.tiers) {
      const label = tier.name + " (" + (tier.max_accounts === null ? "unlimited" : tier.max_accounts + " accounts") + ")";
      const option = el("option", { text: label, attrs: { value: tier.name } });
      if (tier.name === selected) option.selected = true;
      select.appendChild(option);
    }
  }

  async function loadTiers() {
    state.tiers = await api("GET", "/v1/admin/tiers");
    const body = $("tier-rows");
    clear(body);
    for (const tier of state.tiers) {
      body.appendChild(el("tr", null, [
        el("td", { text: tier.name }),
        el("td", { text: tier.max_accounts === null ? "Unlimited" : tier.max_accounts }),
      ]));
    }
    fillTierSelect($("i-tier"), $("i-tier").value || "pro");
    if (state.current) fillTierSelect($("e-tier"), state.current.tier);
  }

  async function searchLicenses() {
    const params = new URLSearchParams({ q: $("search-q").value.trim(), limit: "200" });
    const status = $("search-status").value;
    if (status) params.set("status", status);
    const rows = await api("GET", "/v1/admin/licenses?" + params.toString());
    const body = $("license-rows");
    clear(body);
    for (const lic of rows) {
      const row = el("tr", { attrs: { tabindex: "0" } }, [
        el("td", { text: lic.id }),
        el("td", null, [el("span", { text: lic.customer_name }), el("span", { text: lic.customer_email, cls: "sub" })]),
        el("td", { text: lic.tier }),
        el("td", null, [badge(lic.effective_status)]),
        el("td", { text: lic.bound_ip || "unbound" }),
        el("td", { text: lic.channel }),
        el("td", { text: lic.expires_at ? fmtDay(lic.expires_at) : "never" }),
        el("td", { text: fmtDate(lic.last_check_at) }),
      ]);
      const open = () => openLicense(lic.id);
      row.addEventListener("click", open);
      row.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
      body.appendChild(row);
    }
    $("license-empty").hidden = rows.length > 0;
  }

  async function openLicense(id) {
    const [shown, events] = await Promise.all([
      api("GET", "/v1/admin/licenses/" + id),
      api("GET", "/v1/admin/licenses/" + id + "/events?limit=100"),
    ]);
    renderLicense(shown.license, shown.installations, events);
    show("detail");
    window.scrollTo(0, 0);
  }

  function renderLicense(lic, installs, events) {
    state.current = lic;
    $("d-title").textContent = "#" + lic.id + " " + lic.customer_name;
    $("d-subtitle").textContent = lic.customer_email + " · key …" + lic.key_hint;
    const status = $("d-status");
    status.textContent = lic.effective_status;
    status.className = "badge " + lic.effective_status;

    const facts = $("d-facts");
    clear(facts);
    const pairs = [
      ["Tier", lic.tier + " (" + (lic.max_accounts === null ? "unlimited" : lic.max_accounts + " accounts") + ")"],
      ["Stored status", lic.status],
      ["Server IP", lic.bound_ip || "unbound (binds on next activation)"],
      ["Update channel", lic.channel],
      ["Expires", lic.expires_at ? fmtDate(lic.expires_at) : "never"],
      ["Created", fmtDate(lic.created_at)],
      ["Last check", fmtDate(lic.last_check_at)],
    ];
    for (const [term, value] of pairs) facts.appendChild(el("div", null, [el("dt", { text: term }), el("dd", { text: value })]));
    $("d-notes").textContent = lic.notes || "";
    $("d-notes").hidden = !lic.notes;

    fillTierSelect($("e-tier"), lic.tier);
    $("e-channel").value = lic.channel;
    $("e-expires").value = fmtDay(lic.expires_at);
    $("e-no-expiry").checked = !lic.expires_at;
    $("e-expires").disabled = !lic.expires_at;
    $("e-name").value = lic.customer_name;
    $("e-email").value = lic.customer_email;
    $("e-notes").value = lic.notes || "";
    $("rebind-ip").value = lic.bound_ip || "";
    $("status-reason").value = "";

    const installBody = $("d-installs");
    clear(installBody);
    for (const row of installs) {
      installBody.appendChild(el("tr", null, [
        el("td", null, [el("code", { text: row.install_id })]), el("td", { text: row.ip }),
        el("td", { text: row.hostname || "—" }), el("td", { text: row.version || "—" }),
        el("td", { text: row.account_count === null ? "—" : row.account_count }),
        el("td", { text: fmtDate(row.first_seen) }), el("td", { text: fmtDate(row.last_seen) }),
      ]));
    }
    $("d-installs-empty").hidden = installs.length > 0;

    const eventBody = $("d-events");
    clear(eventBody);
    for (const row of events) {
      const detail = Object.entries(row.detail || {})
        .filter(([, v]) => v !== null && v !== "")
        .map(([k, v]) => k + "=" + (typeof v === "object" ? JSON.stringify(v) : String(v))).join("  ");
      eventBody.appendChild(el("tr", null, [
        el("td", { text: fmtDate(row.at) }), el("td", { text: row.action }), el("td", { text: row.actor }),
        el("td", { text: row.ip || "—" }), el("td", { text: detail || "—", cls: "wrap" }),
      ]));
    }
    $("d-events-empty").hidden = events.length > 0;
  }

  async function refreshCurrent() { if (state.current) await openLicense(state.current.id); }

  // --- Wiring -------------------------------------------------------------

  function on(id, event, handler) {
    $(id).addEventListener(event, (e) => {
      if (event === "submit") e.preventDefault();
      handler(e);
    });
  }

  on("login-form", "submit", () => busy($("login-form"), async () => {
    setToken($("token").value.trim());
    // A 401 already signed out with its own message.
    try { await connect(); } catch (e) { if (e.status !== 401) signOut(e.message); }
  }));
  on("sign-out", "click", () => signOut());

  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
    show(tab.dataset.view);
    if (tab.dataset.view === "licenses") busy($("search-form"), searchLicenses);
    if (tab.dataset.view === "tiers") busy($("tier-form"), loadTiers);
  }));

  on("search-form", "submit", () => busy($("search-form"), searchLicenses));
  on("back", "click", () => { show("licenses"); busy($("search-form"), searchLicenses); });
  on("reload-detail", "click", () => busy($("view-detail"), refreshCurrent));

  document.querySelectorAll("[data-status]").forEach((button) => button.addEventListener("click", () => {
    const action = button.dataset.status;
    const lic = state.current;
    if (!lic) return;
    if (action === "cancel" && !window.confirm("Cancel license #" + lic.id + " for " + lic.customer_name +
        "? The panel locks its admin area and stops receiving updates.")) return;
    busy($("status-form"), async () => {
      await api("POST", "/v1/admin/licenses/" + lic.id + "/" + action, { reason: $("status-reason").value.trim() });
      toast("License " + (action === "resume" ? "resumed" : action === "cancel" ? "cancelled" : "suspended") + ".");
      await refreshCurrent();
    });
  }));

  on("rebind-form", "submit", () => busy($("rebind-form"), async () => {
    const ip = $("rebind-ip").value.trim();
    if (!ip) { toast("Enter an IP address, or use Clear.", true); return; }
    await api("POST", "/v1/admin/licenses/" + state.current.id + "/rebind", { ip });
    toast("License bound to " + ip + ".");
    await refreshCurrent();
  }));
  on("rebind-clear", "click", () => {
    if (!window.confirm("Clear the server IP? The next activation from any server binds this license.")) return;
    busy($("rebind-form"), async () => {
      await api("POST", "/v1/admin/licenses/" + state.current.id + "/rebind", { ip: null });
      toast("IP binding cleared.");
      await refreshCurrent();
    });
  });

  on("e-no-expiry", "change", () => { $("e-expires").disabled = $("e-no-expiry").checked; });
  on("edit-form", "submit", () => busy($("edit-form"), async () => {
    const body = {
      tier: $("e-tier").value, channel: $("e-channel").value, customer_name: $("e-name").value.trim(),
      customer_email: $("e-email").value.trim(), notes: $("e-notes").value,
    };
    if ($("e-no-expiry").checked) body.clear_expiry = true;
    else if ($("e-expires").value) body.expires_at = endOfDay($("e-expires").value);
    await api("PATCH", "/v1/admin/licenses/" + state.current.id, body);
    toast("Changes saved.");
    await refreshCurrent();
  }));

  on("issue-form", "submit", () => busy($("issue-form"), async () => {
    const body = {
      tier: $("i-tier").value, customer_name: $("i-name").value.trim(), customer_email: $("i-email").value.trim(),
      channel: $("i-channel").value, notes: $("i-notes").value, expires_at: endOfDay($("i-expires").value),
      bound_ip: $("i-ip").value.trim() || null,
    };
    const created = await api("POST", "/v1/admin/licenses", body);
    state.issuedId = created.license.id;
    $("issued-key").textContent = created.license_key;
    $("issued").hidden = false;
    $("issue-form").reset();
    fillTierSelect($("i-tier"), "pro");
    $("issued").scrollIntoView({ behavior: "smooth", block: "center" });
  }));
  on("copy-key", "click", async () => {
    const key = $("issued-key").textContent;
    try {
      await navigator.clipboard.writeText(key);
      toast("Key copied to the clipboard.");
    } catch (e) {
      const range = document.createRange();
      range.selectNodeContents($("issued-key"));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      toast("Press Ctrl+C (or long-press) to copy the selected key.");
    }
  });
  on("open-issued", "click", () => {
    if (state.issuedId === null) return;
    // Leaving the page forgets the key: hide it now so it is not shown again.
    $("issued").hidden = true;
    $("issued-key").textContent = "";
    busy($("view-issue"), () => openLicense(state.issuedId));
  });

  on("tier-form", "submit", () => busy($("tier-form"), async () => {
    const max = $("t-max").value.trim();
    await api("POST", "/v1/admin/tiers", { name: $("t-name").value.trim().toLowerCase(), max_accounts: max ? Number(max) : null });
    $("tier-form").reset();
    toast("Tier saved.");
    await loadTiers();
  }));

  // --- Start --------------------------------------------------------------

  if (getToken()) {
    connect().catch((e) => { if (e.status !== 401) signOut(e.message); });
  } else {
    signOut();
  }
})();
