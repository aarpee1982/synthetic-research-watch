from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sites.json"
STATE_PATH = ROOT / "state" / "site_state.json"
OUTPUT_DIR = ROOT / "output"
REPORT_PATH = OUTPUT_DIR / "report.md"
SUMMARY_PATH = OUTPUT_DIR / "summary.json"

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.5"

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
    ".gif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".js",
    ".pdf",
    ".png",
    ".svg",
    ".webp",
    ".xml",
    "cdn-cgi",
    "cookie",
    "legal",
    "login",
    "logout",
    "mailto:",
    "privacy",
    "signup",
    "sign-up",
    "tel:",
    "terms",
    "wp-content",
)
DEFAULT_GEO_KEYWORDS = (
    "ai overview",
    "ai overviews",
    "ai mode",
    "ai search",
    "answer engine optimization",
    "answer engines",
    "chatgpt search",
    "citation",
    "citations",
    "generative engine optimization",
    "geo",
    "google ai",
    "llm visibility",
    "perplexity",
    "search generative experience",
    "search visibility",
)


@dataclass
class PageSnapshot:
    url: str
    page_type: str
    title: str
    description: str
    headline: str
    summary: str
    signal_hash: str
    text_hash: str
    fetched_at: str
    status_code: int
    relevance_score: int = 0
    relevance_reason: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    ai_relevance_checked: bool = False
    ai_relevant: bool | None = None
    body_sample: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "page_type": self.page_type,
            "title": self.title,
            "description": self.description,
            "headline": self.headline,
            "summary": self.summary,
            "signal_hash": self.signal_hash,
            "text_hash": self.text_hash,
            "fetched_at": self.fetched_at,
            "status_code": self.status_code,
            "relevance_score": self.relevance_score,
            "relevance_reason": self.relevance_reason,
            "matched_keywords": self.matched_keywords,
            "ai_relevance_checked": self.ai_relevance_checked,
            "ai_relevant": self.ai_relevant,
        }


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


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


def should_skip_url(url: str, site: dict[str, Any] | None = None) -> bool:
    lowered = url.lower()
    hints = list(EXCLUDE_HINTS)
    if site:
        hints.extend(site.get("exclude_path_keywords", []))
    return any(str(hint).lower() in lowered for hint in hints)


def score_url(url: str, site: dict[str, Any] | None = None) -> tuple[int, str]:
    lowered = url.lower()
    include_hints = [str(hint).lower() for hint in (site or {}).get("include_path_keywords", [])]
    if include_hints and any(hint in lowered for hint in include_hints):
        return (4, "geo")
    if any(hint in lowered for hint in PRODUCT_HINTS):
        return (3, "product")
    if any(hint in lowered for hint in ARTICLE_HINTS):
        return (2, "article")
    path = urlparse(url).path.strip("/")
    if path and path.count("/") >= 1:
        return (1, "general")
    return (0, "general")


def normalize_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")


def fetch_document(
    session: requests.Session,
    url: str,
    timeout_seconds: int,
    allow_xml: bool = False,
) -> tuple[str | None, int, str | None]:
    try:
        response = session.get(url, timeout=timeout_seconds, allow_redirects=True)
    except requests.RequestException as exc:
        return None, 0, str(exc)
    content_type = response.headers.get("content-type", "")
    if response.status_code >= 400:
        return None, response.status_code, f"HTTP error response: {content_type}"
    lowered_type = content_type.lower()
    if "html" not in lowered_type and not (allow_xml and "xml" in lowered_type):
        return None, response.status_code, f"Non-HTML response: {content_type}"
    encoding = response.encoding
    if "charset=" not in lowered_type and response.apparent_encoding:
        encoding = response.apparent_encoding
    return response.content.decode(encoding or "utf-8", errors="replace"), response.status_code, None


def fetch_html(session: requests.Session, url: str, timeout_seconds: int) -> tuple[str | None, int, str | None]:
    return fetch_document(session, url, timeout_seconds, allow_xml=False)


