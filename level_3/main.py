import urllib.request
import time
import socket
import csv
import argparse

from urllib.parse import urlparse
from urllib.error import URLError, HTTPError



"""
12.Validate URLs (scheme must be http/https, no empty lines, duplicate removal) before checking. 

13.Handle a mixed input file gracefully: skip comments (#), blank lines, and malformed entries with per-line warnings. 

14.Add a --retries N option with a short delay between attempts, and report how many attempts each URL needed. 

15.Treat non-2xx status codes as DOWN by default, with a --allow-redirects flag and a documented rule for 3xx codes. 

"""


# 1. READ URLS FROM FILE

def read_urls(filepath):

    try:

        with open(filepath, "r", encoding="utf-8") as file:
            return file.readlines()

    except FileNotFoundError:

        print(f"Input file not found: {filepath}")
        return []


# 2. VALIDATE URLS

def validate_urls(urls):

    valid_urls = []
    seen_urls = set()

    for line_number, url in enumerate(urls, start=1):

        url = url.strip()

        # Skip blank lines and comments
        if not url or url.startswith("#"):
            continue

        parsed_url = urlparse(url)

        # Validate scheme
        if parsed_url.scheme not in ("http", "https"):

            print(
                f"Line {line_number}: "
                f"Invalid scheme: {url}"
            )
            continue

        # Validate hostname
        if not parsed_url.hostname:

            print(
                f"Line {line_number}: "
                f"Invalid URL: {url}"
            )
            continue

        # Validate port
        try:
            parsed_url.port

        except ValueError:

            print(
                f"Line {line_number}: "
                f"Invalid port: {url}"
            )
            continue

        # Remove duplicates
        if url in seen_urls:

            print(
                f"Line {line_number}: "
                f"Duplicate URL: {url}"
            )
            continue

        seen_urls.add(url)
        valid_urls.append(url)

    return valid_urls


# --------------------------------------------------
# 3. CHECK ONE WEBSITE
# --------------------------------------------------

def check_website(url, timeout, allow_redirects):

    result = {
        "url": url,
        "status": "DOWN",
        "status_code": None,
        "response_time_ms": 0,
        "attempts": 0,
        "redirect_target": None,
        "error_type": None
    }

    start_time = time.perf_counter()

    try:

        with urllib.request.urlopen(
            url,
            timeout=timeout
        ) as response:

            result["status_code"] = response.getcode()

            final_url = response.geturl()

            # Detect redirects
            if final_url != url:

                result["redirect_target"] = final_url

            # Check HTTP response
            if 200 <= result["status_code"] < 300:

                if result["redirect_target"] and not allow_redirects:

                    result["error_type"] = "RedirectNotAllowed"

                else:

                    result["status"] = "UP"

            else:

                result["error_type"] = "HTTPError"

    except HTTPError as error:

        result["status_code"] = error.code
        result["error_type"] = "HTTPError"

    except socket.timeout:

        result["error_type"] = "Timeout"

    except URLError as error:

        reason = error.reason

        if isinstance(reason, socket.timeout):

            result["error_type"] = "Timeout"

        else:

            result["error_type"] = "URLError"

    # Measure response time
    result["response_time_ms"] = round(
        (time.perf_counter() - start_time) * 1000,
        2
    )

    return result


# --------------------------------------------------
# 4. RETRY FAILED CHECKS
# --------------------------------------------------

def monitor_website(url, timeout, retries, allow_redirects):

    for attempt in range(retries + 1):

        result = check_website(
            url,
            timeout,
            allow_redirects
        )

        result["attempts"] = attempt + 1

        # Stop if website is UP
        if result["status"] == "UP":
            break

        # Identify temporary failures
        retryable_errors = (
            "Timeout",
            "URLError"
        )

        retryable = (
            result["error_type"] in retryable_errors
            or (
                result["error_type"] == "HTTPError"
                and result["status_code"] is not None
                and 500 <= result["status_code"] < 600
            )
        )

        # Stop if error is not retryable
        if not retryable or attempt == retries:
            break

        # Wait before retrying
        time.sleep(2)

    return result


# 5. CHECK ALL WEBSITES

def check_websites(urls, timeout, retries, allow_redirects):

    results = []

    for url in urls:

        result = monitor_website(
            url,
            timeout,
            retries,
            allow_redirects
        )

        results.append(result)

    return results


# --------------------------------------------------
# 6. DISPLAY RESULTS
# --------------------------------------------------

def print_results(results):

    print("\nWEBSITE STATUS RESULTS")
    print("-" * 100)

    for result in results:

        status_code = (
            result["status_code"]
            if result["status_code"] is not None
            else result["error_type"]
        )

        print(
            f"{result['url']:<40} "
            f"{result['status']:<6} "
            f"{str(status_code):<20} "
            f"{result['response_time_ms']} ms "
            f"Attempts: {result['attempts']}"
        )

        if result["redirect_target"]:

            print(
                f"  Redirected to: "
                f"{result['redirect_target']}"
            )

    print("-" * 100)


# --------------------------------------------------
# 7. DISPLAY SUMMARY
# --------------------------------------------------

def print_summary(results):

    total = len(results)

    up = sum(
        result["status"] == "UP"
        for result in results
    )

    down = total - up

    print(f"\nSummary: {up} UP, {down} DOWN")


# --------------------------------------------------
# 8. SAVE CSV REPORT
# --------------------------------------------------

def save_report(results, filepath):

    if not results:
        return

    with open(
        filepath,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=results[0].keys()
        )

        writer.writeheader()
        writer.writerows(results)

    print(f"\nReport written to {filepath}")


# --------------------------------------------------
# 9. COMMAND-LINE ARGUMENTS
# --------------------------------------------------

def parse_arguments():

    parser = argparse.ArgumentParser(
        description="Website Status Checker - Level 3"
    )

    parser.add_argument(
        "--input",
        help="Path to the file containing URLs"
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Request timeout in seconds"
    )

    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of additional attempts"
    )

    parser.add_argument(
        "--allow-redirects",
        action="store_true",
        help="Accept redirected websites if final response is 2xx"
    )

    parser.add_argument(
        "--output",
        default="status_report.csv",
        help="CSV report output file"
    )

    parser.add_argument(
        "urls",
        nargs="*",
        help="URLs supplied directly"
    )

    args = parser.parse_args()

    if args.input and args.urls:
        parser.error("Use either --input or positional URLs.")

    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero.")

    if args.retries < 0:
        parser.error("--retries cannot be negative.")

    return args


# --------------------------------------------------
# 10. MAIN FUNCTION
# --------------------------------------------------

def main():

    args = parse_arguments()

    # Read URLs
    if args.urls:

        urls = args.urls

    else:

        filepath = args.input or "websites.txt"
        urls = read_urls(filepath)

    # Validate URLs
    urls = validate_urls(urls)

    if not urls:

        print("No valid URLs found.")
        return

    print(
        f"\nChecking {len(urls)} websites "
        f"(timeout {args.timeout}s)..."
    )

    # Check websites
    results = check_websites(
        urls,
        args.timeout,
        args.retries,
        args.allow_redirects
    )

    # Display results
    print_results(results)

    # Display summary
    print_summary(results)

    # Save report
    save_report(
        results,
        args.output
    )


if __name__ == "__main__":
    main()