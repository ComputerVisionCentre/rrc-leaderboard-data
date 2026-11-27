import csv
import json
from pathlib import Path

import pytest

from rrc_leaderboard import mirror, scrape

FIX = Path(__file__).parent / "fixtures"
PAGE = (FIX / "leaderboard.html").read_text()
METHOD = (FIX / "method_info.html").read_text()


def test_parse_leaderboard():
    lb = scrape.parse_leaderboard(PAGE)
    assert lb.challenge_title == "Test Challenge 2030"
    assert lb.task_title == "Task 2 - Reading"
    assert [c.key for c in lb.metrics] == ["score", "score_maps", "score_comics"]
    assert [(c.key, c.label) for c in lb.attributes] == [("parameters", "Total Parameters")]

    names = [m["method"] for m in lb.methods]
    assert "My private method" not in names  # 'user' rows are never mirrored
    assert names == ["Alpha & Co", "Beta", "[Baseline] Gamma (zero-shot)"]

    alpha, beta, gamma = lb.methods
    assert alpha["method_id"] == 1001 and alpha["position"] == 1
    assert alpha["url"] == "https://rrc.cvc.uab.es/?ch=99&com=evaluation&view=method_info&task=2&m=1001"
    assert alpha["score"] == 0.9 and alpha["parameters"] == "Up to 8B parameters"
    assert alpha["has_description"] and alpha["has_paper"] and alpha["has_code"]
    assert not alpha["is_competition"] and not alpha["is_baseline"]
    assert beta["is_competition"] and beta["score_maps"] is None and not beta["has_paper"]
    assert gamma["is_baseline"] and gamma["parameters"] is None


def test_layout_change_is_reported():
    with pytest.raises(scrape.ParseError):
        scrape.parse_leaderboard("<html><body><p>maintenance</p></body></html>")
    broken = PAGE.replace("<th>Method</th>", "<th>Name</th>")
    with pytest.raises(scrape.ParseError):
        scrape.parse_leaderboard(broken)


def test_method_info_never_returns_email():
    info = scrape.parse_method_info(METHOD, ("authors", "affiliation", "description", "email"))
    assert info == {"authors": "Ada Example, Bo Sample", "affiliation": "Example University",
                    "description": "Line one\nLine two"}
    assert scrape.parse_method_info(METHOD, ("authors",)) == {"authors": "Ada Example, Bo Sample"}


class FakeRRC:
    def __init__(self, page):
        self.page, self.calls = page, []

    def __call__(self, session, url):
        self.calls.append(url)
        return METHOD if "method_info" in url else self.page


def _config(tmp_path, extra=""):
    p = tmp_path / "competitions.yml"
    p.write_text(f"competitions:\n  - id: test\n    ch: 99\n    tasks: [2]\n{extra}")
    return p


def _rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_mirror_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "now_iso", lambda: "2030-05-10T10:00:00Z")
    out, cfg, rrc = tmp_path / "out", _config(tmp_path), FakeRRC(PAGE)
    assert mirror.run(cfg, out, None, fetch=rrc) == 0
    tdir = out / "data" / "test" / "task2"

    meta = json.loads((tdir / "meta.json").read_text())
    assert meta["primary_metric"] == "score" and meta["n_methods"] == 3 and meta["n_baselines"] == 1
    assert json.loads((out / "data" / "index.json").read_text())[0]["path"] == "data/test/task2/"
    assert len(_rows(tdir / "wide.csv")) == 3
    assert len(_rows(tdir / "latest.csv")) == 9
    assert {r["event"] for r in _rows(tdir / "history.csv")} == {"added"}
    assert not any("method_info" in u for u in rrc.calls)  # details are opt-in

    # Nothing changed: no file is rewritten.
    before = {p: p.stat().st_mtime_ns for p in tdir.iterdir()}
    assert mirror.run(cfg, out, None, fetch=rrc) == 0
    assert {p: p.stat().st_mtime_ns for p in tdir.iterdir()} == before

    # One score changes and one method disappears: only those events are logged.
    monkeypatch.setattr(mirror, "now_iso", lambda: "2030-05-10T11:00:00Z")
    page2 = PAGE.replace(">0.7000<", ">0.7500<").replace("[Baseline] Gamma", "XX").replace("m=1004", "m=1005")
    assert mirror.run(cfg, out, None, fetch=FakeRRC(page2)) == 0
    events = _rows(tdir / "history.csv")[9:]
    kinds = sorted((e["event"], e["method"], e["metric"]) for e in events)
    assert ("updated", "Beta", "score") in kinds
    assert ("removed", "[Baseline] Gamma (zero-shot)", "") in kinds
    assert sum(k[0] == "added" for k in kinds) == 3  # the renamed method counts as new


