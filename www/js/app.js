"use strict";
/* Saxon ROR browser — vanilla JS, no dependencies, no build step.
 * Works against the v2 ROR schema served in data/records.json. */

(function () {
  // ---- State --------------------------------------------------------------
  const state = {
    lang: "en",
    records: [],
    byId: new Map(), // ror suffix -> record
    meta: null,
    dataBase: "data", // resolved at load time
    issues: null, // object: suffix -> [{number,title,state,url}] (best-effort)
    openalex: null, // Map: suffix -> entity (lazy, per record)
    filters: { q: "", types: new Set(), city: "", statuses: new Set(["active"]) },
  };

  const ALL_TYPES = [
    "education", "funder", "healthcare", "company", "archive",
    "nonprofit", "government", "facility", "other",
  ];
  const ALL_STATUSES = ["active", "inactive", "withdrawn"];

  // ROR's curation tracker — records are corrected upstream there, and each
  // record links to its curation requests (see data/curation.json). Corrections
  // can be filed via ROR's web form or directly on GitHub.
  const ROR_UPDATES_URL = "https://github.com/ror-community/ror-updates";
  const ROR_CURATION_FORM_URL = "https://curation-request.ror.org/";

  // ---- i18n ---------------------------------------------------------------
  function t(key) {
    const table = window.I18N[state.lang] || window.I18N.en;
    return table[key] != null ? table[key] : (window.I18N.en[key] ?? key);
  }

  function applyStaticI18n() {
    document.documentElement.lang = state.lang;
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const v = t(el.getAttribute("data-i18n"));
      if (typeof v === "string") el.textContent = v;
    });
    document.querySelectorAll("[data-i18n-attr]").forEach((el) => {
      el.getAttribute("data-i18n-attr").split(",").forEach((pair) => {
        const [attr, key] = pair.split(":");
        const v = t(key);
        if (typeof v === "string") el.setAttribute(attr, v);
      });
    });
    document
      .querySelectorAll(".lang-toggle button")
      .forEach((b) =>
        b.setAttribute("aria-pressed", b.dataset.lang === state.lang)
      );
  }

  // ---- Text utilities -----------------------------------------------------
  function normalize(s) {
    return (s || "")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  function el(tag, opts = {}, children = []) {
    const node = document.createElement(tag);
    if (opts.class) node.className = opts.class;
    if (opts.text != null) node.textContent = opts.text;
    if (opts.html != null) node.innerHTML = opts.html;
    if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
    for (const c of [].concat(children)) if (c) node.appendChild(c);
    return node;
  }

  // ---- Record accessors (v2 schema) --------------------------------------
  function suffix(rec) {
    return (rec.id || "").replace(/\/+$/, "").split("/").pop();
  }
  function namesByType(rec, type) {
    return (rec.names || []).filter((n) => (n.types || []).includes(type));
  }
  function displayName(rec) {
    const d = namesByType(rec, "ror_display")[0];
    if (d) return d.value;
    return (rec.names && rec.names[0] && rec.names[0].value) || suffix(rec);
  }
  function acronyms(rec) {
    return namesByType(rec, "acronym").map((n) => n.value);
  }
  function city(rec) {
    for (const loc of rec.locations || []) {
      const g = loc.geonames_details || {};
      if (g.name) return g.name;
    }
    return "";
  }
  function allSearchTerms(rec) {
    // Every name variant + the ROR id (full url and suffix).
    const terms = (rec.names || []).map((n) => n.value);
    terms.push(rec.id || "", suffix(rec));
    return terms.map(normalize);
  }

  // ---- Data loading -------------------------------------------------------
  async function resolveDataBase() {
    // Deployed to Pages: data/ sits next to index.html.
    // Local dev under repo root as /www/: data/ is one level up.
    for (const base of ["data", "../data"]) {
      try {
        const r = await fetch(`${base}/meta.json`, { cache: "no-cache" });
        if (r.ok) {
          state.meta = await r.json();
          state.dataBase = base;
          return;
        }
      } catch (e) {
        /* try next */
      }
    }
    throw new Error("data not found");
  }

  async function loadData() {
    await resolveDataBase();
    const r = await fetch(`${state.dataBase}/records.json`, { cache: "no-cache" });
    if (!r.ok) throw new Error("records.json missing");
    state.records = await r.json();
    state.records.sort((a, b) =>
      displayName(a).localeCompare(displayName(b), undefined, { sensitivity: "base" })
    );
    for (const rec of state.records) state.byId.set(suffix(rec), rec);

    // Record → GitHub issues overlay. Generated at deploy time and not always
    // present (e.g. local dev without running scripts/update_issues.py), so a
    // miss is non-fatal: the detail page then just offers to open a new issue.
    try {
      const r2 = await fetch(`${state.dataBase}/issues.json`, { cache: "no-cache" });
      state.issues = r2.ok ? await r2.json() : {};
    } catch (e) {
      state.issues = {};
    }
  }

  // ---- Filters UI ---------------------------------------------------------
  function buildFilters() {
    // Status
    const statusBox = document.getElementById("filter-status");
    statusBox.innerHTML = "";
    ALL_STATUSES.forEach((s) => {
      statusBox.appendChild(
        checkbox(`st-${s}`, t(`status_${s}`), state.filters.statuses.has(s), (on) => {
          on ? state.filters.statuses.add(s) : state.filters.statuses.delete(s);
          render();
        })
      );
    });

    // Types — only those present in the data
    const present = new Set();
    state.records.forEach((r) => (r.types || []).forEach((x) => present.add(x)));
    const typeBox = document.getElementById("filter-type");
    typeBox.innerHTML = "";
    ALL_TYPES.filter((x) => present.has(x)).forEach((x) => {
      typeBox.appendChild(
        checkbox(`ty-${x}`, t(`type_${x}`), state.filters.types.has(x), (on) => {
          on ? state.filters.types.add(x) : state.filters.types.delete(x);
          render();
        })
      );
    });

    // Cities
    const cities = Array.from(new Set(state.records.map(city).filter(Boolean))).sort(
      (a, b) => a.localeCompare(b)
    );
    const sel = document.getElementById("filter-city");
    const current = state.filters.city;
    sel.innerHTML = "";
    sel.appendChild(el("option", { text: t("allCities"), attrs: { value: "" } }));
    cities.forEach((c) =>
      sel.appendChild(el("option", { text: c, attrs: { value: c } }))
    );
    sel.value = current;
    sel.onchange = () => {
      state.filters.city = sel.value;
      render();
    };
  }

  function checkbox(id, label, checked, onchange) {
    const input = el("input", { attrs: { type: "checkbox", id } });
    input.checked = checked;
    input.addEventListener("change", () => onchange(input.checked));
    const lbl = el("label", { attrs: { for: id } }, [input, document.createTextNode(" " + label)]);
    return el("div", { class: "checkbox" }, [lbl]);
  }

  // ---- Filtering / search -------------------------------------------------
  function matches(rec) {
    const f = state.filters;
    if (f.statuses.size && !f.statuses.has(rec.status)) return false;
    if (f.types.size && !(rec.types || []).some((x) => f.types.has(x))) return false;
    if (f.city && city(rec) !== f.city) return false;
    if (f.q) {
      const q = normalize(f.q);
      if (!rec._terms) rec._terms = allSearchTerms(rec);
      if (!rec._terms.some((term) => term.includes(q))) return false;
    }
    return true;
  }

  // ---- Rendering: list ----------------------------------------------------
  function render() {
    const list = document.getElementById("result-list");
    const countEl = document.getElementById("result-count");
    const empty = document.getElementById("no-results");
    const results = state.records.filter(matches); // already sorted by name

    list.innerHTML = "";
    empty.hidden = results.length !== 0;

    const total = state.records.length;
    countEl.textContent =
      results.length === total
        ? t("resultCount")(results.length, total)
        : t("resultCountFiltered")(results.length);

    const frag = document.createDocumentFragment();
    for (const rec of results) frag.appendChild(resultRow(rec));
    list.appendChild(frag);
  }

  function resultRow(rec) {
    const sfx = suffix(rec);
    const badges = (rec.types || []).map((x) =>
      el("span", { class: `badge badge-${x}`, text: t(`type_${x}`) })
    );
    const acr = acronyms(rec);

    const nameLink = el("a", {
      class: "result-name",
      text: displayName(rec),
      attrs: { href: `#/${sfx}` },
    });

    const meta = el("div", { class: "result-meta" }, [
      acr.length ? el("span", { class: "acronym", text: acr.join(", ") }) : null,
      city(rec) ? el("span", { class: "city", text: city(rec) }) : null,
      rec.status !== "active"
        ? el("span", { class: `status status-${rec.status}`, text: t(`status_${rec.status}`) })
        : null,
    ]);

    const idRow = el("div", { class: "result-id" }, [
      el("a", { class: "ror-id", text: rec.id, attrs: { href: rec.id, target: "_blank", rel: "noopener" } }),
      copyButton(rec.id),
    ]);

    return el("li", { class: "result-row" }, [
      el("div", { class: "result-main" }, [nameLink, el("div", { class: "badges" }, badges)]),
      meta,
      idRow,
    ]);
  }

  function copyButton(value) {
    const btn = el("button", {
      class: "copy-btn",
      attrs: { type: "button", "aria-label": t("copyRorId"), title: t("copyRorId") },
      text: "⧉",
    });
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        await navigator.clipboard.writeText(value);
      } catch (_) {
        const ta = document.createElement("textarea");
        ta.value = value;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      const prev = btn.textContent;
      btn.textContent = "✓";
      btn.classList.add("copied");
      setTimeout(() => {
        btn.textContent = prev;
        btn.classList.remove("copied");
      }, 1200);
    });
    return btn;
  }

  // ---- Rendering: detail --------------------------------------------------
  const RESOLVERS = {
    wikidata: (v) => `https://www.wikidata.org/wiki/${v}`,
    isni: (v) => `https://isni.org/isni/${v.replace(/\s+/g, "")}`,
    fundref: (v) => `https://doi.org/10.13039/${v}`,
    grid: (v) => `https://grid.ac/institutes/${v}`,
  };
  const EXTID_LABELS = { wikidata: "Wikidata", isni: "ISNI", fundref: "Funder ID (Crossref)", grid: "GRID" };

  function renderDetail(sfx) {
    const container = document.getElementById("detail-content");
    container.innerHTML = "";
    const rec = state.byId.get(sfx);
    if (!rec) {
      container.appendChild(el("p", { class: "empty", text: t("notFound") }));
      return;
    }

    // Header
    const badges = (rec.types || []).map((x) =>
      el("span", { class: `badge badge-${x}`, text: t(`type_${x}`) })
    );
    badges.push(
      el("span", { class: `status status-${rec.status}`, text: t(`status_${rec.status}`) })
    );
    container.appendChild(
      el("div", { class: "detail-header" }, [
        el("h1", { text: displayName(rec) }),
        el("div", { class: "badges", attrs: { role: "list" } }, badges),
      ])
    );

    // ROR id row
    container.appendChild(
      el("div", { class: "detail-idrow" }, [
        el("a", { class: "ror-id big", text: rec.id, attrs: { href: rec.id, target: "_blank", rel: "noopener" } }),
        copyButton(rec.id),
        el("a", { class: "chip", text: t("rorRecord"), attrs: { href: rec.id, target: "_blank", rel: "noopener" } }),
        el("a", {
          class: "chip",
          text: t("rawJson"),
          attrs: { href: `${state.dataBase}/records/${sfx}.json`, target: "_blank", rel: "noopener" },
        }),
      ])
    );

    const grid = el("div", { class: "detail-grid" });

    // Names
    const nameBlocks = [];
    const dn = namesByType(rec, "ror_display");
    if (dn.length) nameBlocks.push(nameLine(t("displayName"), dn));
    const al = namesByType(rec, "alias");
    if (al.length) nameBlocks.push(nameLine(t("aliases"), al));
    const ac = namesByType(rec, "acronym");
    if (ac.length) nameBlocks.push(nameLine(t("acronyms"), ac));
    const lb = (rec.names || []).filter(
      (n) => (n.types || []).includes("label") && !(n.types || []).includes("ror_display")
    );
    if (lb.length) nameBlocks.push(nameLine(t("labels"), lb));
    grid.appendChild(card(t("names"), nameBlocks));

    // Details
    const details = [];
    if (rec.established) details.push(kv(t("established"), String(rec.established)));
    const loc = (rec.locations || [])[0];
    if (loc && loc.geonames_details) {
      const g = loc.geonames_details;
      details.push(kv(t("location"), [g.name, g.country_subdivision_name, g.country_name].filter(Boolean).join(", ")));
    }
    (rec.links || []).forEach((l) => {
      const label = l.type === "wikipedia" ? t("wikipedia") : t("websiteLabel");
      details.push(kv(label, el("a", { text: l.value, attrs: { href: l.value, target: "_blank", rel: "noopener" } })));
    });
    grid.appendChild(card(t("details"), details));

    // Relationships
    const rels = rec.relationships || [];
    if (rels.length) {
      const order = ["parent", "predecessor", "child", "successor", "related"];
      const items = rels
        .slice()
        .sort((a, b) => order.indexOf(a.type) - order.indexOf(b.type) || a.label.localeCompare(b.label))
        .map((rel) => relLine(rel));
      grid.appendChild(card(t("relationships"), items));
    }

    // External IDs
    const ext = rec.external_ids || [];
    if (ext.length) {
      const items = [];
      ext.forEach((e) => {
        const type = e.type;
        const value = e.preferred || (e.all || [])[0];
        if (!value) return;
        const label = EXTID_LABELS[type] || type;
        const resolver = RESOLVERS[type];
        const node = resolver
          ? el("a", { text: value, attrs: { href: resolver(value), target: "_blank", rel: "noopener" } })
          : document.createTextNode(value);
        items.push(kv(label, node));
      });
      grid.appendChild(card(t("externalIds"), items));
    }

    // ROR curation requests for this record + a link to file one. Always shown.
    grid.appendChild(curationCard(sfx, rec));

    container.appendChild(grid);

    // OpenAlex panel (lazy)
    const panel = el("section", { class: "openalex-panel", attrs: { "aria-label": t("openalexTitle") } });
    container.appendChild(panel);
    loadOpenalexPanel(rec, panel);
  }

  function nameLine(label, names) {
    const values = names.map((n) => {
      const wrap = el("span", { class: "name-value" }, [document.createTextNode(n.value)]);
      if (n.lang) wrap.appendChild(el("span", { class: "lang-tag", text: n.lang }));
      return wrap;
    });
    const box = el("div", { class: "name-values" }, values);
    return el("div", { class: "kv" }, [el("span", { class: "k", text: label }), box]);
  }

  function curationCard(sfx, rec) {
    const items = [];
    const list = (state.issues && state.issues[sfx]) || [];
    for (const it of list) {
      const link = el("a", {
        class: "issue-title",
        text: `#${it.number} ${it.title}`,
        attrs: { href: it.url, target: "_blank", rel: "noopener" },
      });
      const badge = el("span", {
        class: `issue-state issue-${it.state}`,
        text: it.state === "closed" ? t("issueClosed") : t("issueOpen"),
      });
      items.push(el("div", { class: `issue-line issue-${it.state}` }, [badge, link]));
    }
    // Corrections are filed upstream — via ROR's web form or on GitHub.
    items.push(
      el("div", { class: "issue-new" }, [
        el("span", { class: "issue-new-label", text: `${t("requestCorrection")}: ` }),
        el("a", {
          text: t("curationForm"),
          attrs: { href: ROR_CURATION_FORM_URL, target: "_blank", rel: "noopener" },
        }),
        el("span", { class: "issue-new-sep", text: " · " }),
        el("a", {
          text: t("onGithub"),
          attrs: { href: `${ROR_UPDATES_URL}/issues/new/choose`, target: "_blank", rel: "noopener" },
        }),
      ])
    );
    return card(t("curationRequests"), items);
  }

  function relLine(rel) {
    const relSuffix = (rel.id || "").replace(/\/+$/, "").split("/").pop();
    const internal = state.byId.has(relSuffix);
    const link = internal
      ? el("a", { text: rel.label, attrs: { href: `#/${relSuffix}` } })
      : el("a", { text: rel.label, attrs: { href: rel.id, target: "_blank", rel: "noopener" } });
    const badge = el("span", { class: "rel-type", text: t(`rel_${rel.type}`) });
    const tail = internal ? null : el("span", { class: "ext-mark", text: "↗", attrs: { title: "ror.org" } });
    return el("div", { class: "rel-line" }, [badge, link, tail]);
  }

  function card(title, children) {
    return el("section", { class: "card" }, [el("h2", { text: title }), ...[].concat(children)]);
  }
  function kv(k, v) {
    const val = typeof v === "string" ? document.createTextNode(v) : v;
    return el("div", { class: "kv" }, [el("span", { class: "k", text: k }), el("span", { class: "v" }, [val])]);
  }

  // ---- OpenAlex (lazy, per record) ---------------------------------------
  async function loadOpenalexPanel(rec, panel) {
    panel.appendChild(el("h2", { text: t("openalexTitle") }));
    panel.appendChild(el("p", { class: "derived-note", text: t("openalexDerived") }));

    // Known miss from meta.json → no fetch.
    const missing = (state.meta && state.meta.reuse && state.meta.reuse.openalex &&
      state.meta.reuse.openalex.missing_ror_ids) || [];
    if (missing.includes(rec.id)) {
      panel.appendChild(el("p", { class: "empty", text: t("openalexNone") }));
      return;
    }

    const status = el("p", { class: "loading", text: t("openalexLoading") });
    panel.appendChild(status);

    let entity = null;
    try {
      const r = await fetch(`${state.dataBase}/reuse/openalex/records/${suffix(rec)}.json`, { cache: "no-cache" });
      if (r.ok) entity = await r.json();
    } catch (_) {
      /* treat as miss */
    }
    status.remove();

    if (!entity) {
      panel.appendChild(el("p", { class: "empty", text: t("openalexNone") }));
      return;
    }
    renderOpenalex(entity, rec, panel);
  }

  function renderOpenalex(e, rec, panel) {
    // Stats
    const stats = el("div", { class: "oa-stats" }, [
      stat(t("worksCount"), (e.works_count || 0).toLocaleString()),
      stat(t("citedByCount"), (e.cited_by_count || 0).toLocaleString()),
    ]);
    panel.appendChild(stats);
    panel.appendChild(
      el("p", {}, [
        el("a", { class: "chip", text: t("viewOnOpenalex"), attrs: { href: e.id, target: "_blank", rel: "noopener" } }),
      ])
    );

    // counts_by_year sparkline
    const cby = (e.counts_by_year || []).slice().sort((a, b) => a.year - b.year);
    if (cby.length) {
      panel.appendChild(el("h3", { text: t("worksByYear") }));
      panel.appendChild(sparkline(cby));
    }

    // Top topics
    const topics = (e.topics || []).slice(0, 6);
    if (topics.length) {
      panel.appendChild(el("h3", { text: t("topTopics") }));
      const ul = el("ul", { class: "topic-list" });
      topics.forEach((tp) => {
        const field = (tp.field && tp.field.display_name) || "";
        ul.appendChild(
          el("li", {}, [
            el("span", { class: "topic-name", text: tp.display_name }),
            field ? el("span", { class: "topic-field", text: field }) : null,
          ])
        );
      });
      panel.appendChild(ul);
    }

    // display_name_alternatives not already in ROR names
    const rorNames = new Set((rec.names || []).map((n) => normalize(n.value)));
    const extras = (e.display_name_alternatives || []).filter((n) => !rorNames.has(normalize(n)));
    if (extras.length) {
      panel.appendChild(el("h3", { text: t("altNames") }));
      panel.appendChild(el("p", { class: "alt-names", text: extras.join(" · ") }));
    }
  }

  function stat(label, value) {
    return el("div", { class: "stat" }, [
      el("span", { class: "stat-value", text: value }),
      el("span", { class: "stat-label", text: label }),
    ]);
  }

  function sparkline(cby) {
    const w = 320, h = 60, pad = 4;
    const max = Math.max(...cby.map((d) => d.works_count), 1);
    const n = cby.length;
    const bw = (w - pad * 2) / n;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("class", "sparkline");
    svg.setAttribute("role", "img");
    cby.forEach((d, i) => {
      const bh = (d.works_count / max) * (h - pad * 2 - 12);
      const x = pad + i * bw;
      const y = h - pad - bh - 12;
      const bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      bar.setAttribute("x", x + bw * 0.15);
      bar.setAttribute("y", y);
      bar.setAttribute("width", bw * 0.7);
      bar.setAttribute("height", Math.max(bh, 0.5));
      bar.setAttribute("class", "spark-bar");
      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = `${d.year}: ${d.works_count}`;
      bar.appendChild(title);
      svg.appendChild(bar);
      if (i === 0 || i === n - 1 || n <= 8) {
        const lab = document.createElementNS("http://www.w3.org/2000/svg", "text");
        lab.setAttribute("x", x + bw / 2);
        lab.setAttribute("y", h - 2);
        lab.setAttribute("text-anchor", "middle");
        lab.setAttribute("class", "spark-label");
        lab.textContent = String(d.year).slice(2);
        svg.appendChild(lab);
      }
    });
    return svg;
  }

  // ---- Router -------------------------------------------------------------
  function route() {
    const hash = location.hash.replace(/^#\/?/, "");
    const listView = document.getElementById("view-list");
    const detailView = document.getElementById("view-detail");
    if (hash) {
      listView.hidden = true;
      detailView.hidden = false;
      renderDetail(hash);
      window.scrollTo(0, 0);
    } else {
      detailView.hidden = true;
      listView.hidden = false;
    }
  }

  // ---- Footer -------------------------------------------------------------
  function renderFooter() {
    const m = state.meta && state.meta.ror;
    if (!m) return;
    document.getElementById("footer-meta").textContent = t("footerMeta")(
      m.dump_version, m.publication_date, m.record_count
    );
  }

  // ---- Language -----------------------------------------------------------
  function setLang(lang) {
    state.lang = window.I18N[lang] ? lang : "en";
    applyStaticI18n();
    buildFilters();
    renderFooter();
    render();
    // Re-render detail if open
    if (location.hash.replace(/^#\/?/, "")) route();
  }

  // ---- Init ---------------------------------------------------------------
  async function init() {
    state.lang = (navigator.language || "en").toLowerCase().startsWith("de") ? "de" : "en";
    applyStaticI18n();

    document.querySelectorAll(".lang-toggle button").forEach((b) =>
      b.addEventListener("click", () => setLang(b.dataset.lang))
    );

    const q = document.getElementById("q");
    q.addEventListener("input", () => {
      state.filters.q = q.value;
      render();
    });
    document.getElementById("reset-filters").addEventListener("click", () => {
      state.filters = { q: "", types: new Set(), city: "", statuses: new Set(["active"]) };
      q.value = "";
      buildFilters();
      render();
    });

    window.addEventListener("hashchange", route);

    try {
      await loadData();
    } catch (e) {
      document.getElementById("loading").hidden = true;
      const err = document.getElementById("load-error");
      err.hidden = false;
      err.textContent = t("loadError");
      return;
    }

    document.getElementById("loading").hidden = true;
    document.getElementById("view-list").hidden = false;
    buildFilters();
    renderFooter();
    render();
    route();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
