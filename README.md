# RRC leaderboard data

A mirror of the **public** leaderboards of competitions run on the
[Robust Reading Competition](https://rrc.cvc.uab.es/) (RRC) platform. The data is published as
plain CSV/JSON on GitHub Pages so competitions can embed leaderboards and build their own plots.
No access to the RRC code or database is needed.

- **Explorer:** https://computervisioncentre.github.io/rrc-leaderboard-data/
- **Data:** `https://computervisioncentre.github.io/rrc-leaderboard-data/data/<competition>/task<N>/…`

```
 rrc.cvc.uab.es  ──(hourly GitHub Action, anonymous HTTP)──►  gh-pages branch  ──►  GitHub Pages
 public results       parse table, log changes                 data/*.csv|json        explorer, embed, data
                                                                                         │
                         competition sites / notebooks / plot scripts  ◄─────────────────┘
```

## What is mirrored (and what is not)

The mirror makes **anonymous** requests, so it gets exactly what any visitor to RRC sees. During a
competition this is usually just the baselines and methods that participants made public. Private
methods never appear here, and no RRC credentials exist anywhere in this repository.

Each method row has:

| field | meaning |
|---|---|
| `position` | row order on RRC (RRC sorts by the primary metric) |
| `method_id`, `method`, `url` | RRC method id, name and link to its RRC page |
| `date` | submission date shown on RRC |
| `is_baseline` | name matches `baseline_pattern` (default: starts with `[Baseline]`) |
| `is_competition` | RRC marks it as an official competition entry (shown blue on RRC) |
| `has_description`, `has_paper`, `has_code` | RRC shows the corresponding icon |
| attribute columns | non-numeric columns of the RRC table, e.g. `parameters` ("Up to 8B parameters") |
| metric columns | every numeric column; keys come from RRC (`score`, `score_maps`, …), labels are in `meta.json` |
| `authors`, `affiliation`, `description` | only if the competition opts in with `method_details` (emails are never collected) |

## Files

For each `data/<competition>/task<N>/`:

| file | contents |
|---|---|
| `meta.json` | titles, RRC URL, list of metrics (`key`, `label`), attributes, `primary_metric`, `higher_is_better`, `last_changed` |
| `wide.csv` | current leaderboard, one row per method, one column per metric |
| `latest.csv` | current leaderboard in long format: one row per method × metric (`metric`, `metric_label`, `value`). Ready for seaborn, Plotly Express or Vega-Lite |
| `latest.json` | current leaderboard as JSON (`methods: [...]`), used by the website |
| `history.csv` | change log: one row each time a (method, metric) value is `added`, `updated`, or a method is `removed` (`snapshot` is the UTC time the mirror saw it) |

Also `data/index.json` (all mirrored leaderboards) and `data/status.json` (last check, errors).
The status file is refreshed at least weekly, so you can tell how fresh the data is.

`history.csv` records changes, not full copies, so it stays small. To rebuild the leaderboard
as it was at any moment, use `rrc_leaderboard.state_at(history, "2026-04-01")`.

## Making plots

Each competition decides how to plot its data. Three options, which can be combined:

**1. Python (e.g. in the competition's own repo or a notebook)**

```bash
pip install "rrc-leaderboard[plots] @ git+https://github.com/ComputerVisionCentre/rrc-leaderboard-data"
```
```python
import rrc_leaderboard as rl
meta = rl.meta("docvqa-2026", task=1)
wide = rl.load("docvqa-2026", task=1, kind="wide")     # one row per method
long = rl.load("docvqa-2026", task=1)                  # one row per method x metric
hist = rl.load("docvqa-2026", task=1, kind="history")
```
or simply `pd.read_csv("https://computervisioncentre.github.io/rrc-leaderboard-data/data/docvqa-2026/task1/wide.csv")`.

[`examples/plots.py`](examples/plots.py) makes a ranking chart, a per-category heatmap, a
score-over-time chart and a score-by-model-size chart. [`examples/competition-plots.yml`](examples/competition-plots.yml)
is a workflow that regenerates such plots hourly and publishes them on the competition's own GitHub Pages site.

**2. Interactive charts in the browser.** GitHub Pages lets other websites read these files directly
(it sends CORS headers), so any page can draw them with Vega-Lite, Plotly, D3, etc. See
[`examples/vega/score-by-parameters.vl.json`](examples/vega/score-by-parameters.vl.json): paste it
into the [Vega editor](https://vega.github.io/editor) to try it.

**3. Embed the ready-made table**

```html
<iframe src="https://computervisioncentre.github.io/rrc-leaderboard-data/embed.html?c=docvqa-2026&t=1&top=10"
        width="100%" height="480" style="border:0"></iframe>
```
Parameters: `c` competition, `t` task, `only=baselines|competition`, `top=N`,
`metrics=score,score_maps`, `attrs=0` to hide attribute columns. The table follows the visitor's light/dark setting.

## Adding a competition

Add an entry to [`competitions.yml`](competitions.yml) and push to `main`. The update workflow runs
immediately and then every hour:

```yaml
competitions:
  - id: mycomp-2027        # used in URLs
    ch: 40                 # ?ch= on RRC
    tasks: [1, 2]          # ?task= on RRC
    # primary_metric: hmean
    # higher_is_better: true
    # method_details: [authors, affiliation]
```

## How it works

- `src/rrc_leaderboard/scrape.py` downloads `https://rrc.cvc.uab.es/?ch=<ch>&com=evaluation&task=<task>`
  and parses the ranking table (`table#table-N-N`). The RRC CSV export cannot be used because it
  requires a login.
- `src/rrc_leaderboard/mirror.py` writes the files and appends to `history.csv`. Nothing is written
  if nothing changed. If one leaderboard fails (RRC down, layout changed), the others are still
  published, the error is recorded in `status.json`, and the workflow run is marked failed so
  maintainers get notified.
- `.github/workflows/update.yml` runs it hourly and commits to the `gh-pages` branch, which GitHub Pages serves.
- The table layout is detected from its headers, so new metric columns appear automatically. If RRC
  changes its page structure, the parser raises an error and the previous data stays in place.

### Note on the RRC TLS certificate

`rrc.cvc.uab.es` sends only its own certificate, without the intermediate certificate
(currently `GEANT TLS RSA 1`) that links it to a trusted root. Browsers fill the gap
themselves, but `curl`, Python and similar tools fail with *"unable to verify the first certificate"*.
This is not expected to change on the server, so the mirror handles it itself, with
certificate verification always on:

1. It ships the current intermediate ([`certs/geant-tls-rsa-1.pem`](src/rrc_leaderboard/certs/geant-tls-rsa-1.pem)).
2. If RRC's certificate is reissued under a different intermediate and verification fails, the
   mirror reads the intermediate's download URL from RRC's certificate ("CA Issuers"),
   downloads it, and retries once. A downloaded certificate is only used if a root in
   `certifi` signed it, so tampering with the plain-HTTP download can't make anything untrusted
   pass. The workflow log then says *"fetched intermediate …; consider adding it to
   src/rrc_leaderboard/certs/"*: adding it saves one extra request per run, but isn't required.

To fetch RRC yourself with `curl`, add the intermediate to the trusted list:
`cat /etc/ssl/certs/ca-certificates.crt src/rrc_leaderboard/certs/geant-tls-rsa-1.pem > rrc-ca.pem && curl --cacert rrc-ca.pem https://rrc.cvc.uab.es/`.

## Development

```bash
pip install -e ".[dev,plots]"
pytest
rrc-mirror --out out --site site   # full local run; then: python -m http.server -d out
```
The tests use a made-up page fixture. Don't commit pages saved while logged in: they can contain private methods.

## License

The contents of this repository are licensed under
[Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE).

The mirrored leaderboard results come from the public pages of the
[Robust Reading Competition](https://rrc.cvc.uab.es/). When you use them, credit the RRC and the
competition, and follow the competition's own terms.

