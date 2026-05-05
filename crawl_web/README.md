# crawl-web

Crawl any website, download linked files (PDF, DOCX...), rename by page title, and zip results.

## Install

```bash
# From this repo
pip install .

# Or install directly (no clone needed)
pip install git+https://github.com/caikhongnenhoi-24042026org/voice-pro.git
```

## Usage

After installing, use the `crawl-web` command from anywhere:

```bash
# Crawl a website (HTML only)
crawl-web crawl https://example.com/ -o ./crawled --max-pages 100

# Download files from crawled HTML pages (renamed by page title)
crawl-web download ./crawled -o ./downloads --zip

# All-in-one: crawl + download + zip
crawl-web all https://example.com/ -o ./output --max-pages 100
```

### Without installing (run directly)

```bash
python -m crawl_web all https://example.com/ -o ./output --max-pages 100
```

## Commands

### `crawl` - Crawl website and save HTML pages

```bash
crawl-web crawl <url> [options]

Options:
  -o, --output DIR      Output directory (default: ./crawled_pages)
  -d, --max-depth N     Maximum crawl depth, 0=unlimited (default: 10)
  --max-pages N         Maximum pages to save, 0=unlimited (default: 0)
  -w, --wait SECONDS    Wait between requests (default: 0.5)
  --download DIR        Also download files to this directory
  --zip                 Create zip archive of output
  -v, --verbose         Debug logging
```

### `download` - Download files from crawled HTML

```bash
crawl-web download <html_dir> [options]

Options:
  -o, --output DIR      Output directory (default: ./downloads)
  -w, --wait SECONDS    Wait between downloads (default: 0.5)
  --zip                 Create zip archive of output
```

### `all` - Crawl + Download + Zip

```bash
crawl-web all <url> [options]

Options:
  -o, --output DIR      Base output directory (default: ./output)
  -d, --max-depth N     Maximum crawl depth (default: 10)
  --max-pages N         Maximum pages to save (default: 0)
  -w, --wait SECONDS    Wait between requests (default: 0.5)
  --zip                 Create zip archive (auto-enabled in 'all' mode)
```

## Output Structure

```
./output/
├── example_com_html/          # Crawled HTML pages
├── example_com_html.zip       # Zipped HTML
├── example_com_downloads/     # Downloaded files (PDF, DOCX...)
└── example_com_downloads.zip  # Zipped downloads
```

## Features

- Works with any website (Cloudflare-friendly headers)
- Downloads renamed by page title (from `<h1>` or `<title>`)
- Auto-strips site name suffixes (e.g. `| Site Name`) from filenames
- Content-Type guard: won't save HTML responses as PDF
- Unicode filenames preserved (Vietnamese, CJK, etc.)
- Zip output for easy sharing

## Google Colab

```python
!pip install git+https://github.com/caikhongnenhoi-24042026org/voice-pro.git

!crawl-web all https://asianresearchcenter.org/ -o ./output --max-pages 100

# Download the zip
from google.colab import files
files.download('./output/asianresearchcenter_org_downloads.zip')
```

## Dependencies

- Python >= 3.8
- requests
- beautifulsoup4
- lxml