def test_empty_table_keeps_previous_data(tmp_path):
    out, cfg = tmp_path / "out", _config(tmp_path)
    mirror.run(cfg, out, None, fetch=FakeRRC(PAGE))
    import re
    empty = re.sub(r"<tbody>.*</tbody>", "<tbody></tbody>", PAGE, flags=re.S)
    assert mirror.run(cfg, out, None, fetch=FakeRRC(empty)) == 1
    assert len(_rows(out / "data" / "test" / "task2" / "wide.csv")) == 3
    status = json.loads((out / "data" / "status.json").read_text())
    assert "test/task2" in status["errors"]


def test_method_details_opt_in(tmp_path):
    out = tmp_path / "out"
    cfg = _config(tmp_path, "    method_details: [authors, affiliation]\n")
    rrc = FakeRRC(PAGE)
    mirror.run(cfg, out, None, fetch=rrc)
    wide = _rows(out / "data" / "test" / "task2" / "wide.csv")
    assert wide[0]["authors"] == "Ada Example, Bo Sample"
    assert "email" not in wide[0] and "someone@example.org" not in (out / "data" / "test" / "task2" / "latest.json").read_text()
    n = len(rrc.calls)
    mirror.run(cfg, out, None, fetch=rrc)
    assert len(rrc.calls) == n + 1  # cached: only the leaderboard is fetched again


def test_rejects_email_in_config(tmp_path):
    with pytest.raises(SystemExit):
        mirror.load_config(_config(tmp_path, "    method_details: [email]\n"))


# --------------------------------------------------------------------------- TLS workaround

def test_issuer_url_and_root_check():
    from cryptography import x509
    from importlib import resources
    leaf = x509.load_pem_x509_certificate((FIX / "rrc_leaf.pem").read_bytes())
    assert scrape._issuer_urls(leaf) == ["http://crt.harica.gr/HARICA-GEANT-TLS-R1.cer"]
    bundled = resources.files("rrc_leaderboard").joinpath("certs/geant-tls-rsa-1.pem").read_bytes()
    assert scrape._signed_by_trusted_root(x509.load_pem_x509_certificate(bundled))
    assert not scrape._signed_by_trusted_root(leaf)  # issued by an intermediate, not a root


class _Resp:
    text = "ok"

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, errors):
        self.errors, self.verify, self.calls = list(errors), None, 0

    def get(self, url, timeout):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return _Resp()


def test_fetch_repairs_missing_intermediate_once(monkeypatch):
    import requests
    calls = []
    monkeypatch.setattr(scrape, "fetch_intermediates", lambda host: calls.append(host) or 1)
    s = _Session([requests.exceptions.SSLError("unable to get local issuer certificate")])
    assert scrape.fetch(s, "https://rrc.cvc.uab.es/?ch=1", retries=1) == "ok"
    assert calls == ["rrc.cvc.uab.es"] and s.calls == 2

    s = _Session([requests.exceptions.SSLError("x"), requests.exceptions.SSLError("y")])
    with pytest.raises(requests.exceptions.SSLError):
        scrape.fetch(s, "https://rrc.cvc.uab.es/", retries=3)
    assert s.calls == 2  # repaired once, then gives up instead of looping


def test_fetch_does_not_hide_tls_errors_without_new_intermediate(monkeypatch):
    import requests
    monkeypatch.setattr(scrape, "fetch_intermediates", lambda host: 0)
    with pytest.raises(requests.exceptions.SSLError):
        scrape.fetch(_Session([requests.exceptions.SSLError("x")]), "https://rrc.cvc.uab.es/")
