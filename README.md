# RRC leaderboard data

Mirrors the **public** leaderboards of competitions hosted on the
[Robust Reading Competition](https://rrc.cvc.uab.es/) (RRC) platform into plain CSV/JSON files,
published on GitHub Pages. Use them to embed a leaderboard on another site or to make your own plots.
No access to the RRC code or database is needed.

- **Leaderboard page:** https://computervisioncentre.github.io/rrc-leaderboard-data/
- **Data:** `https://computervisioncentre.github.io/rrc-leaderboard-data/data/<competition>/task<N>/<file>`

A GitHub Action runs every hour. It downloads the RRC results page of each task in
[`competitions.yml`](competitions.yml), parses the ranking table, and commits any changes to the
`gh-pages` branch, which GitHub Pages serves.

## Adding a competition

Add an entry to [`competitions.yml`](competitions.yml) and push to `main`. The update workflow runs
immediately and then every hour.

```yaml
competitions:
  - id: mycomp-2027        # used in URLs
    ch: 40                 # the ?ch= number in RRC URLs
    tasks: [1, 2]          # the &task= numbers in RRC URLs
    # primary_metric: hmean                   # default: "score", else the first metric
    # higher_is_better: true
    # baseline_pattern: '^\s*\[baseline\]'    # regex on method names (default shown)
    # method_details: [authors, affiliation]  # opt-in, fetched from each method page; emails are never collected
```

To run it by hand, go to **Actions → Update leaderboards → Run workflow**.

## What is mirrored

The mirror makes **anonymous** requests, so it gets exactly what any visitor to RRC sees.
While a competition is running, that is usually just the baselines and any methods participants
have made public. Private methods never appear here, and the repository contains no RRC credentials.

## Data files

Per task, in `data/<competition>/task<N>/`:

| file | contents |
|---|---|
| `wide.csv` | current leaderboard, one row per method, one column per metric |
| `latest.csv` | same data in long format: one row per method × metric (`metric`, `metric_label`, `value`) |
| `latest.json` | current leaderboard as JSON |
| `meta.json` | titles, RRC URL, metric keys and labels, `primary_metric`, `last_changed` |
| `history.csv` | change log: one row each time a (method, metric) value is `added` or `updated`, or a method is `removed`, with the UTC time it was seen |

Columns: `position` (row order on RRC), `method_id`, `method`, `url`, `date` (submission date),
`is_baseline`, `is_competition` (an official competition entry, shown in blue on RRC),
`has_description`, `has_paper` and `has_code` (whether RRC shows the corresponding icon),
non-numeric RRC columns (e.g. `parameters`), and one column per metric (e.g. `score`, `score_maps`).

`data/index.json` lists all mirrored leaderboards. `data/status.json` has the last check time and
any errors; it is refreshed at least weekly.

```python
import pandas as pd
wide = pd.read_csv("https://computervisioncentre.github.io/rrc-leaderboard-data/data/docvqa-2026/task1/wide.csv")
```

GitHub Pages lets any website load these files directly (it sends CORS headers), so they can also
be used from JavaScript (Vega-Lite, Plotly, D3, …).

### Embedding the table

```html
<iframe src="https://computervisioncentre.github.io/rrc-leaderboard-data/embed.html?c=docvqa-2026&t=1&top=10"
        width="100%" height="480" style="border:0"></iframe>
```
Parameters: `c` competition id, `t` task, `only=baselines|competition`, `top=N`,
`metrics=score,score_maps`, `attrs=0` to hide non-numeric columns.

## How it works

| path | role |
|---|---|
| `src/rrc_leaderboard/scrape.py` | downloads `https://rrc.cvc.uab.es/?ch=<ch>&com=evaluation&task=<task>` and parses the ranking table (`table#table-N-N`) |
| `src/rrc_leaderboard/mirror.py` | writes the data files and appends to `history.csv`; writes nothing if nothing changed |
| `.github/workflows/update.yml` | runs the mirror hourly and pushes to `gh-pages` |
| `site/` | the leaderboard page and `embed.html`, copied to `gh-pages` on each run |

The RRC CSV export can't be used because it requires a login. Metric columns are detected from
the table headers, so new metrics appear automatically. If RRC changes its page layout, parsing
fails, the previous data stays in place, the error goes into `status.json`, and the workflow run
fails, which notifies the maintainers.

### RRC TLS certificate

`rrc.cvc.uab.es` sends only its own certificate, without the intermediate certificate (currently
`GEANT TLS RSA 1`). Browsers cope, but `curl`, Python and similar tools fail with
*"unable to verify the first certificate"*. The mirror handles this itself, with verification always on:

1. It ships the current intermediate ([`certs/geant-tls-rsa-1.pem`](src/rrc_leaderboard/certs/geant-tls-rsa-1.pem)).
2. If the certificate is reissued under a different intermediate, it downloads the intermediate
   named in RRC's certificate ("CA Issuers") and retries. The downloaded certificate is accepted
   only if a root in `certifi` signed it.

## Development

```bash
pip install -e ".[dev]"
pytest
rrc-mirror --out out --site site   # full local run, then: python -m http.server -d out
```

The tests use a made-up page fixture. Don't commit RRC pages saved while logged in: they can
contain private methods.

## License

The contents of this repository are licensed under
[Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE).
The mirrored results come from the public pages of the [Robust Reading Competition](https://rrc.cvc.uab.es/);
when using them, credit the RRC and the competition.
