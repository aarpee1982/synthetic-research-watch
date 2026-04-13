from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sites.json"
STATE_PATH = ROOT / "state" / "site_state.json"
OUTPUT_DIR = ROOT / "output"
REPORT_PATH = OUTPUT_DIR / "report.md"
SUMMARY_PATH = OUTPUT_DIR / "summary.json"

ARTICLE_HINTS = (
    "article",
    "blog",
    "case-study",
    "case-studies",
    "content",
    "guide",
    "insight",
    "insights",
    "news",
    "press",
    "release",
    "releases",
    "research",
    "resource",
    "resources",
    "webinar",
    "whitepaper",
)
PRODUCT_HINTS = (
    "ai-agent",
    "capabilities",
    "feature",
    "features",
    "launch",
    "platform",
    "pricing",
    "product",
    "products",
    "service",
    "services",
    "solution",
    "solutions",
    "update",
    "updates",
)
EXCLUDE_HINTS = (
    ".css",
    ".jpg",
    ".jpeg",
    ".js",
    ".pdf",
    ".png",
    ".svg",
    ".xml",
    "cdn-cgi",
    "cookie",
    "legal",
    "login",
    "mailto:",
    "privacy",
    "tel:",
    "terms",
    "wp-content",
)


@dataclass
class PageSnapshot:
    url: str
    page_type: str
    title: str
    description: str
    headline: str
    signal_hash: str
    text_hash: str
    text_sample: str
    fetched_at: str
    status_code: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "page_type": self.page_type,
            "title": self.title,
            "description": self.description,
            "headline": self.headline,
            "signal_hash": self.signal_hash,
            "text_hash": self.text_hash,
            "text_sample": self.text_sample,
            "fetched_at": self.fetched_at,
            "status_code": self.status_code,
        }


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def make_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
        }
    )
    return session


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    normalized = parsed._replace(
        scheme=(parsed.scheme or "https").lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        params="",
        query="",
        fragment="",
    )
    return urlunparse(normalized)


def hostname(url: str) -> str:
    return urlparse(url).netloc.lower()


def is_same_domain(base_url: str, candidate_url: str) -> bool:
    return hostname(base_url) == hostname(candidate_url)


def should_skip_url(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in EXCLUDE_HINTS)


def score_url(url: str) -> tuple[int, str]:
    lowered = url.lower()
    if any(hint in lowered for hint in PRODUCT_HINTS):
        return (3, "product")
    if any(hint in lowered for hint in ARTICLE_HINTS):
        return (2, "article")
    path = urlparse(url).path.strip("/")
    slash_count = path.count("/")
    if slash_count >= 1 and path:
        return (1, "general")
    return (0, "general")


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def fetch_html(session: requests.Session, url: str, timeout_seconds: int) -> tuple[str | None, int, str | None]:
    try:
        response = session.get(url, timeout=timeout_seconds, allow_redirects=True)
    except requests.RequestException as exc:
        return None, 0, str(exc)
    content_type = response.headers.get("content-type", "")
    if response.status_code >= 400 or "html" not in content_type.lower():
        return None, response.status_code, f"Non-HTML or error response: {content_type}"
    return response.text, response.status_code, None


