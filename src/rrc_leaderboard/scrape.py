"""Fetch and parse the *public* leaderboard pages of the Robust Reading Competition.

Only anonymous requests are made, so the data is exactly what any visitor sees:
private methods are never visible here.
"""

from __future__ import annotations

import re
import ssl
import tempfile
import time
import warnings
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import certifi
import requests
from bs4 import BeautifulSoup
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import AuthorityInformationAccessOID

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
_FETCHED_INTERMEDIATES: list[str] = []


def ca_bundle() -> str:
    """Trusted roots (certifi) + intermediate certificates that rrc.cvc.uab.es does not send.

    The server only sends its leaf certificate, so non-browser clients cannot build the
    chain. We ship the current intermediate (certs/), and `fetch_intermediates()` adds
    newer ones at run time if RRC's certificate is reissued under a different one.
    Verification always stays on.
    """
    global _CA_BUNDLE
    if _CA_BUNDLE is None:
        parts = [
            Path(certifi.where()).read_text(),
            resources.files("rrc_leaderboard").joinpath("certs/geant-tls-rsa-1.pem").read_text(),
            *_FETCHED_INTERMEDIATES,
        ]
        tmp = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
        tmp.write("\n".join(parts))
        tmp.close()
        _CA_BUNDLE = tmp.name
    return _CA_BUNDLE


def _issuer_urls(cert: x509.Certificate) -> list[str]:
    """'CA Issuers' URLs from a certificate's Authority Information Access extension."""
    try:
        aia = cert.extensions.get_extension_for_class(x509.AuthorityInformationAccess).value
    except x509.ExtensionNotFound:
        return []
    return [d.access_location.value for d in aia if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS]


def _trusted_roots() -> list[x509.Certificate]:
    """certifi roots, parsed one by one so a single malformed old root can't break loading."""
    roots = []
    end = "-----END CERTIFICATE-----"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for block in Path(certifi.where()).read_text().split(end)[:-1]:
            try:
                roots.append(x509.load_pem_x509_certificate((block[block.index("-----BEGIN"):] + end).encode()))
            except ValueError:
                continue
    return roots


def _signed_by_trusted_root(cert: x509.Certificate) -> bool:
    for root in _trusted_roots():
        if root.subject == cert.issuer:
            try:
                cert.verify_directly_issued_by(root)
                return True
            except (ValueError, TypeError, InvalidSignature):
                continue
    return False


def fetch_intermediates(host: str, port: int = 443) -> int:
    """Download the intermediate(s) named in the server certificate (AIA "CA Issuers").

    AIA downloads are plain HTTP, so a downloaded certificate is only accepted if it is
    directly signed by a root in certifi: tampering can make the request fail, but can
    never make an untrusted certificate trusted. Returns the number of certificates added.
    """
    global _CA_BUNDLE
    leaf = x509.load_pem_x509_certificate(ssl.get_server_certificate((host, port)).encode())
    added = 0
    for url in _issuer_urls(leaf):
        try:
            data = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT}).content
            try:
                cert = x509.load_der_x509_certificate(data)
            except ValueError:
                cert = x509.load_pem_x509_certificate(data)
        except (requests.RequestException, ValueError) as e:
            print(f"  could not download intermediate {url}: {e}")
            continue
        if not _signed_by_trusted_root(cert):
            print(f"  ignoring {url}: not signed by a trusted root")
            continue
        _FETCHED_INTERMEDIATES.append(cert.public_bytes(serialization.Encoding.PEM).decode())
        print(f"  note: fetched intermediate {cert.subject.rfc4514_string()} from {url}; "
              "consider adding it to src/rrc_leaderboard/certs/")
        added += 1
    if added:
        _CA_BUNDLE = None
    return added


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
    failures, healed = 0, False
    while True:
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            return r.text
        except requests.exceptions.SSLError:
            # Most likely RRC's certificate now uses an intermediate we don't have yet.
            if healed or not fetch_intermediates(urlparse(url).hostname):
                raise
            healed = True
            session.verify = ca_bundle()
        except requests.RequestException:
            failures += 1
            if failures >= retries:
                raise
            time.sleep(5 * failures)


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
