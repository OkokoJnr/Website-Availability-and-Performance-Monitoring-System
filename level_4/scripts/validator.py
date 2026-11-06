
from urllib.parse import urlparse, urljoin
from urllib.error import URLError, HTTPError


def validate_urls(urls):

    valid_urls = []
    seen_urls = set()

    for line_number, url in enumerate(urls, start=1):

        url = url.strip()

        # Skip blank lines and comments
        if not url or url.startswith("#"):
            continue

        # Add HTTPS if no scheme is provided
        if "://" not in url:
            url = "https://" + url

        parsed_url = urlparse(url)

        # Validate scheme
        if parsed_url.scheme not in ("http", "https"):
            print(f"Line {line_number}: Invalid scheme: {url}")
            continue

        # Validate hostname
        if not parsed_url.hostname:
            print(f"Line {line_number}: Invalid URL: {url}")
            continue

        # Validate port
        try:
            parsed_url.port

        except ValueError:
            print(f"Line {line_number}: Invalid port: {url}")
            continue

        # Remove duplicates
        if url in seen_urls:
            print(f"Line {line_number}: Duplicate URL: {url}")
            continue

        seen_urls.add(url)
        valid_urls.append(url)

    return valid_urls

