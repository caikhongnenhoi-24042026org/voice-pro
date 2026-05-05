#!/usr/bin/env python3
"""
Web Crawler Tool - Crawl websites and save HTML pages only.

Usage:
    python crawl_web.py <url> [options]

Examples:
    python crawl_web.py https://asianresearchcenter.org/
    python crawl_web.py https://asianresearchcenter.org/ -o ./output -d 5 -w 1.0
    python crawl_web.py https://asianresearchcenter.org/ --max-pages 500
"""

import argparse
import hashlib
import logging
import os
import re
import sys
import time
from collections import deque
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def normalize_url(url):
    """Normalize a URL by removing fragments and trailing slashes."""
    parsed = urlparse(url)
    # Remove fragment
    normalized = urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), parsed.params, parsed.query, "")
    )
    return normalized


def is_same_domain(url, base_domain):
    """Check if a URL belongs to the same domain."""
    parsed = urlparse(url)
    return parsed.netloc == base_domain or parsed.netloc == ""


def url_to_filepath(url, output_dir):
    """Convert a URL to a local file path for saving."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")

    if not path:
        path = "index"

    # Add query string hash if present
    if parsed.query:
        query_hash = hashlib.md5(parsed.query.encode()).hexdigest()[:8]
        path = f"{path}_q{query_hash}"

    # Ensure .html extension
    if not path.endswith((".html", ".htm")):
        path = path + ".html"

    # Replace special characters
    path = re.sub(r'[<>:"|?*]', "_", path)

    filepath = os.path.join(output_dir, parsed.netloc, path)
    return filepath


def extract_links(html_content, base_url):
    """Extract all links from HTML content."""
    soup = BeautifulSoup(html_content, "lxml")
    links = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()

        # Skip non-http links
        if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue

        # Resolve relative URLs
        absolute_url = urljoin(base_url, href)

        # Only keep http/https
        if urlparse(absolute_url).scheme in ("http", "https"):
            links.add(normalize_url(absolute_url))

    return links


def should_skip_url(url):
    """Check if a URL should be skipped based on file extension."""
    skip_extensions = {
        ".js", ".css", ".ico", ".txt", ".gif", ".jpg", ".jpeg", ".png",
        ".mp3", ".mp4", ".pdf", ".tgz", ".flv", ".avi", ".mpeg", ".iso",
        ".zip", ".tar", ".gz", ".bz2", ".rar", ".7z", ".exe", ".dmg",
        ".svg", ".webp", ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".xml", ".json", ".rss", ".atom",
    }
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    return any(path_lower.endswith(ext) for ext in skip_extensions)


def crawl(start_url, output_dir, max_depth=10, wait_time=0.5, max_pages=0, timeout=30):
    """
    Crawl a website starting from start_url and save HTML pages.

    Args:
        start_url: The starting URL to crawl.
        output_dir: Directory to save HTML files.
        max_depth: Maximum crawl depth (0 = unlimited).
        wait_time: Wait time between requests in seconds.
        max_pages: Maximum number of pages to crawl (0 = unlimited).
        timeout: Request timeout in seconds.
    """
    parsed_start = urlparse(start_url)
    base_domain = parsed_start.netloc

    if not base_domain:
        logger.error("Invalid URL: %s", start_url)
        sys.exit(1)

    logger.info("Starting crawl of %s", start_url)
    logger.info("Output directory: %s", output_dir)
    logger.info("Max depth: %s", max_depth if max_depth > 0 else "unlimited")
    logger.info("Max pages: %s", max_pages if max_pages > 0 else "unlimited")
    logger.info("Wait time: %.1fs", wait_time)

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    visited = set()
    queue = deque()
    queue.append((normalize_url(start_url), 0))
    saved_count = 0
    error_count = 0

    while queue:
        url, depth = queue.popleft()

        if url in visited:
            continue

        if max_depth > 0 and depth > max_depth:
            continue

        if max_pages > 0 and saved_count >= max_pages:
            logger.info("Reached max pages limit (%d). Stopping.", max_pages)
            break

        visited.add(url)

        if should_skip_url(url):
            continue

        if not is_same_domain(url, base_domain):
            continue

        logger.info("[%d/%s] Depth=%d Crawling: %s",
                    saved_count + 1,
                    str(max_pages) if max_pages > 0 else "∞",
                    depth, url)

        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                logger.debug("Skipping non-HTML: %s (%s)", url, content_type)
                continue

            html_content = response.text

            # Save the HTML file
            filepath = url_to_filepath(url, output_dir)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_content)

            saved_count += 1
            logger.info("  Saved: %s", filepath)

            # Extract and queue new links
            links = extract_links(html_content, url)
            new_links = 0
            for link in links:
                if link not in visited and is_same_domain(link, base_domain):
                    queue.append((link, depth + 1))
                    new_links += 1

            if new_links > 0:
                logger.debug("  Found %d new links", new_links)

        except requests.exceptions.HTTPError as e:
            error_count += 1
            logger.warning("  HTTP Error for %s: %s", url, e)
        except requests.exceptions.ConnectionError as e:
            error_count += 1
            logger.warning("  Connection Error for %s: %s", url, e)
        except requests.exceptions.Timeout:
            error_count += 1
            logger.warning("  Timeout for %s", url)
        except requests.exceptions.RequestException as e:
            error_count += 1
            logger.warning("  Error for %s: %s", url, e)

        # Be polite - wait between requests
        if wait_time > 0:
            time.sleep(wait_time)

    logger.info("=" * 60)
    logger.info("Crawl completed!")
    logger.info("  Pages saved: %d", saved_count)
    logger.info("  Pages visited: %d", len(visited))
    logger.info("  Errors: %d", error_count)
    logger.info("  Output: %s", output_dir)

    return saved_count


def main():
    parser = argparse.ArgumentParser(
        description="Crawl a website and save HTML pages only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s https://asianresearchcenter.org/
  %(prog)s https://asianresearchcenter.org/ -o ./output -d 5
  %(prog)s https://asianresearchcenter.org/ --max-pages 500 -w 1.0
  %(prog)s https://example.com/ -d 3 --timeout 60
        """,
    )
    parser.add_argument("url", help="The starting URL to crawl")
    parser.add_argument(
        "-o", "--output",
        default="./crawled_pages",
        help="Output directory (default: ./crawled_pages)",
    )
    parser.add_argument(
        "-d", "--max-depth",
        type=int,
        default=10,
        help="Maximum crawl depth, 0=unlimited (default: 10)",
    )
    parser.add_argument(
        "-w", "--wait",
        type=float,
        default=0.5,
        help="Wait time between requests in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Maximum number of pages to save, 0=unlimited (default: 0)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    crawl(
        start_url=args.url,
        output_dir=args.output,
        max_depth=args.max_depth,
        wait_time=args.wait,
        max_pages=args.max_pages,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
