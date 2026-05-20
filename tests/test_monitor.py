from __future__ import annotations

import json
from pathlib import Path

from scripts.monitor import (
    PageSnapshot,
    canonicalize_url,
    diff_sites,
    extract_links,
    keyword_relevance,
    snapshot_page,
)


def test_canonicalize_url_removes_query_and_fragment() -> None:
    assert canonicalize_url("HTTPS://Example.com/path/?utm=x#top") == "https://example.com/path"


def test_extract_links_keeps_same_domain_and_skips_assets() -> None:
    html = """
    <a href="/blog/ai-search">AI search</a>
    <a href="https://example.com/file.pdf">PDF</a>
    <a href="https://other.com/blog/ai-search">Other</a>
    """
    assert extract_links("https://example.com/", html) == ["https://example.com/blog/ai-search"]


def test_extract_links_reads_rss_link_tags() -> None:
    xml = """
    <rss><channel>
      <item><title>AI search</title><link>https://example.com/news/chatgpt-search</link></item>
      <item><title>Other</title><link>https://other.com/news/chatgpt-search</link></item>
    </channel></rss>
    """
    assert extract_links("https://example.com/news/rss.xml", xml) == ["https://example.com/news/chatgpt-search"]


def test_keyword_relevance_accepts_geo_page() -> None:
    snapshot = PageSnapshot(
        url="https://example.com/blog/google-ai-overviews",
        page_type="article",
        title="Google AI Overviews change search visibility",
        description="Brands need citations in AI search.",
        headline="AI search update",
        summary="AI search update",
        signal_hash="a",
        text_hash="b",
        fetched_at="2026-01-01T00:00:00+00:00",
        status_code=200,
        body_sample="Generic body",
    )
    score, matches = keyword_relevance(snapshot, ["AI Overviews", "AI search", "citation"])
    assert score >= 4
    assert "AI search" in matches


def test_keyword_relevance_rejects_generic_market_research_page() -> None:
    snapshot = PageSnapshot(
        url="https://example.com/blog/survey-panel-pricing",
        page_type="article",
        title="Survey panel pricing",
        description="Market research operations update.",
        headline="Panel pricing",
        summary="Panel pricing",
        signal_hash="a",
        text_hash="b",
        fetched_at="2026-01-01T00:00:00+00:00",
        status_code=200,
        body_sample="Consumer survey panel operations with no search visibility topic.",
    )
    score, matches = keyword_relevance(snapshot, ["AI search", "generative engine optimization", "AI Overviews"])
    assert score == 0
    assert matches == []


def test_keyword_relevance_rejects_footer_only_matches() -> None:
    snapshot = PageSnapshot(
        url="https://example.com/category/agency-marketing",
        page_type="article",
        title="Agency Marketing",
        description="Marketing tactics for agencies.",
        headline="Agency Marketing",
        summary="Agency Marketing",
        signal_hash="a",
        text_hash="b",
        fetched_at="2026-01-01T00:00:00+00:00",
        status_code=200,
        body_sample="Footer links: AI search, search visibility, generative engine optimization.",
    )
    score, matches = keyword_relevance(snapshot, ["AI search", "search visibility", "generative engine optimization"])
    assert score == 0
    assert matches == []


def test_diff_detects_body_hash_change_even_when_signal_same() -> None:
    previous = {
        "pages": {
            "https://example.com/blog/ai-search": {
                "signal_hash": "same",
                "text_hash": "old",
                "title": "AI search",
            }
        }
    }
    current = {
        "pages": {
            "https://example.com/blog/ai-search": {
                "signal_hash": "same",
                "text_hash": "new",
                "title": "AI search",
                "summary": "Updated body",
            }
        }
    }
    events = diff_sites(previous, current)
    assert events[0]["event_type"] == "updated_page"
    assert events[0]["change_type"] == "content"


def test_snapshot_state_excludes_raw_body_sample() -> None:
    snapshot = snapshot_page(
        "https://example.com/blog/ai-search",
        "<html><head><title>AI search</title></head><body><h1>AI search</h1><p>secret-looking body text</p></body></html>",
        200,
        "article",
    )
    payload = snapshot.to_dict()
    assert "body_sample" not in payload
    assert "text_sample" not in payload


def test_config_sources_marked_geo_relevant() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config" / "sites.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["global"]["require_geo_relevance"] is True
    assert all(site["geo_relevance"] is True for site in config["sites"])
