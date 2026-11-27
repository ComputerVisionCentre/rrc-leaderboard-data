"""Load mirrored leaderboards as pandas DataFrames.

    import rrc_leaderboard as rl
    df = rl.load("docvqa-2026", task=1)            # long: one row per method x metric
    wide = rl.load("docvqa-2026", task=1, kind="wide")
    hist = rl.load("docvqa-2026", task=1, kind="history")
    meta = rl.meta("docvqa-2026", task=1)

Only pandas is needed (installed with the `client` extra). Any CSV can also be read
directly, e.g. `pd.read_csv(f"{DATA_URL}/docvqa-2026/task1/latest.csv")`.
"""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

DATA_URL = os.environ.get(
    "RRC_LEADERBOARD_DATA_URL", "https://computervisioncentre.github.io/rrc-leaderboard-data/data"
).rstrip("/")
_KINDS = {"latest": "latest.csv", "wide": "wide.csv", "history": "history.csv"}


def _url(competition: str, task: int, filename: str, base_url: str | None) -> str:
    return f"{(base_url or DATA_URL).rstrip('/')}/{competition}/task{task}/{filename}"


def _get_json(url: str):
    if "://" not in url:  # local directory, e.g. a checkout of the gh-pages branch
        with open(url, encoding="utf-8") as f:
            return json.load(f)
    with urlopen(Request(url, headers={"User-Agent": "rrc-leaderboard-client"}), timeout=60) as r:
        return json.load(r)


def competitions(base_url: str | None = None) -> list[dict]:
    """Every mirrored competition/task, as listed in data/index.json."""
    return _get_json(f"{(base_url or DATA_URL).rstrip('/')}/index.json")


def meta(competition: str, task: int = 1, base_url: str | None = None) -> dict:
    """Titles, metric keys/labels, primary metric and last change time."""
    return _get_json(_url(competition, task, "meta.json", base_url))


def load(competition: str, task: int = 1, kind: str = "latest", base_url: str | None = None):
    """Return a DataFrame. kind: 'latest' (long), 'wide' or 'history'.

    `base_url` may also be a local directory (e.g. a checkout of the gh-pages branch + '/data').
    """
    import pandas as pd

    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {sorted(_KINDS)}")
    df = pd.read_csv(_url(competition, task, _KINDS[kind], base_url))
    for col in ("date", "snapshot"):
        if col in df:
            df[col] = pd.to_datetime(df[col], utc=(col == "snapshot"))
    return df


def state_at(history, when) -> "pd.DataFrame":  # noqa: F821
    """Rebuild the leaderboard (long format) as it was at time `when` from a history frame."""
    import pandas as pd

    when = pd.Timestamp(when, tz="UTC") if pd.Timestamp(when).tzinfo is None else pd.Timestamp(when)
    h = history[history["snapshot"] <= when]
    removed = h[h["event"] == "removed"].groupby("method_id")["snapshot"].max()
    last = h[h["event"] != "removed"].sort_values("snapshot").groupby(["method_id", "metric"]).tail(1)
    alive = last["method_id"].map(removed).isna() | (last["snapshot"] > last["method_id"].map(removed))
    return last[alive].drop(columns=["event"]).reset_index(drop=True)
