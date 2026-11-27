"""Mirror RRC leaderboards into static, plot-friendly files.

Output layout (served by GitHub Pages from the `gh-pages` branch):

    data/index.json                         every mirrored competition/task
    data/status.json                        last check time and per-task errors
    data/<competition>/task<N>/meta.json    titles, metric list, source URL, last change
    data/<competition>/task<N>/latest.json  current leaderboard (one object per method)
    data/<competition>/task<N>/latest.csv   current leaderboard, long: one row per method x metric
    data/<competition>/task<N>/wide.csv     current leaderboard, wide: one row per method
    data/<competition>/task<N>/history.csv  change log: one row per (method, metric) change
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from . import scrape

BASE_FIELDS = [
    "competition", "task", "position", "method_id", "method", "date",
    "is_baseline", "is_competition", "has_description", "has_paper", "has_code", "url",
]
HISTORY_FIELDS = ["snapshot", "competition", "task", "method_id", "method", "event", "metric", "value"]
STATUS_HEARTBEAT = timedelta(days=6)  # rewrite status.json at least this often (keeps the repo "active")
MAX_DETAIL_FETCHES = 25  # per task and run, to stay gentle with RRC


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_config(path: Path) -> list[dict[str, Any]]:
    cfg = yaml.safe_load(path.read_text()) or {}
    comps = cfg.get("competitions") or []
    ids = [c["id"] for c in comps]
    if len(ids) != len(set(ids)):
        raise SystemExit("competitions.yml: duplicate competition ids")
    for c in comps:
        bad = set(c.get("method_details") or []) - set(scrape.DETAIL_FIELDS)
        if bad:
            raise SystemExit(f"{c['id']}: unsupported method_details {sorted(bad)}; allowed: {scrape.DETAIL_FIELDS}")
    return comps


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _csv_value(v: Any) -> Any:
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else v


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]], append: bool = False) -> None:
    exists = path.exists()
    with path.open("a" if append else "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if not (append and exists):
            w.writeheader()
        w.writerows({k: _csv_value(r.get(k)) for k in fields} for r in rows)


def history_events(old: list[dict], new: list[dict], metrics: list[str], snapshot: str, base: dict) -> list[dict]:
    """Rows for every (method, metric) whose value appeared, changed or disappeared."""
    def key(m: dict) -> Any:
        return m["method_id"] if m["method_id"] is not None else m["method"]

    old_by, new_by = {key(m): m for m in old}, {key(m): m for m in new}
    events = []
    for k, m in new_by.items():
        prev = old_by.get(k)
        for metric in metrics:
            if prev is None or prev.get(metric) != m.get(metric):
                events.append({**base, "snapshot": snapshot, "method_id": m["method_id"], "method": m["method"],
                               "event": "added" if prev is None else "updated", "metric": metric,
                               "value": m.get(metric)})
    for k, m in old_by.items():
        if k not in new_by:
            events.append({**base, "snapshot": snapshot, "method_id": m["method_id"], "method": m["method"],
                           "event": "removed", "metric": "", "value": None})
    return events


def update_task(comp: dict, task: int, out: Path, session, fetch: Callable[[Any, str], str]) -> bool:
    """Refresh one competition task. Returns True if its data changed."""
    cid, ch = comp["id"], int(comp["ch"])
    tdir = out / "data" / cid / f"task{task}"
    tdir.mkdir(parents=True, exist_ok=True)
    source = scrape.leaderboard_url(ch, task)

    lb = scrape.parse_leaderboard(fetch(session, source), comp.get("baseline_pattern", scrape.DEFAULT_BASELINE_PATTERN))
    previous = _read_json(tdir / "latest.json", {})
    old_methods = previous.get("methods", [])
    if not lb.methods and old_methods:
        raise scrape.ParseError("table came back empty but had methods before; keeping previous data")
    metric_keys = [c.key for c in lb.metrics]
    attr_keys = [c.key for c in lb.attributes]
    base = {"competition": cid, "task": task}

    # Optional, opt-in method details (cached; only new methods are fetched).
    detail_fields = tuple(comp.get("method_details") or ())
    details: dict[str, dict] = {}
    if detail_fields:
        cache_path = tdir / "method_details.json"
        details = _read_json(cache_path, {})
        missing = [m for m in lb.methods if m["method_id"] and str(m["method_id"]) not in details]
        for m in missing[:MAX_DETAIL_FETCHES]:
            html = fetch(session, scrape.method_url(ch, task, m["method_id"]))
            details[str(m["method_id"])] = scrape.parse_method_info(html, detail_fields)
            time.sleep(1)
        details = {k: {f: v.get(f) for f in detail_fields} for k, v in details.items()}
        _write_json(cache_path, details)
    methods = [{**base, **m, **details.get(str(m["method_id"]), {})} for m in lb.methods]

    changed = old_methods != methods
    meta_old = _read_json(tdir / "meta.json", {})
    snapshot = now_iso()

    primary = comp.get("primary_metric") or ("score" if "score" in metric_keys else (metric_keys or [None])[0])
    meta = {
        "competition": cid,
        "title": comp.get("title") or lb.challenge_title,
        "challenge_title": lb.challenge_title,
        "ch": ch,
        "task": task,
        "task_title": lb.task_title,
        "source_url": source,
        "metrics": [{"key": c.key, "label": c.label} for c in lb.metrics],
        "attributes": [{"key": c.key, "label": c.label} for c in lb.attributes],
        "detail_fields": list(detail_fields),
        "primary_metric": primary,
        "higher_is_better": comp.get("higher_is_better", True),
        "n_methods": len(methods),
        "n_baselines": sum(m["is_baseline"] for m in methods),
        "last_changed": snapshot if changed else meta_old.get("last_changed", snapshot),
    }
    if changed or meta != meta_old:
        _write_json(tdir / "meta.json", meta)
    if not changed:
        return meta != meta_old

    _write_json(tdir / "latest.json", {"competition": cid, "task": task, "snapshot": snapshot, "methods": methods})
    label = {c.key: c.label for c in lb.metrics}
    wide_fields = BASE_FIELDS + attr_keys + metric_keys + list(detail_fields)
    _write_csv(tdir / "wide.csv", wide_fields, methods)
    long_rows = [{**m, "metric": k, "metric_label": label[k], "value": m.get(k)} for m in methods for k in metric_keys]
    _write_csv(tdir / "latest.csv", BASE_FIELDS + attr_keys + ["metric", "metric_label", "value"], long_rows)
    events = history_events(old_methods, methods, metric_keys, snapshot, base)
    if events:
        _write_csv(tdir / "history.csv", HISTORY_FIELDS, events, append=True)
    print(f"  {cid} task {task}: {len(methods)} methods, {len(events)} history events")
    return True


def run(config: Path, out: Path, site: Path | None, only: str | None = None,
        fetch: Callable[[Any, str], str] = scrape.fetch) -> int:
    comps = [c for c in load_config(config) if not only or c["id"] == only]
    out.mkdir(parents=True, exist_ok=True)
    session = scrape.make_session()
    any_change, errors, index = False, {}, []

    for comp in comps:
        for task in comp.get("tasks") or [1]:
            name = f"{comp['id']}/task{task}"
            print(f"Updating {name}")
            try:
                any_change |= update_task(comp, int(task), out, session, fetch)
            except Exception as e:  # keep the other tasks going; report at the end
                errors[name] = f"{type(e).__name__}: {e}"
                print(f"  ERROR {errors[name]}", file=sys.stderr)
            meta = _read_json(out / "data" / comp["id"] / f"task{task}" / "meta.json", None)
            if meta:
                index.append({k: meta.get(k) for k in ("competition", "title", "ch", "task", "task_title",
                                                       "primary_metric", "n_methods", "last_changed")}
                             | {"path": f"data/{comp['id']}/task{task}/"})

    index_path = out / "data" / "index.json"
    if not only and index != _read_json(index_path, None):
        _write_json(index_path, index)

    status_path = out / "data" / "status.json"
    status = _read_json(status_path, {})
    last = status.get("last_checked")
    stale = not last or datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00")) > STATUS_HEARTBEAT
    if any_change or stale or status.get("errors", {}) != errors:
        _write_json(status_path, {"last_checked": now_iso(), "errors": errors})

    if site:
        shutil.copytree(site, out, dirs_exist_ok=True)
        (out / ".nojekyll").touch()
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=Path("competitions.yml"))
    p.add_argument("--out", type=Path, default=Path("out"), help="checkout of the gh-pages branch")
    p.add_argument("--site", type=Path, help="static site files to copy into --out")
    p.add_argument("--only", help="update a single competition id")
    a = p.parse_args(argv)
    sys.exit(run(a.config, a.out, a.site, a.only))


if __name__ == "__main__":
    main()
