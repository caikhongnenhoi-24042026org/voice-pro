#!/usr/bin/env python3
"""
crawl-web - Crawl any website, save HTML pages, download linked files,
and zip the results for easy sharing.

Install:
    pip install crawl-web

Usage:
    crawl-web crawl <url> [options]
    crawl-web download <html_dir> [options]
    crawl-web all <url> [options]

Examples:
    crawl-web crawl https://example.com/ -o ./crawled
    crawl-web download ./crawled -o ./downloads --zip
    crawl-web all https://example.com/ -o ./output --max-pages 100
"""

__version__ = "1.0.0"

import argparse
import hashlib
import logging
import os
import re
import shutil
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


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

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
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"[_\s]+", " ", name).strip()
    if len(name) > 200:
        name = name[:200]
    return name


def clean_title(raw_title, site_name=None):
    """Clean page title by removing common site name suffixes.

    Handles patterns like:
        "Article Title | Site Name"
        "Article Title - Site Name"
        "Article Title — Site Name"
    """
    if not raw_title:
        return raw_title

    # Common separator patterns between title and site name
    separators = [" | ", " - ", " — ", " – ", " :: "]

    for sep in separators:
        if sep in raw_title:
            parts = raw_title.split(sep)
            # The site name is usually the last part
            if len(parts) >= 2:
                candidate = parts[-1].strip()
                # If a site_name is given, check if it matches
                if site_name and site_name.lower() in candidate.lower():
                    return sep.join(parts[:-1]).strip()
                # Otherwise, if the last part is short (likely a site name),
                # remove it only if we have a site_name hint
                if site_name:
                    return sep.join(parts[:-1]).strip()

    # If site_name provided, try removing it directly
    if site_name:
        # Remove trailing site name patterns
        for sep in separators:
            pattern = re.escape(sep) + re.escape(site_name)
            raw_title = re.sub(pattern + r"\s*$", "", raw_title, flags=re.IGNORECASE)

    return raw_title.strip()


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

    h1 = soup.find("h1")
    if h1 and h1.text.strip():
        return h1.text.strip()

    title = soup.find("title")
    if title and title.text.strip():
        return title.text.strip()

    return None


def extract_site_name(html_content):
    """Try to extract the site name from HTML meta tags."""
    soup = BeautifulSoup(html_content, "lxml")

    og_site = soup.find("meta", property="og:site_name")
    if og_site and og_site.get("content"):
        return og_site["content"].strip()

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