def extract_links(base_url: str, html: str, site: dict[str, Any] | None = None) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    seen: set[str] = set()
    candidates: list[str] = []
    candidates.extend(anchor.get("href", "").strip() for anchor in soup.find_all("a", href=True))
    candidates.extend(match.strip() for match in re.findall(r"<link[^>]*>(.*?)</link>", html, flags=re.I | re.S))
    for link_tag in soup.find_all("link"):
        if link_tag.get("href"):
            candidates.append(link_tag.get("href", "").strip())
        elif link_tag.string:
            candidates.append(link_tag.string.strip())

    for href in candidates:
        if not href:
            continue
        absolute = canonicalize_url(urljoin(base_url, href))
        if not absolute.startswith("http"):
            continue
        if not is_same_domain(base_url, absolute):
            continue
        if base_url.lower().endswith(".xml") and urlparse(absolute).path.rstrip("/") in {"", "/news", "/blog"}:
            continue
        if should_skip_url(absolute, site):
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
    body_sample = body_text[:900]
    summary = normalize_text(description or headline or body_sample[:260])
    signal_text = " | ".join(part for part in (title, description, headline) if part)
    signal_hash = hashlib.sha256(signal_text.encode("utf-8", errors="ignore")).hexdigest()
    text_hash = hashlib.sha256(body_text.encode("utf-8", errors="ignore")).hexdigest()
    return PageSnapshot(
        url=url,
        page_type=page_type,
        title=title,
        description=description,
        headline=headline,
        summary=summary,
        signal_hash=signal_hash,
        text_hash=text_hash,
        body_sample=body_sample,
        fetched_at=now_iso(),
        status_code=status_code,
    )


def prioritize_urls(seed_urls: list[str], discovered_urls: list[str], max_pages: int, site: dict[str, Any]) -> list[tuple[str, str]]:
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
        score, page_type = score_url(url, site)
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


def keyword_relevance(snapshot: PageSnapshot, keywords: list[str]) -> tuple[int, list[str]]:
    high_weight_text = " ".join([snapshot.url, snapshot.title, snapshot.description, snapshot.headline]).lower()
    matched: list[str] = []
    score = 0
    for keyword in keywords:
        keyword_l = keyword.lower()
        if keyword_l in high_weight_text:
            score += 2
            matched.append(keyword)
    return score, sorted(set(matched), key=str.lower)


def extract_response_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text") or content.get("output_text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks)


def classify_with_openai(snapshot: PageSnapshot, topic: str, model: str, api_key: str, timeout_seconds: int) -> dict[str, Any] | None:
    prompt = {
        "topic": topic,
        "url": snapshot.url,
        "title": snapshot.title,
        "description": snapshot.description,
        "headline": snapshot.headline,
        "sample": snapshot.body_sample[:700],
        "instruction": (
            "Return JSON only. Mark relevant true only if this page is about generative engine "
            "optimization, AI search, answer engines, AI Overviews, LLM visibility, search citations, "
            "or tools/platform changes that affect how brands are found in AI answers."
        ),
    }
    response = requests.post(
        OPENAI_RESPONSES_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": "You are a strict GEO news relevance classifier. Do not invent facts.",
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "geo_relevance",
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "relevant": {"type": "boolean"},
                            "reason": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["relevant", "reason", "confidence"],
                    },
                    "strict": True,
                }
            },
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    text = extract_response_text(response.json())
    return json.loads(text)


def apply_relevance_filter(
    snapshot: PageSnapshot,
    config: dict[str, Any],
    ai_budget: dict[str, int],
) -> bool:
    global_config = config["global"]
    if not global_config.get("require_geo_relevance", True):
        snapshot.relevance_reason = "GEO relevance filter disabled."
        return True

    keywords = list(config.get("geo_keywords") or DEFAULT_GEO_KEYWORDS)
    score, matched_keywords = keyword_relevance(snapshot, keywords)
    snapshot.relevance_score = score
    snapshot.matched_keywords = matched_keywords
    min_score = int(global_config.get("min_geo_keyword_score", 2))
    if score >= min_score:
        snapshot.relevance_reason = f"Matched GEO keywords: {', '.join(matched_keywords[:6])}"
        return True

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or ai_budget["remaining"] <= 0:
        snapshot.relevance_reason = "Skipped: no GEO keyword match."
        return False

    ai_budget["remaining"] -= 1
    snapshot.ai_relevance_checked = True
    try:
        result = classify_with_openai(
            snapshot=snapshot,
            topic=str(global_config.get("topic_name", "generative engine optimization")),
            model=os.getenv("OPENAI_MODEL", "").strip() or str(global_config.get("openai_model", DEFAULT_OPENAI_MODEL)),
            api_key=api_key,
            timeout_seconds=int(global_config.get("openai_timeout_seconds", 30)),
        )
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        snapshot.ai_relevant = None
        snapshot.relevance_reason = f"Skipped: AI relevance check failed safely ({type(exc).__name__})."
        return False

    snapshot.ai_relevant = bool(result and result.get("relevant"))
    snapshot.relevance_reason = normalize_text(str((result or {}).get("reason", "")))[:240]
    return snapshot.ai_relevant


