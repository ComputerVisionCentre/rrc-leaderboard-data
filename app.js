// Shared code for index.html (explorer) and embed.html (embeddable table).
// Data comes from ./data/<competition>/task<N>/{meta.json,latest.json}.

const DATA = new URL("data/", document.baseURI).href;
const params = new URLSearchParams(location.search);

export const TYPE_ORDER = ["Baseline", "Competition entry", "Other public"];

export function methodType(m) {
  if (m.is_baseline) return "Baseline";
  if (m.is_competition) return "Competition entry";
  return "Other public";
}

export async function getJSON(path) {
  const r = await fetch(DATA + path, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

export async function loadTask(competition, task) {
  const base = `${competition}/task${task}/`;
  const [meta, latest] = await Promise.all([getJSON(base + "meta.json"), getJSON(base + "latest.json")]);
  latest.methods.forEach((m) => (m.type = methodType(m)));
  return { meta, methods: latest.methods, snapshot: latest.snapshot, base: DATA + base };
}

export function filterMethods(methods, only) {
  if (only === "baselines") return methods.filter((m) => m.is_baseline);
  if (only === "competition") return methods.filter((m) => m.is_competition || m.is_baseline);
  return methods;
}

export function sortMethods(methods, key, higherIsBetter = true) {
  const dir = higherIsBetter ? -1 : 1;
  return [...methods].sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null && y == null) return a.position - b.position;
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === "number") return dir * (x - y) || a.position - b.position;
    return String(x).localeCompare(String(y));
  });
}

const fmt = (v) => (typeof v === "number" ? v.toFixed(4) : v ?? "–");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/**
 * Render a leaderboard table into `el`.
 * opts: { metrics: [keys] | null (all), attributes: bool, sortKey, onSort(key) }
 */
export function renderTable(el, meta, methods, opts = {}) {
  const metrics = meta.metrics.filter((m) => !opts.metrics || opts.metrics.includes(m.key));
  const attrs = opts.attributes === false ? [] : meta.attributes;
  const sortKey = opts.sortKey || meta.primary_metric;
  const head = [
    `<th class="num">#</th>`,
    `<th>Method</th>`,
    `<th>Date</th>`,
    ...attrs.map((a) => `<th>${esc(a.label)}</th>`),
    ...metrics.map(
      (m) => `<th class="num sortable${m.key === sortKey ? " sorted" : ""}" data-key="${esc(m.key)}" tabindex="0">${esc(m.label)}</th>`
    ),
  ].join("");
  const best = {};
  for (const m of metrics) {
    const vals = methods.map((x) => x[m.key]).filter((v) => typeof v === "number");
    best[m.key] = vals.length ? (meta.higher_is_better ? Math.max(...vals) : Math.min(...vals)) : null;
  }
  const rows = methods
    .map((x, i) => {
      const cls = x.is_baseline ? "baseline" : x.is_competition ? "competition" : "";
      return `<tr class="${cls}">
        <td class="num muted">${i + 1}</td>
        <td><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.method)}</a>${
          x.is_baseline ? ' <span class="tag">baseline</span>' : ""
        }</td>
        <td class="muted nowrap">${esc(x.date)}</td>
        ${attrs.map((a) => `<td class="muted">${esc(x[a.key])}</td>`).join("")}
        ${metrics
          .map((m) => `<td class="num${x[m.key] === best[m.key] ? " best" : ""}">${fmt(x[m.key])}</td>`)
          .join("")}
      </tr>`;
    })
    .join("");
  el.innerHTML = `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
  if (opts.onSort) {
    el.querySelectorAll("th.sortable").forEach((th) => {
      const go = () => opts.onSort(th.dataset.key);
      th.addEventListener("click", go);
      th.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), go()));
    });
  }
}

export function getParam(name, fallback = null) {
  return params.get(name) ?? fallback;
}

export function isDark() {
  return matchMedia("(prefers-color-scheme: dark)").matches;
}
