"""Fetch and parse the *public* leaderboard pages of the Robust Reading Competition.

Only anonymous requests are made, so the data is exactly what any visitor sees:
private methods are never visible here.
"""

from __future__ import annotations

import re
import tempfile
import time
from dataclasses import dataclass, field
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import certifi
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://rrc.cvc.uab.es/"
USER_AGENT = "rrc-leaderboard-mirror (+https://github.com/ComputerVisionCentre/rrc-leaderboard-data)"
DEFAULT_BASELINE_PATTERN = r"^\s*\[baseline\]"

# Icon columns next to the method name: their presence means the method has it.
ICON_FLAGS = {
    "fa-file-text-o": "has_description",
    "fa-file-pdf-o": "has_paper",
    "fa-file-code-o": "has_code",
}
# Fields of the method info page that may be mirrored. Email is deliberately absent.
DETAIL_FIELDS = ("authors", "affiliation", "description")


class ParseError(RuntimeError):
    """The page does not look like an RRC ranking page (layout changed?)."""


@dataclass
class Column:
    key: str
    label: str
    kind: str  # "metric" (numeric) or "attribute" (text, e.g. parameter-count bucket)


@dataclass
class Leaderboard:
    challenge_title: str | None
    task_title: str | None
    columns: list[Column]
    methods: list[dict[str, Any]] = field(default_factory=list)

    @property
    def metrics(self) -> list[Column]:
        return [c for c in self.columns if c.kind == "metric"]

    @property
    def attributes(self) -> list[Column]:
        return [c for c in self.columns if c.kind == "attribute"]


# --------------------------------------------------------------------------- fetching

_CA_BUNDLE: str | None = None


def ca_bundle() -> str:
    """certifi roots + the GEANT intermediate that rrc.cvc.uab.es does not send.

    The server only sends its leaf certificate, so non-browser clients cannot build
    the chain. Adding the (public) intermediate keeps full verification on. Once the
    server sends the full chain this is harmless and can be removed.
    """
    global _CA_BUNDLE
    if _CA_BUNDLE is None:
        intermediate = resources.files("rrc_leaderboard").joinpath("certs/geant-tls-rsa-1.pem").read_text()
        with open(certifi.where()) as f:
            roots = f.read()
        tmp = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
        tmp.write(roots + "\n" + intermediate)
        tmp.close()
        _CA_BUNDLE = tmp.name
    return _CA_BUNDLE


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.verify = ca_bundle()
    return session


def leaderboard_url(ch: int, task: int) -> str:
    return f"{BASE_URL}?ch={ch}&com=evaluation&task={task}"


def method_url(ch: int, task: int, method_id: int) -> str:
    return f"{BASE_URL}?ch={ch}&com=evaluation&view=method_info&task={task}&m={method_id}"


def fetch(session: requests.Session, url: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------- parsing


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "col"


def _to_float(text: str) -> float | None:
    text = text.strip().rstrip("%")
    if not text or text in {"-", "—", "N/A", "n/a"}:
        return None
    return float(text)


def _is_numeric(values: list[str]) -> bool:
    seen = False
    for v in values:
        try:
            if _to_float(v) is not None:
                seen = True
        except ValueError:
            return False
    return seen


def parse_leaderboard(html: str, baseline_pattern: str = DEFAULT_BASELINE_PATTERN) -> Leaderboard:
    soup = BeautifulSoup(html, "html.parser")

    crumbs = [li.get_text(" ", strip=True) for li in soup.select("ul.breadcrumb li")]
    challenge_title = crumbs[1] if len(crumbs) > 1 else None
    task_title = crumbs[-1] if len(crumbs) > 3 else None

    table = soup.find("table", id=re.compile(r"^table-\d+-\d+$"))
    if table is None:
        raise ParseError("No ranking table (id='table-N-N') found on the page")

    header = table.find("tr")
    ths = header.find_all(["th", "td"])
    labels = [th.get_text(" ", strip=True) for th in ths]
    try:
        date_i, method_i = labels.index("Date"), labels.index("Method")
    except ValueError as e:
        raise ParseError(f"Unexpected table header: {labels}") from e
    value_cols = [
        (i, ths[i].get("data-n") or _slug(labels[i]), labels[i])
        for i in range(len(ths))
        if i not in (date_i, method_i) and labels[i]
    ]

    baseline_re = re.compile(baseline_pattern, re.I)
    raw_rows = []
    for tr in table.find_all("tr")[1:]:
        classes = tr.get("class") or []
        if "user" in classes:
            continue  # a logged-in viewer's own private method: never mirror it
        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue
        if len(tds) != len(ths):
            raise ParseError(f"Row has {len(tds)} cells but header has {len(ths)}")
        cell = tds[method_i]
        link = cell.find("a")
        name = cell.get_text(" ", strip=True)
        href = urljoin(BASE_URL, link["href"]) if link and link.get("href") else None
        mid = parse_qs(urlparse(href).query).get("m", [None])[0] if href else None
        flags = dict.fromkeys(ICON_FLAGS.values(), False)
        for icon in tr.find_all("i"):
            for c in icon.get("class") or []:
                if c in ICON_FLAGS:
                    flags[ICON_FLAGS[c]] = True
        raw_rows.append(
            {
                "position": len(raw_rows) + 1,
                "method_id": int(mid) if mid and mid.isdigit() else None,
                "method": name,
                "date": tds[date_i].get_text(strip=True) or None,
                "is_baseline": bool(baseline_re.search(name)),
                "is_competition": "competition" in classes,
                **flags,
                "url": href,
                "_values": {key: tds[i].get_text(" ", strip=True) for i, key, _ in value_cols},
            }
        )

    columns = [
        Column(key, label, "metric" if _is_numeric([r["_values"][key] for r in raw_rows]) else "attribute")
        for _, key, label in value_cols
    ]
    for row in raw_rows:
        values = row.pop("_values")
        for col in columns:
            v = values[col.key]
            row[col.key] = _to_float(v) if col.kind == "metric" else (v or None)
    return Leaderboard(challenge_title, task_title, columns, raw_rows)


def parse_method_info(html: str, fields: tuple[str, ...] = DETAIL_FIELDS) -> dict[str, str | None]:
    """Parse `<p>Label: <b>value</b></p>` pairs from a method info page.

    Only the requested fields among DETAIL_FIELDS are returned; email is never collected.
    """
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".method_info .panel-body") or soup
    found: dict[str, str | None] = {}
    for p in body.find_all("p"):
        b = p.find("b")
        label = (p.contents[0] if p.contents else "")
        if not b or not isinstance(label, str):
            continue
        key = label.strip().rstrip(":").strip().lower()
        if key in fields and key in DETAIL_FIELDS:
            found[key] = b.get_text("\n", strip=True) or None
    return {f: found.get(f) for f in fields if f in DETAIL_FIELDS}