def monitor_site(
    session: requests.Session,
    site: dict[str, Any],
    config: dict[str, Any],
    ai_budget: dict[str, int],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    global_config = config["global"]
    timeout_seconds = int(global_config["request_timeout_seconds"])
    max_seed_urls = int(global_config["max_seed_urls_per_site"])
    max_discovered = int(global_config["max_discovered_links_per_site"])
    max_pages = int(global_config["max_pages_to_fetch_per_site"])
    homepage = canonicalize_url(site["homepage"])
    seed_urls = [canonicalize_url(url) for url in site.get("seed_urls", [])[:max_seed_urls]]
    if homepage not in seed_urls:
        seed_urls.insert(0, homepage)

    discovered_urls: list[str] = []
    fetch_errors: list[dict[str, str | int]] = []
    fetched_seed_count = 0

    for seed_url in seed_urls:
        html, status_code, error = fetch_document(session, seed_url, timeout_seconds, allow_xml=True)
        if error:
            fetch_errors.append({"url": seed_url, "error": error, "status_code": status_code})
            continue
        fetched_seed_count += 1
        for candidate in extract_links(seed_url, html, site):
            if candidate not in discovered_urls:
                discovered_urls.append(candidate)
            if len(discovered_urls) >= max_discovered:
                break
        if len(discovered_urls) >= max_discovered:
            break

    chosen_urls = prioritize_urls(seed_urls, discovered_urls, max_pages, site)
    snapshots: dict[str, dict[str, Any]] = {}
    fetch_log: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    skipped_non_geo = 0

    for url, page_type in chosen_urls:
        html, status_code, error = fetch_html(session, url, timeout_seconds)
        if error:
            fetch_log.append({"url": url, "status_code": status_code, "error": error})
            continue
        page_kind = page_type if page_type != "seed" else score_url(url, site)[1]
        snapshot = snapshot_page(url, html, status_code, page_kind)
        if snapshot.text_hash in seen_hashes:
            continue
        seen_hashes.add(snapshot.text_hash)
        if not apply_relevance_filter(snapshot, config, ai_budget):
            skipped_non_geo += 1
            fetch_log.append(
                {
                    "url": url,
                    "status_code": status_code,
                    "error": None,
                    "kept": False,
                    "reason": snapshot.relevance_reason,
                }
            )
            continue
        snapshots[url] = snapshot.to_dict()
        fetch_log.append({"url": url, "status_code": status_code, "error": None, "kept": True})

    site_state = {
        "name": site["name"],
        "category": site.get("category", "unknown"),
        "homepage": homepage,
        "checked_at": now_iso(),
        "seed_urls": seed_urls,
        "fetched_seed_count": fetched_seed_count,
        "pages": snapshots,
        "skipped_non_geo": skipped_non_geo,
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
                    "change_type": "new",
                    "page_type": page.get("page_type", "general"),
                    "url": url,
                    "title": page.get("title") or page.get("headline") or url,
                    "summary": page.get("summary") or page.get("description") or "",
                    "relevance_reason": page.get("relevance_reason", ""),
                }
            )
            continue
        signal_changed = before.get("signal_hash") != page.get("signal_hash")
        content_changed = before.get("text_hash") != page.get("text_hash")
        if signal_changed or content_changed:
            events.append(
                {
                    "event_type": "updated_page",
                    "change_type": "signal" if signal_changed else "content",
                    "page_type": page.get("page_type", "general"),
                    "url": url,
                    "title": page.get("title") or page.get("headline") or url,
                    "summary": page.get("summary") or page.get("description") or "",
                    "relevance_reason": page.get("relevance_reason", ""),
                }
            )
    return events