def extract_links(base_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href:
            continue
        absolute = canonicalize_url(urljoin(base_url, href))
        if not absolute.startswith("http"):
            continue
        if not is_same_domain(base_url, absolute):
            continue
        if should_skip_url(absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
    return links


def snapshot_page(url: str, html: str, status_code: int, page_type: str) -> PageSnapshot:
    soup = BeautifulSoup(html, "html.parser")
    title = normalize_text(soup.title.string if soup.title and soup.title.string else "")
    description_tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    description = normalize_text(description_tag.get("content", "") if description_tag else "")
    headline_tag = soup.find("h1") or soup.find("h2")
    headline = normalize_text(headline_tag.get_text(" ", strip=True) if headline_tag else "")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    body_text = normalize_text(soup.get_text(" ", strip=True))
    text_sample = body_text[:300]
    signal_text = " | ".join(part for part in (title, description, headline) if part)
    signal_hash = hashlib.sha256(signal_text.encode("utf-8", errors="ignore")).hexdigest()
    text_hash = hashlib.sha256(body_text.encode("utf-8", errors="ignore")).hexdigest()
    return PageSnapshot(
        url=url,
        page_type=page_type,
        title=title,
        description=description,
        headline=headline,
        signal_hash=signal_hash,
        text_hash=text_hash,
        text_sample=text_sample,
        fetched_at=now_iso(),
        status_code=status_code,
    )


def prioritize_urls(seed_urls: list[str], discovered_urls: list[str], max_pages: int) -> list[tuple[str, str]]:
    chosen: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url in seed_urls:
        normalized = canonicalize_url(url)
        if normalized not in seen:
            seen.add(normalized)
            chosen.append((normalized, "seed"))
    ranked: list[tuple[int, str, str]] = []
    for url in discovered_urls:
        if url in seen:
            continue
        score, page_type = score_url(url)
        ranked.append((score, url, page_type))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    for score, url, page_type in ranked:
        if len(chosen) >= max_pages:
            break
        if score <= 0:
            continue
        seen.add(url)
        chosen.append((url, page_type))
    return chosen


def monitor_site(
    session: requests.Session,
    site: dict[str, Any],
    global_config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    timeout_seconds = global_config["request_timeout_seconds"]
    max_seed_urls = global_config["max_seed_urls_per_site"]
    max_discovered = global_config["max_discovered_links_per_site"]
    max_pages = global_config["max_pages_to_fetch_per_site"]
    homepage = canonicalize_url(site["homepage"])
    seed_urls = [canonicalize_url(url) for url in site.get("seed_urls", [])[:max_seed_urls]]
    if homepage not in seed_urls:
        seed_urls.insert(0, homepage)

    discovered_urls: list[str] = []
    fetch_errors: list[dict[str, str]] = []
    fetched_seed_count = 0

    for seed_url in seed_urls:
        html, status_code, error = fetch_html(session, seed_url, timeout_seconds)
        if error:
            fetch_errors.append({"url": seed_url, "error": error, "status_code": status_code})
            continue
        fetched_seed_count += 1
        for candidate in extract_links(seed_url, html):
            if candidate not in discovered_urls:
                discovered_urls.append(candidate)
            if len(discovered_urls) >= max_discovered:
                break
        if len(discovered_urls) >= max_discovered:
            break

    chosen_urls = prioritize_urls(seed_urls, discovered_urls, max_pages)
    snapshots: dict[str, dict[str, Any]] = {}
    fetch_log: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()

    for url, page_type in chosen_urls:
        html, status_code, error = fetch_html(session, url, timeout_seconds)
        if error:
            fetch_log.append({"url": url, "status_code": status_code, "error": error})
            continue
        page_kind = page_type if page_type != "seed" else score_url(url)[1]
        snapshot = snapshot_page(url, html, status_code, page_kind)
        if snapshot.text_hash in seen_hashes:
            continue
        seen_hashes.add(snapshot.text_hash)
        snapshots[url] = snapshot.to_dict()
        fetch_log.append({"url": url, "status_code": status_code, "error": None})

    site_state = {
        "name": site["name"],
        "homepage": homepage,
        "checked_at": now_iso(),
        "seed_urls": seed_urls,
        "fetched_seed_count": fetched_seed_count,
        "pages": snapshots,
        "errors": fetch_errors,
        "fetch_log": fetch_log,
    }
    return site_state, fetch_log


def diff_sites(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not previous or not previous.get("pages"):
        return events
    previous_pages = previous.get("pages", {})
    current_pages = current.get("pages", {})

    for url, page in current_pages.items():
        before = previous_pages.get(url)
        if before is None:
            events.append(
                {
                    "event_type": "new_page",
                    "page_type": page.get("page_type", "general"),
                    "url": url,
                    "title": page.get("title") or page.get("headline") or url,
                    "description": page.get("description") or page.get("text_sample", ""),
                }
            )
            continue
        if before.get("signal_hash") != page.get("signal_hash"):
            events.append(
                {
                    "event_type": "updated_page",
                    "page_type": page.get("page_type", "general"),
                    "url": url,
                    "title": page.get("title") or page.get("headline") or url,
                    "description": page.get("description") or page.get("text_sample", ""),
                }
            )
    return events


def render_event(event: dict[str, Any]) -> str:
    page_type = event["page_type"]
    event_type = event["event_type"]
    label = "New page" if event_type == "new_page" else "Updated page"
    description = normalize_text(event.get("description", ""))
    if description:
        description = description[:220]
    return (
        f"- [{event['title']}]({event['url']})\n"
        f"  - Type: {label} ({page_type})\n"
        f"  - URL: {event['url']}\n"
        f"  - Context: {description or 'No summary extracted.'}"
    )


def render_report(
    site_events: dict[str, list[dict[str, Any]]],
    failures: dict[str, list[dict[str, Any]]],
    is_baseline_run: bool,
) -> str:
    run_date = datetime.now(UTC).date().isoformat()
    changed_sites = sum(1 for events in site_events.values() if events)
    total_events = sum(len(events) for events in site_events.values())

    lines = [
        f"# AI Market Watch - {run_date}",
        "",
        "Daily issue for monitored AI market research companies.",
        "",
        f"- Sites with changes: {changed_sites}",
        f"- Total detected events: {total_events}",
        "",
    ]

    if is_baseline_run:
        lines.extend(
            [
                "## Baseline established",
                "",
                "This run created the first committed snapshot set. Future runs will report only deltas against this baseline.",
                "",
            ]
        )
    elif total_events == 0:
        lines.extend(
            [
                "## No material changes detected",
                "",
                "The monitor ran successfully, but it did not find new or materially changed tracked pages today.",
                "",
            ]
        )
    else:
        lines.extend(["## Changes", ""])
        for site_name in sorted(site_events):
            events = site_events[site_name]
            if not events:
                continue
            lines.append(f"### {site_name}")
            lines.append("")
            for event in events:
                lines.append(render_event(event))
            lines.append("")

    if failures:
        lines.extend(["## Fetch failures", ""])
        for site_name in sorted(failures):
            lines.append(f"### {site_name}")
            lines.append("")
            for failure in failures[site_name]:
                lines.append(
                    f"- `{failure.get('url', 'unknown')}`: {failure.get('error', 'unknown error')} "
                    f"(status `{failure.get('status_code', 0)}`)"
                )
            lines.append("")

    lines.extend(
        [
            "## Operating note",
            "",
            "This report is heuristic. Review the linked pages before acting on any change.",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config = load_json(CONFIG_PATH)
    previous_state = load_json(STATE_PATH) if STATE_PATH.exists() else {"generated_at": None, "sites": {}}
    is_baseline_run = not previous_state.get("sites")
    session = make_session(config["global"]["user_agent"])

    current_sites: dict[str, Any] = {}
    site_events: dict[str, list[dict[str, Any]]] = {}
    failures: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for site in config["sites"]:
        site_state, fetch_log = monitor_site(session, site, config["global"])
        current_sites[site["name"]] = site_state
        previous_site = previous_state.get("sites", {}).get(site["name"], {})
        site_events[site["name"]] = diff_sites(previous_site, site_state)
        failures_for_site = site_state.get("errors", []).copy()
        if failures_for_site:
            failures[site["name"]].extend(failures_for_site)

    current_state = {
        "generated_at": now_iso(),
        "sites": current_sites,
    }
    write_json(STATE_PATH, current_state)

    report = render_report(site_events, failures, is_baseline_run)
    REPORT_PATH.write_text(report, encoding="utf-8")

    summary = {
        "generated_at": current_state["generated_at"],
        "changed_sites": sum(1 for events in site_events.values() if events),
        "total_events": sum(len(events) for events in site_events.values()),
        "has_changes": (not is_baseline_run) and any(site_events.values()),
        "is_baseline_run": is_baseline_run,
        "sites_with_changes": {name: len(events) for name, events in site_events.items() if events},
        "sites_with_failures": {name: len(items) for name, items in failures.items() if items},
    }
    write_json(SUMMARY_PATH, summary)
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