def create_zip(source_dir, zip_name=None):
    """Create a zip archive from a directory.

    Args:
        source_dir: Directory to zip.
        zip_name: Output zip filename (without .zip extension).
                  Defaults to the directory name.

    Returns:
        Path to the created zip file.
    """
    if not os.path.exists(source_dir) or not os.listdir(source_dir):
        logger.warning("Directory is empty or does not exist: %s", source_dir)
        return None

    if not zip_name:
        zip_name = os.path.basename(os.path.normpath(source_dir))

    # Place zip file next to the source directory
    parent_dir = os.path.dirname(os.path.abspath(source_dir))
    zip_base = os.path.join(parent_dir, zip_name)

    logger.info("Creating zip archive: %s.zip", zip_base)
    zip_path = shutil.make_archive(zip_base, "zip", source_dir)

    zip_size = os.path.getsize(zip_path)
    logger.info("Zip created: %s (%.1f MB)", zip_path, zip_size / (1024 * 1024))

    return zip_path


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

    # Try to detect site name from the first HTML file for title cleaning
    site_name = None
    for html_file in html_files:
        with open(html_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        site_name = extract_site_name(content)
        if site_name:
            logger.info("Detected site name: %s", site_name)
            break

    for html_file in sorted(html_files):
        with open(html_file, "r", encoding="utf-8", errors="ignore") as f:
            html_content = f.read()

        title = extract_title(html_content)
        if title:
            title = clean_title(title, site_name)

        # Extract base URL from the HTML file
        soup = BeautifulSoup(html_content, "lxml")
        canonical = soup.find("link", rel="canonical")
        base_url = canonical["href"] if canonical and canonical.get("href") else ""
        if not base_url:
            og_url = soup.find("meta", property="og:url")
            base_url = og_url["content"] if og_url and og_url.get("content") else ""

        if not base_url:
            base_url = "https://placeholder.invalid/"

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
        description="Crawl any website, download linked files, and zip results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Crawl a website (HTML only)
  %(prog)s crawl https://example.com/
  %(prog)s crawl https://example.com/ -o ./crawled -d 5 --max-pages 100

  # Crawl and auto-download files, then zip
  %(prog)s crawl https://example.com/ -o ./crawled --download ./downloads --zip

  # Download files from previously crawled HTML pages
  %(prog)s download ./crawled -o ./downloads --zip

  # All-in-one: crawl + download + zip
  %(prog)s all https://example.com/ -o ./output
  %(prog)s all https://asianresearchcenter.org/ -o ./output --max-pages 100
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # --- Common arguments ---
    def add_common_args(p):
        p.add_argument(
            "-w", "--wait", type=float, default=0.5,
            help="Wait time between requests in seconds (default: 0.5)",
        )
        p.add_argument(
            "--timeout", type=int, default=30,
            help="Request timeout in seconds (default: 30)",
        )
        p.add_argument(
            "--zip", action="store_true",
            help="Create a zip archive of the output",
        )
        p.add_argument(
            "-v", "--verbose", action="store_true",
            help="Enable verbose/debug logging",
        )

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
        "--max-pages", type=int, default=0,
        help="Maximum number of pages to save, 0=unlimited (default: 0)",
    )
    crawl_parser.add_argument(
        "--download", metavar="DOWNLOAD_DIR", default=None,
        help="After crawling, also download files to this directory",
    )
    add_common_args(crawl_parser)

    # --- download subcommand ---
    dl_parser = subparsers.add_parser(
        "download", help="Download files from crawled HTML pages",
    )
    dl_parser.add_argument("html_dir", help="Directory containing crawled HTML files")
    dl_parser.add_argument(
        "-o", "--output", default="./downloads",
        help="Output directory for downloaded files (default: ./downloads)",
    )
    add_common_args(dl_parser)

    # --- all subcommand (crawl + download + zip) ---
    all_parser = subparsers.add_parser(
        "all", help="Crawl + download + zip in one command",
    )
    all_parser.add_argument("url", help="The starting URL to crawl")
    all_parser.add_argument(
        "-o", "--output", default="./output",
        help="Base output directory (default: ./output)",
    )
    all_parser.add_argument(
        "-d", "--max-depth", type=int, default=10,
        help="Maximum crawl depth, 0=unlimited (default: 10)",
    )
    all_parser.add_argument(
        "--max-pages", type=int, default=0,
        help="Maximum number of pages to save, 0=unlimited (default: 0)",
    )
    add_common_args(all_parser)

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

        download_dir = args.download
        if download_dir:
            logger.info("")
            logger.info("Starting download phase...")
            download_from_crawled(
                html_dir=args.output,
                download_dir=download_dir,
                wait_time=args.wait,
                timeout=args.timeout,
            )

        if args.zip:
            logger.info("")
            create_zip(args.output, "crawled_pages")
            if download_dir:
                create_zip(download_dir, "downloads")

    elif args.command == "download":
        download_from_crawled(
            html_dir=args.html_dir,
            download_dir=args.output,
            wait_time=args.wait,
            timeout=args.timeout,
        )
        if args.zip:
            logger.info("")
            create_zip(args.output, "downloads")

    elif args.command == "all":
        # Derive subdirectories from base output
        domain = urlparse(args.url).netloc.replace(".", "_")
        html_dir = os.path.join(args.output, f"{domain}_html")
        download_dir = os.path.join(args.output, f"{domain}_downloads")

        crawl(
            start_url=args.url,
            output_dir=html_dir,
            max_depth=args.max_depth,
            wait_time=args.wait,
            max_pages=args.max_pages,
            timeout=args.timeout,
        )

        logger.info("")
        logger.info("Starting download phase...")
        dl_count = download_from_crawled(
            html_dir=html_dir,
            download_dir=download_dir,
            wait_time=args.wait,
            timeout=args.timeout,
        )

        # Always zip in 'all' mode (unless --zip explicitly set to false via no flag)
        logger.info("")
        create_zip(html_dir, f"{domain}_html")
        if dl_count > 0:
            create_zip(download_dir, f"{domain}_downloads")

        logger.info("")
        logger.info("All done! Output directory: %s", args.output)


if __name__ == "__main__":
    main()