def render_event(event: dict[str, Any]) -> str:
    page_type = event["page_type"]
    event_type = event["event_type"]
    change_type = event.get("change_type", "unknown")
    label = "New page" if event_type == "new_page" else f"Updated page ({change_type})"
    summary = normalize_text(event.get("summary", ""))[:240]
    relevance = normalize_text(event.get("relevance_reason", ""))[:180]
    lines = [
        f"- [{event['title']}]({event['url']})",
        f"  - Type: {label}; page class: {page_type}",
        f"  - URL: {event['url']}",
        f"  - Context: {summary or 'No summary extracted.'}",
    ]
    if relevance:
        lines.append(f"  - GEO relevance: {relevance}")
    return "\n".join(lines)


def render_report(
    site_events: dict[str, list[dict[str, Any]]],
    failures: dict[str, list[dict[str, Any]]],
    skipped_non_geo: dict[str, int],
    is_baseline_run: bool,
    include_filtered_out_details: bool = False,
) -> str:
    run_date = datetime.now(UTC).date().isoformat()
    changed_sites = sum(1 for events in site_events.values() if events)
    total_events = sum(len(events) for events in site_events.values())
    total_skipped = sum(skipped_non_geo.values())

    lines = [
        f"# GEO News Watch - {run_date}",
        "",
        "Daily issue for generative engine optimization and AI search monitoring.",
        "",
        f"- Sites with changes: {changed_sites}",
        f"- Total detected events: {total_events}",
        f"- Non-GEO pages skipped: {total_skipped}",
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
                "## No GEO-relevant changes detected",
                "",
                "The monitor ran successfully, but it did not find new or materially changed GEO pages today.",
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

    skipped_lines = {name: count for name, count in skipped_non_geo.items() if count}
    if include_filtered_out_details and skipped_lines:
        lines.extend(["## Filtered out", ""])
        for site_name, count in sorted(skipped_lines.items()):
            lines.append(f"- {site_name}: skipped {count} page(s) that did not match GEO relevance.")
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
            "This report is heuristic. Review linked pages before acting on any change.",
        ]
    )
    return "\n".join(lines)


def run_monitor(config_path: Path, state_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.md"
    summary_path = output_dir / "summary.json"
    load_dotenv()
    config = load_json(config_path)
    previous_state = load_json(state_path) if state_path.exists() else {"generated_at": None, "sites": {}}
    is_baseline_run = not previous_state.get("sites")
    session = make_session(config["global"]["user_agent"])
    ai_budget = {"remaining": int(config["global"].get("max_ai_classifications_per_run", 30))}

    current_sites: dict[str, Any] = {}
    site_events: dict[str, list[dict[str, Any]]] = {}
    failures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped_non_geo: dict[str, int] = {}

    for site in config["sites"]:
        if not site.get("geo_relevance", True) and config["global"].get("require_geo_relevance", True):
            continue
        site_state, _fetch_log = monitor_site(session, site, config, ai_budget)
        current_sites[site["name"]] = site_state
        previous_site = previous_state.get("sites", {}).get(site["name"], {})
        site_events[site["name"]] = diff_sites(previous_site, site_state)
        skipped_non_geo[site["name"]] = int(site_state.get("skipped_non_geo", 0))
        failures_for_site = site_state.get("errors", []).copy()
        if failures_for_site:
            failures[site["name"]].extend(failures_for_site)

    current_state = {
        "generated_at": now_iso(),
        "topic": config["global"].get("topic_name", "generative engine optimization"),
        "sites": current_sites,
    }
    write_json(state_path, current_state)

    report = render_report(
        site_events,
        failures,
        skipped_non_geo,
        is_baseline_run,
        bool(config["global"].get("include_filtered_out_details", False)),
    )
    report_path.write_text(report, encoding="utf-8")

    summary = {
        "generated_at": current_state["generated_at"],
        "changed_sites": sum(1 for events in site_events.values() if events),
        "total_events": sum(len(events) for events in site_events.values()),
        "has_changes": (not is_baseline_run) and any(site_events.values()),
        "is_baseline_run": is_baseline_run,
        "sites_with_changes": {name: len(events) for name, events in site_events.items() if events},
        "sites_with_failures": {name: len(items) for name, items in failures.items() if items},
        "skipped_non_geo_pages": sum(skipped_non_geo.values()),
        "ai_classifications_remaining": ai_budget["remaining"],
    }
    write_json(summary_path, summary)
    print(f"Wrote {report_path}")
    print(f"Wrote {summary_path}")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor GEO and AI-search news sources.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_monitor(args.config, args.state, args.output_dir)


if __name__ == "__main__":
    main()
