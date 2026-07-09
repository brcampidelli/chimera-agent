"""Web scraping + secure structured extraction — the agent-native answer to Firecrawl/crawl4ai.

Phase 1: ``scrape`` (fetch any page → clean Markdown + metadata, http→browser→Firecrawl cascade) and
``extract`` (schema → validated JSON via the quarantined reader, injection-safe). Crawl/map come next.
"""

from chimera.scrape.crawl import CrawledPage, CrawlResult, crawl_site, map_site
from chimera.scrape.extract import ExtractResult, extract_structured
from chimera.scrape.fetch import FetchResult, fetch_page

__all__ = [
    "CrawlResult",
    "CrawledPage",
    "ExtractResult",
    "FetchResult",
    "crawl_site",
    "extract_structured",
    "fetch_page",
    "map_site",
]
