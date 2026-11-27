// Project overview page for asBuilt, drawn from /overview.json: each language's components grouped
// by kind, then a component's files, then a file's functions; shared helpers, definitions with no
// path from an entry point, the packages the manifests declare, and everything that was not read.

export const NO_PATH_LABEL = "No path from an entry point found by asBuilt";
export const NO_PATH_HINT = "asBuilt found nothing that runs this; worth asking your agent";
export const PACKAGES_TITLE = "Packages this project declares";

// [state key, payload field, label]: the component sets a project can have, in this order.
const SECTIONS = [
  ["py", "components", "Python"],
  ["js", "js_components", "JavaScript / TypeScript"],
];

const KIND_LABELS = {
  commands: "Commands",
  "web pages": "Web pages",
  scripts: "Scripts",
  other: "Other",
};

const BADGE_CLASS = {
  env: "ov-badge-env",
  input: "ov-badge-input",
  writes: "ov-badge-writes",
  "calls out": "ov-badge-calls-out",
  model: "ov-badge-model",
};

const BADGE_DESCRIPTIONS = {
  env: "uses environment settings",
  input: "reads user input",
  writes: "writes files",
  "calls out": "network or external program",
  model: "calls LLM SDK",
};

const HTML_ENTITIES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function esc(value) {
  return String(value ?? "")
    .replace(/[\0-\b\v\f\x0E-\x1F\p{Cs}￾￿]/gu, "�")
    .replace(/[&<>"']/g, (ch) => HTML_ENTITIES[ch]);
}

export function safeUrl(url) {
  if (typeof url !== "string") return "";
  const trimmed = url.trim();
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return "";
}

export function titled(fact) {
  if (fact?.kind === "input") return "Reads what a person types";
  if (fact?.kind === "env") {
    if (fact.how === "check") return "Checks an environment setting";
    if (["remove", "default", "set", "write"].includes(fact.how)) return "Changes an environment setting";
    return "Reads an environment setting";
  }
  if (fact?.kind === "calls out") {
    if (fact.way === "command") return "Runs a program outside this one";
    if (fact.way === "connect") return "Opens a network connection";
    return "Makes a network request";
  }
  return "Something to know";
}

const list = (value) => (Array.isArray(value) ? value : []);

function kindClass(kind) {
  if (kind === "commands") return "ov-kind-commands";
  if (kind === "web pages") return "ov-kind-web-pages";
  if (kind === "scripts") return "ov-kind-scripts";
  return "ov-kind-other";
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

/** The component sets present in the payload, as {key, label, info}. */
export function sections(data) {
  return SECTIONS.filter(([, field]) => data?.[field] && typeof data[field] === "object").map(
    ([key, field, label]) => ({ key, label, info: data[field] })
  );
}

function fileOf(member, boxes) {
  return boxes[member.id]?.file || member.file || String(member.id).split(":")[0];
}

function byFile(members, boxes) {
  const grouped = new Map();
  for (const member of members) {
    const file = fileOf(member, boxes);
    if (!grouped.has(file)) grouped.set(file, []);
    grouped.get(file).push(member);
  }
  return [...grouped.entries()];
}

function notice(title, items, role = "status") {
  return items.length
    ? `<div class="ov-notice" role="${role}"><strong>${esc(title)} (${items.length}):</strong><ul class="ov-detail-list">${items
        .map((item) => `<li class="mono">${esc(item)}</li>`)
        .join("")}</ul></div>`
    : "";
}

export function renderHeader(data) {
  const found = sections(data);
  const boxes = data?.boxes && typeof data.boxes === "object" ? data.boxes : {};
  const comps = found.reduce((n, s) => n + list(s.info.components).length, 0);
  const summary = [
    plural(comps, "component", "components"),
    plural(list(data?.files).length, "file", "files"),
    plural(Object.keys(boxes).length, "function / class", "functions / classes"),
    plural(list(data?.packages).length, "package", "packages"),
  ];
  const jsError = data?.js_error
    ? `<div class="ov-notice ov-error" role="alert"><strong>JavaScript / TypeScript was not charted:</strong> ${esc(
        data.js_error
      )}</div>`
    : "";
  return `
    <header class="ov-header">
      <div class="ov-title-row">
        <h1 class="ov-title">${esc(data?.project || "Project")}</h1>
        <span class="ov-lang-tag">${esc(found.map((s) => s.label).join(" + ") || "No components")}</span>
      </div>
      <p class="ov-summary">${esc(summary.join(" · "))}</p>
      ${jsError}
      ${notice("Files asBuilt could not read", list(data?.not_read), "alert")}
      ${found
        .map(
          (s) =>
            notice(`${s.label}: entry points that reach no charted function`, list(s.info.entries_reaching_nothing)) +
            notice(`${s.label}: entry points asBuilt could not prove`, list(s.info.not_proven))
        )
        .join("")}
      ${notice("Skipped", list(data?.skipped))}
    </header>
  `;
}

export function renderPackages(packages, unreadable = [], read = [], notRead = []) {
  const items = list(packages);
  const missed = list(unreadable);
  const from = list(read).length
    ? `<p class="ov-card-meta">From ${esc(list(read).join(", "))} in the project folder only.</p>`
    : `<p class="ov-card-meta">No pyproject.toml, requirements.txt or package.json found.</p>`;
  const body = items.length
    ? `<ul class="ov-pkg-list">${items
        .map((pkg) => {
          const href = safeUrl(pkg?.url);
          const nameHtml = href
            ? `<a class="ov-pkg-link mono" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(pkg.name)}</a>`
            : `<span class="ov-pkg-link mono">${esc(pkg?.name ?? "")}</span>`;
          const specHtml = pkg?.spec ? `<span class="ov-pkg-spec mono">${esc(pkg.spec)}</span>` : "";
          const scopeTag = pkg?.dev
            ? `<span class="ov-tag-dev">dev</span>`
            : `<span class="ov-tag-runtime">runtime</span>`;
          const extraTags = list(pkg?.tags)
            .map((tag) => `<span class="ov-tag-reg">${esc(tag)}</span>`)
            .join("");
          const regTag = pkg?.registry ? `<span class="ov-tag-reg">${esc(pkg.registry)}</span>` : "";
          const summaryText = typeof pkg?.summary === "string" ? pkg.summary.trim() : "";
          const summaryHtml = summaryText ? `<p class="pkg-summary">${esc(summaryText)}</p>` : "";
          return `
            <li class="ov-pkg-item">
              <div class="ov-pkg-top">${nameHtml}${specHtml}</div>
              ${summaryHtml}
              <div class="ov-pkg-tags">${scopeTag}${extraTags}${regTag}</div>
            </li>
          `;
        })
        .join("")}</ul>`
    : missed.length
      ? ""
      : list(read).length
        ? `<p class="ov-card-meta">No packages declared there.</p>`
        : "";
  const missedHtml = missed.length
    ? `<div class="ov-notice" role="alert"><strong>Could not read:</strong> ${esc(
        missed.join(", ")
      )}. The packages it declares are not listed.</div>`
    : "";
  return `
    <aside class="ov-sidebar" aria-label="${esc(PACKAGES_TITLE)}">
      <h2>${esc(PACKAGES_TITLE)} <span class="ov-count">${items.length}</span></h2>
      ${from}
      ${missedHtml}
      ${notice("Declared there but not read", list(notRead))}
      ${body}
    </aside>
  `;
}

export function renderFunctionCard(box, allBoxes = {}, extraMeta = "") {
  if (!box || typeof box !== "object") return "";
  const lines =
    Array.isArray(box.lines) && box.lines.length === 2 ? `lines ${box.lines[0]}–${box.lines[1]}` : "";
  const whyHtml = extraMeta ? `<p class="ov-fn-why">${esc(extraMeta)}</p>` : "";

  const badges = list(box.badges);
  const badgesHtml = badges.length
    ? `<div class="ov-badges">${badges
        .map((badge) => {
          const cls = BADGE_CLASS[badge] || "";
          const desc = BADGE_DESCRIPTIONS[badge] ? ` title="${esc(BADGE_DESCRIPTIONS[badge])}"` : "";
          return `<span class="ov-badge ${cls}"${desc}>${esc(badge)}</span>`;
        })
        .join("")}</div>`
    : "";

  const plainHtml = list(box.plain)
    .filter((line) => typeof line === "string")
    .map((line) => `<p class="ov-plain">${esc(line)}</p>`)
    .join("");

  const provenHtml =
    typeof box.proven === "string" && box.proven ? `<p class="ov-proven">${esc(box.proven)}</p>` : "";

  const secNotes = list(box.security).filter((note) => note && typeof note.plain === "string");
  const securityHtml = secNotes.length
    ? `<div class="ov-security">
        <p class="ov-security-head">Security: ${plural(secNotes.length, "thing", "things")} to know</p>
        ${secNotes
          .map((note, idx) => {
            const expertHtml =
              typeof note.expert === "string" && note.expert
                ? `<p><strong>Expert:</strong> ${esc(note.expert)}</p>`
                : "";
            return `<div class="ov-security-item">
              <p><strong>${idx + 1}. ${esc(titled(note.fact))}</strong></p>
              <p><strong>Plain:</strong> ${esc(note.plain)}</p>
              ${expertHtml}
            </div>`;
          })
          .join("")}
      </div>`
    : "";

  const calls = list(box.calls);
  const callsHtml = calls.length
    ? `<div class="ov-calls"><strong>Calls:</strong> ${calls
        .map((calleeId) => {
          const target = allBoxes[calleeId];
          const label = target
            ? target.file && target.file !== box.file
              ? `${target.name} (${target.file})`
              : target.name
            : calleeId;
          return `<span class="ov-call-chip mono">${esc(label)}</span>`;
        })
        .join("")}</div>`
    : "";

  const restHtml = typeof box.rest === "string" && box.rest ? `<p class="ov-rest">${esc(box.rest)}</p>` : "";
  const sourceHtml =
    typeof box.text === "string" && box.text.length > 0
      ? `<details class="ov-source">
        <summary>Source code</summary>
        <pre>${esc(box.text)}</pre>
        ${restHtml}
      </details>`
      : "";

  return `
    <article class="ov-fn-card" data-box-id="${esc(box.id)}">
      <div class="ov-fn-head">
        <span class="ov-fn-name mono">${esc(box.name)}</span>
        ${lines ? `<span class="ov-fn-lines mono">${esc(lines)}</span>` : ""}
      </div>
      ${whyHtml}
      ${badgesHtml}
      ${plainHtml}
      ${provenHtml}
      ${securityHtml}
      ${callsHtml}
      ${sourceHtml}
    </article>
  `;
}

function back(trail) {
  return `
    <div class="ov-breadcrumb">
      <button type="button" class="ov-back-btn" data-action="back">&larr; Back</button>
      <span>${trail}</span>
    </div>
  `;
}

function tabsOf(info) {
  return [
    ...list(info.groups).map((g) => ({
      id: `group:${g.kind}`,
      label: KIND_LABELS[g.kind] || g.kind,
      count: list(g.components).length,
      unit: ["component", "components"],
    })),
    { id: "shared", label: "Shared helpers", count: list(info.shared).length, unit: ["file", "files"] },
    {
      id: "no_path_found",
      label: NO_PATH_LABEL,
      count: list(info.no_path_found).length,
      unit: ["function / class", "functions / classes"],
    },
  ];
}

function tabLabel(tab) {
  if (tab === "shared") return "Shared helpers";
  if (tab === "no_path_found") return NO_PATH_LABEL;
  const kind = String(tab).replace(/^group:/, "");
  return KIND_LABELS[kind] || kind;
}

function renderSwitcher(found, section) {
  return found.length > 1
    ? `<nav class="ov-tabs" aria-label="Languages">${found
        .map(
          (s) =>
            `<button type="button" class="ov-tab" data-action="select-section" data-section="${esc(s.key)}" aria-pressed="${
              s.key === section.key ? "true" : "false"
            }">${esc(s.label)} <span class="ov-count">${list(s.info.components).length}</span></button>`
        )
        .join("")}</nav>`
    : "";
}

// The first screen: one card per group, so a big project opens as a handful of groups.
function renderGroups(found, section) {
  const cards = tabsOf(section.info)
    .map(
      (tab) => `
        <button type="button" class="ov-card" data-action="select-tab" data-tab="${esc(tab.id)}">
          <div class="ov-card-head"><span class="ov-card-title">${esc(tab.label)}</span></div>
          <div class="ov-card-meta">${esc(plural(tab.count, ...tab.unit))}</div>
        </button>
      `
    )
    .join("");
  return `${renderSwitcher(found, section)}<div class="ov-grid">${cards}</div>`;
}

function renderTabs(found, section, state) {
  return `${renderSwitcher(found, section)}
    <nav class="ov-tabs" aria-label="Component groups">
      ${tabsOf(section.info)
        .map(
          (tab) =>
            `<button type="button" class="ov-tab" data-action="select-tab" data-tab="${esc(tab.id)}" aria-pressed="${
              state.tab === tab.id ? "true" : "false"
            }">${esc(tab.label)} <span class="ov-count">${tab.count}</span></button>`
        )
        .join("")}
    </nav>
  `;
}

function renderComponent(comp, boxes, state) {
  const members = list(comp.members);
  const files = byFile(members, boxes);
  const kind = KIND_LABELS[comp.kind] || comp.kind;
  const opened = state.file && files.find(([file]) => file === state.file);
  if (opened) {
    const [file, inFile] = opened;
    return `
      ${back(`${esc(kind)} / ${esc(comp.name)} / <strong class="mono">${esc(file)}</strong>`)}
      <div class="ov-detail-header">
        <h2 class="mono">${esc(file)}</h2>
        <div class="ov-card-meta">${esc(plural(inFile.length, "function / class", "functions / classes"))} in ${esc(comp.name)}</div>
      </div>
      <div class="ov-fn-list">${inFile.map((m) => renderFunctionCard(boxes[m.id], boxes, m.why || "")).join("")}</div>
    `;
  }
  const ways = list(comp.ways_in);
  const cards = files.length
    ? `<div class="ov-grid">${files
        .map(
          ([file, inFile]) => `
            <button type="button" class="ov-card" data-action="open-file" data-file="${esc(file)}">
              <div class="ov-card-head"><span class="ov-card-title mono">${esc(file)}</span></div>
              <div class="ov-card-meta">${esc(plural(inFile.length, "function / class", "functions / classes"))}</div>
            </button>
          `
        )
        .join("")}</div>`
    : `<p class="ov-empty">This component has no charted functions or classes.</p>`;
  return `
    ${back(`${esc(kind)} / <strong>${esc(comp.name)}</strong>`)}
    <div class="ov-detail-header">
      <h2>
        <span>${esc(comp.name)}</span>
        <span class="ov-kind-badge ${kindClass(comp.kind)}">${esc(comp.kind)}</span>
      </h2>
      <div class="ov-card-meta">${esc(plural(files.length, "file", "files"))} · ${esc(
    plural(members.length, "function / class", "functions / classes")
  )}</div>
      ${ways.length ? `<ul class="ov-detail-list">${ways.map((w) => `<li class="mono">${esc(w)}</li>`).join("")}</ul>` : ""}
    </div>
    ${cards}
  `;
}

function renderShared(item, boxes) {
  const cards = list(item.members)
    .map((member) => {
      const reasons = Array.isArray(member.used_by)
        ? `used by ${member.used_by.join(", ")}`
        : member.used_by && typeof member.used_by === "object"
          ? Object.entries(member.used_by)
              .map(([c, why]) => `${c}: ${why}`)
              .join("; ")
          : "";
      return renderFunctionCard(boxes[member.id], boxes, reasons);
    })
    .join("");
  return `
    ${back(`Shared helpers / <strong>${esc(item.name)}</strong>`)}
    <div class="ov-detail-header">
      <h2>
        <span class="mono">${esc(item.file || item.name)}</span>
        <span class="ov-kind-badge ov-kind-shared">shared</span>
      </h2>
      <div class="ov-card-meta">Used by: ${esc(list(item.used_by).join(", "))}</div>
    </div>
    <div class="ov-fn-list">${cards}</div>
  `;
}

function renderNoPath(info, boxes) {
  const files = byFile(list(info.no_path_found), boxes);
  const intro = `
    <div class="ov-detail-header">
      <h2>${esc(NO_PATH_LABEL)}</h2>
      <p class="ov-soft-hint">${esc(NO_PATH_HINT)}</p>
      <div class="ov-card-meta">
        asBuilt did not find a static call path from a detected entry point to these definitions.
        This does not mean they are unused — dynamic calls, callbacks, plugins, or external callers are not followed.
      </div>
    </div>
  `;
  const body = files.length
    ? files
        .map(
          ([file, members]) => `
            <section class="ov-file-section">
              <h3 class="ov-file-heading mono">
                <span>${esc(file)}</span>
                <span class="ov-count">${plural(members.length, "item", "items")}</span>
              </h3>
              <div class="ov-fn-list">${members.map((m) => renderFunctionCard(boxes[m.id], boxes)).join("")}</div>
            </section>
          `
        )
        .join("")
    : `<p class="ov-empty">Every charted definition was reached from at least one detected entry point.</p>`;
  return `${intro}${body}`;
}

function renderGroup(info, boxes, kind) {
  const comps = new Map(list(info.components).map((c) => [c.name, c]));
  const group = list(info.groups).find((g) => g.kind === kind);
  const cards = list(group?.components)
    .map((name) => comps.get(name))
    .filter(Boolean)
    .map((comp) => {
      const members = list(comp.members);
      const files = new Set(members.map((m) => fileOf(m, boxes))).size;
      const ways = list(comp.ways_in);
      const preview = ways.slice(0, 2);
      const waysHtml = preview.length
        ? `<ul class="ov-card-ways">${preview.map((w) => `<li class="mono">${esc(w)}</li>`).join("")}${
            ways.length > preview.length ? `<li>+${ways.length - preview.length} more</li>` : ""
          }</ul>`
        : "";
      return `
        <button type="button" class="ov-card" data-action="open-component" data-component="${esc(comp.name)}">
          <div class="ov-card-head">
            <span class="ov-card-title">${esc(comp.name)}</span>
            <span class="ov-kind-badge ${kindClass(comp.kind)}">${esc(comp.kind)}</span>
          </div>
          <div class="ov-card-meta">${esc(plural(files, "file", "files"))} · ${esc(
        plural(members.length, "function / class", "functions / classes")
      )}</div>
          ${waysHtml}
        </button>
      `;
    })
    .join("");
  return cards ? `<div class="ov-grid">${cards}</div>` : `<p class="ov-empty">No components in this group.</p>`;
}

function renderMain(data, state) {
  const found = sections(data);
  const section = found.find((s) => s.key === state.section);
  if (!section) return `<p class="ov-empty">No components were charted.</p>`;
  const info = section.info;
  const boxes = data?.boxes && typeof data.boxes === "object" ? data.boxes : {};
  const comp = state.component && list(info.components).find((c) => c.name === state.component);
  if (comp) return renderComponent(comp, boxes, state);
  const shared = state.shared && list(info.shared).find((s) => (s.file || s.name) === state.shared);
  if (shared) return renderShared(shared, boxes);
  if (!state.tab) return renderGroups(found, section);
  const tabs = `${back(`<strong>${esc(tabLabel(state.tab))}</strong>`)}${renderTabs(found, section, state)}`;
  if (state.tab === "shared") {
    const items = list(info.shared);
    const body = items.length
      ? `<div class="ov-grid">${items
          .map(
            (s) => `
              <button type="button" class="ov-card" data-action="open-shared" data-shared="${esc(s.file || s.name)}">
                <div class="ov-card-head">
                  <span class="ov-card-title mono">${esc(s.name)}</span>
                  <span class="ov-kind-badge ov-kind-shared">shared</span>
                </div>
                <div class="ov-card-meta">${esc(plural(list(s.members).length, "helper", "helpers"))}</div>
                <div class="ov-card-meta">Used by: ${esc(list(s.used_by).join(", "))}</div>
              </button>
            `
          )
          .join("")}</div>`
      : `<p class="ov-empty">No helpers shared across multiple components.</p>`;
    return `${tabs}${body}`;
  }
  if (state.tab === "no_path_found") return `${tabs}${renderNoPath(info, boxes)}`;
  return `${tabs}${renderGroup(info, boxes, String(state.tab).replace(/^group:/, ""))}`;
}

export function initialState(data, section = sections(data)[0]?.key ?? null) {
  return { section, tab: null, component: null, shared: null, file: null };
}

export function renderOverview(data, state = initialState(data)) {
  return `
    ${renderHeader(data)}
    <div class="ov-layout">
      <main class="ov-main">${renderMain(data, state)}</main>
      ${renderPackages(data?.packages, data?.packages_unreadable, data?.packages_read, data?.packages_not_read)}
    </div>
  `;
}

export function createApp(root, data) {
  let state = initialState(data);

  function draw() {
    root.innerHTML = renderOverview(data, state);
  }

  root.addEventListener("click", (event) => {
    const target = event.target?.closest?.("[data-action]");
    if (!target) return;
    const action = target.getAttribute("data-action");
    const value = (name) => target.getAttribute(name);
    if (action === "select-section") {
      state = initialState(data, value("data-section"));
    } else if (action === "select-tab") {
      state = { ...state, tab: value("data-tab"), component: null, shared: null, file: null };
    } else if (action === "open-component") {
      state = { ...state, component: value("data-component"), shared: null, file: null };
    } else if (action === "open-shared") {
      state = { ...state, shared: value("data-shared"), component: null, file: null };
    } else if (action === "open-file") {
      state = { ...state, file: value("data-file") };
    } else if (action === "back") {
      // One level up: file -> component -> group -> the groups.
      if (state.file) state = { ...state, file: null };
      else if (state.component || state.shared) state = { ...state, component: null, shared: null };
      else state = { ...state, tab: null };
    } else {
      return;
    }
    draw();
  });

  draw();
}

export async function boot(root) {
  try {
    const response = await fetch("/overview.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Server returned HTTP ${response.status}`);
    }
    const data = await response.json();
    if (data?.project && typeof document !== "undefined") {
      document.title = `As-Built — ${data.project}`;
    }
    createApp(root, data);
  } catch (err) {
    root.innerHTML = `<div class="ov-error">Could not load project overview: ${esc(err?.message || err)}</div>`;
  }
}

if (typeof document !== "undefined" && typeof fetch === "function") {
  const root = document.getElementById("app");
  if (root) boot(root);
}
