#!/usr/bin/env python3
"""
Web Crawler Tool - Crawl websites, save HTML pages, and download linked files.

Commands:
    crawl   - Recursively crawl a website and save HTML pages
    download - Parse crawled HTML files, find download links, and download files
               with titles as filenames

Usage:
    python crawl_web.py crawl <url> [options]
    python crawl_web.py download <html_dir> [options]

Examples:
    # Step 1: Crawl website
    python crawl_web.py crawl https://asianresearchcenter.org/ -o ./crawled

    # Step 2: Download files from crawled pages (named by page title)
    python crawl_web.py download ./crawled -o ./downloads

    # One-shot: crawl + download in one command
    python crawl_web.py crawl https://asianresearchcenter.org/ -o ./crawled --download ./downloads
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

DOWNLOAD_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".epub", ".mobi",
}


def normalize_url(url):
    """Normalize a URL by removing fragments and trailing slashes."""
    parsed = urlparse(url)
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

    if parsed.query:
        query_hash = hashlib.md5(parsed.query.encode()).hexdigest()[:8]
        path = f"{path}_q{query_hash}"

    if not path.endswith((".html", ".htm")):
        path = path + ".html"

    path = re.sub(r'[<>:"|?*]', "_", path)
    filepath = os.path.join(output_dir, parsed.netloc, path)
    return filepath


def sanitize_filename(name):
    """Sanitize a string for use as a filename."""
    # Replace problematic characters with underscore
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    # Replace multiple spaces/underscores with single
    name = re.sub(r"[_\s]+", " ", name).strip()
    # Limit length
    if len(name) > 200:
        name = name[:200]
    return name


def extract_links(html_content, base_url):
    """Extract all links from HTML content."""
    soup = BeautifulSoup(html_content, "lxml")
    links = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()

        if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue

        absolute_url = urljoin(base_url, href)

        if urlparse(absolute_url).scheme in ("http", "https"):
            links.add(normalize_url(absolute_url))

    return links


def extract_download_links(html_content, base_url):
    """Extract download links from HTML content.

    Looks for links pointing to downloadable file types or containing
    '/download/' in the URL path.
    """
    soup = BeautifulSoup(html_content, "lxml")
    downloads = []

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href:
            continue

        absolute_url = urljoin(base_url, href)
        parsed = urlparse(absolute_url)
        path_lower = parsed.path.lower()

        is_download = (
            any(path_lower.endswith(ext) for ext in DOWNLOAD_EXTENSIONS)
            or "/download/" in path_lower
        )

        if is_download and parsed.scheme in ("http", "https"):
            downloads.append(absolute_url)

    return downloads


def extract_title(html_content):
    """Extract the page title from HTML content."""
    soup = BeautifulSoup(html_content, "lxml")

    # Try <h1> first (usually more specific)
    h1 = soup.find("h1")
    if h1 and h1.text.strip():
        return h1.text.strip()

    # Fall back to <title> tag
    title = soup.find("title")
    if title and title.text.strip():
        return title.text.strip()

    return None


def get_file_extension(url, response=None):
    """Get the file extension from URL or response headers."""
    parsed = urlparse(url)
    path = parsed.path.lower()

    for ext in DOWNLOAD_EXTENSIONS:
        if path.endswith(ext):
            return ext

    if response:
        content_type = response.headers.get("Content-Type", "").lower()
        type_map = {
            "application/pdf": ".pdf",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/zip": ".zip",
            "application/epub+zip": ".epub",
        }
        for mime, ext in type_map.items():
            if mime in content_type:
                return ext

    return ".pdf"


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


# ---------------------------------------------------------------------------
# Crawl command
# ---------------------------------------------------------------------------

def crawl(start_url, output_dir, max_depth=10, wait_time=0.5, max_pages=0, timeout=30):
    """Crawl a website starting from start_url and save HTML pages."""
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

            filepath = url_to_filepath(url, output_dir)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_content)

            saved_count += 1
            logger.info("  Saved: %s", filepath)

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

        if wait_time > 0:
            time.sleep(wait_time)

    logger.info("=" * 60)
    logger.info("Crawl completed!")
    logger.info("  Pages saved: %d", saved_count)
    logger.info("  Pages visited: %d", len(visited))
    logger.info("  Errors: %d", error_count)
    logger.info("  Output: %s", output_dir)

    return saved_count


# ---------------------------------------------------------------------------
# Download command
# ---------------------------------------------------------------------------

def download_from_crawled(html_dir, download_dir, wait_time=0.5, timeout=60):
    """Parse crawled HTML files, find download links, and download files named by page title."""
    html_files = []
    for root, _dirs, files in os.walk(html_dir):
        for fname in files:
            if fname.endswith((".html", ".htm")):
                html_files.append(os.path.join(root, fname))

    if not html_files:
        logger.error("No HTML files found in %s", html_dir)
        return 0

    logger.info("Found %d HTML files in %s", len(html_files), html_dir)
    logger.info("Download directory: %s", download_dir)
    os.makedirs(download_dir, exist_ok=True)

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    downloaded_count = 0
    skipped_count = 0
    error_count = 0
    seen_urls = set()

    for html_file in sorted(html_files):
        with open(html_file, "r", encoding="utf-8") as f:
            html_content = f.read()

        title = extract_title(html_content)
        download_links = extract_download_links(html_content, "https://placeholder.invalid/")

        # Re-extract with proper base URL from the HTML file
        soup = BeautifulSoup(html_content, "lxml")
        canonical = soup.find("link", rel="canonical")
        base_url = canonical["href"] if canonical and canonical.get("href") else ""
        if not base_url:
            og_url = soup.find("meta", property="og:url")
            base_url = og_url["content"] if og_url and og_url.get("content") else ""

        if base_url:
            download_links = extract_download_links(html_content, base_url)

        if not download_links:
            continue

        for dl_url in download_links:
            if dl_url in seen_urls:
                continue
            seen_urls.add(dl_url)

            ext = get_file_extension(dl_url)

            if title:
                filename = sanitize_filename(title) + ext
            else:
                # Fall back to URL-based name
                filename = os.path.basename(urlparse(dl_url).path)
                if not filename:
                    filename = hashlib.md5(dl_url.encode()).hexdigest()[:12] + ext

            filepath = os.path.join(download_dir, filename)

            if os.path.exists(filepath):
                logger.info("  Exists, skipping: %s", filename)
                skipped_count += 1
                continue

            logger.info("Downloading: %s", dl_url)
            logger.info("  -> %s", filename)

            try:
                response = session.get(dl_url, timeout=timeout, stream=True, allow_redirects=True)
                response.raise_for_status()

                # Skip if the response is HTML (not an actual file download)
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" in content_type:
                    logger.debug("  Skipping (HTML response): %s", dl_url)
                    continue

                # Update extension from actual response if needed
                actual_ext = get_file_extension(dl_url, response)
                if actual_ext != ext:
                    filename = sanitize_filename(title) + actual_ext if title else filename
                    filepath = os.path.join(download_dir, filename)

                with open(filepath, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                file_size = os.path.getsize(filepath)
                logger.info("  Saved: %s (%.1f KB)", filename, file_size / 1024)
                downloaded_count += 1

            except requests.exceptions.RequestException as e:
                error_count += 1
                logger.warning("  Error downloading %s: %s", dl_url, e)

            if wait_time > 0:
                time.sleep(wait_time)

    logger.info("=" * 60)
    logger.info("Download completed!")
    logger.info("  Files downloaded: %d", downloaded_count)
    logger.info("  Files skipped (already exist): %d", skipped_count)
    logger.info("  Errors: %d", error_count)
    logger.info("  Output: %s", download_dir)

    return downloaded_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Crawl websites and download linked files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Crawl a website (HTML only)
  %(prog)s crawl https://asianresearchcenter.org/
  %(prog)s crawl https://asianresearchcenter.org/ -o ./crawled -d 5

  # Crawl and auto-download files
  %(prog)s crawl https://asianresearchcenter.org/ -o ./crawled --download ./downloads

  # Download files from previously crawled HTML pages
  %(prog)s download ./crawled -o ./downloads
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # --- crawl subcommand ---
    crawl_parser = subparsers.add_parser("crawl", help="Crawl a website and save HTML pages")
    crawl_parser.add_argument("url", help="The starting URL to crawl")
    crawl_parser.add_argument(
        "-o", "--output", default="./crawled_pages",
        help="Output directory for HTML files (default: ./crawled_pages)",
    )
    crawl_parser.add_argument(
        "-d", "--max-depth", type=int, default=10,
        help="Maximum crawl depth, 0=unlimited (default: 10)",
    )
    crawl_parser.add_argument(
        "-w", "--wait", type=float, default=0.5,
        help="Wait time between requests in seconds (default: 0.5)",
    )
    crawl_parser.add_argument(
        "--max-pages", type=int, default=0,
        help="Maximum number of pages to save, 0=unlimited (default: 0)",
    )
    crawl_parser.add_argument(
        "--timeout", type=int, default=30,
        help="Request timeout in seconds (default: 30)",
    )
    crawl_parser.add_argument(
        "--download", metavar="DOWNLOAD_DIR", default=None,
        help="After crawling, also download files to this directory",
    )
    crawl_parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose/debug logging",
    )

    # --- download subcommand ---
    dl_parser = subparsers.add_parser(
        "download", help="Download files from crawled HTML pages",
    )
    dl_parser.add_argument("html_dir", help="Directory containing crawled HTML files")
    dl_parser.add_argument(
        "-o", "--output", default="./downloads",
        help="Output directory for downloaded files (default: ./downloads)",
    )
    dl_parser.add_argument(
        "-w", "--wait", type=float, default=0.5,
        help="Wait time between downloads in seconds (default: 0.5)",
    )
    dl_parser.add_argument(
        "--timeout", type=int, default=60,
        help="Download timeout in seconds (default: 60)",
    )
    dl_parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose/debug logging",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.command == "crawl":
        crawl(
            start_url=args.url,
            output_dir=args.output,
            max_depth=args.max_depth,
            wait_time=args.wait,
            max_pages=args.max_pages,
            timeout=args.timeout,
        )
        if args.download:
            logger.info("")
            logger.info("Starting download phase...")
            download_from_crawled(
                html_dir=args.output,
                download_dir=args.download,
                wait_time=args.wait,
                timeout=args.timeout,
            )

    elif args.command == "download":
        download_from_crawled(
            html_dir=args.html_dir,
            download_dir=args.output,
            wait_time=args.wait,
            timeout=args.timeout,
        )


if __name__ == "__main__":
    main()
