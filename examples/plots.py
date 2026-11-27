"""Example: custom plots for one competition, built from the mirrored data.

Copy this into your competition's repo and change it freely. Run:

    pip install "rrc-leaderboard[plots] @ git+https://github.com/ComputerVisionCentre/rrc-leaderboard-data"
    python plots.py --competition docvqa-2026 --task 1 --out figures/

Pass --data-url to read a local checkout of the gh-pages branch instead (e.g. ../out/data).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import rrc_leaderboard as rl  # noqa: E402

COLORS = {"Baseline": "#e0a100", "Competition entry": "#2f6fd6", "Other public": "#8a94a3"}


def method_type(row) -> str:
    if row.is_baseline:
        return "Baseline"
    return "Competition entry" if row.is_competition else "Other public"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--competition", default="docvqa-2026")
    p.add_argument("--task", type=int, default=1)
    p.add_argument("--out", type=Path, default=Path("figures"))
    p.add_argument("--data-url", default=None)
    p.add_argument("--top", type=int, default=15)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    meta = rl.meta(a.competition, a.task, base_url=a.data_url)
    wide = rl.load(a.competition, a.task, kind="wide", base_url=a.data_url)
    wide["type"] = wide.apply(method_type, axis=1)
    primary = meta["primary_metric"]
    label = {m["key"]: m["label"] for m in meta["metrics"]}
    cats = [m["key"] for m in meta["metrics"] if m["key"] != primary]
    top = wide.sort_values(primary, ascending=not meta["higher_is_better"]).head(a.top)

    # 1. Ranking bar chart
    fig, ax = plt.subplots(figsize=(8, 0.35 * len(top) + 1))
    ax.barh(top["method"], top[primary], color=top["type"].map(COLORS))
    ax.invert_yaxis()
    ax.set_xlabel(label[primary])
    ax.set_title(f"{meta['title']} – top {len(top)}")
    fig.tight_layout()
    fig.savefig(a.out / "ranking.svg")

    # 2. Per-category heatmap
    if cats:
        fig, ax = plt.subplots(figsize=(1 + 0.9 * len(cats), 0.35 * len(top) + 1.5))
        im = ax.imshow(top[cats].to_numpy(dtype=float), cmap="Blues", vmin=0, vmax=1, aspect="auto")
        ax.set_xticks(range(len(cats)), [label[c] for c in cats], rotation=40, ha="right")
        ax.set_yticks(range(len(top)), top["method"])
        fig.colorbar(im, ax=ax, shrink=0.6)
        fig.tight_layout()
        fig.savefig(a.out / "categories.svg")

    # 3. Score vs submission date, with the best-so-far frontier
    fig, ax = plt.subplots(figsize=(8, 4))
    for t, g in wide.groupby("type"):
        ax.scatter(g["date"], g[primary], label=t, color=COLORS[t], s=25)
    s = wide.dropna(subset=["date", primary]).sort_values("date")
    best = s[primary].cummax() if meta["higher_is_better"] else s[primary].cummin()
    ax.step(s["date"], best, where="post", color="grey", linestyle="--", label="best so far")
    ax.set_ylabel(label[primary])
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(a.out / "progress.svg")

    # 4. Example of a competition-specific view: score by model-size bucket
    if "parameters" in wide:
        fig, ax = plt.subplots(figsize=(8, 4))
        groups = wide.dropna(subset=["parameters"]).groupby("parameters")[primary]
        ax.boxplot([g.values for _, g in groups], vert=False)
        ax.set_yticks(range(1, len(groups) + 1), list(groups.groups))
        ax.set_xlabel(label[primary])
        fig.tight_layout()
        fig.savefig(a.out / "by_parameters.svg")

    print(f"Wrote figures to {a.out}/")


if __name__ == "__main__":
    main()
