import urllib.request
import time
import socket
import ssl
import http.client
import csv
import json
import argparse
import os

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, urljoin
from urllib.error import URLError, HTTPError


"""

16.Run checks concurrently using concurrent.futures.ThreadPoolExecutor (standard library) with a configurable worker count, 
    while preserving per-URL results and a stable output order. 
    

17.Add a --watch mode that re-checks every N seconds and prints a running table with changes since the 
    last check (UP→DOWN, DOWN→UP), plus a simple state history in memory. 
    

18.Write a detailed CSV/JSON report including: URL, status, response time, attempts, redirect target, error type (if any), 
    and timestamp; add a --history file that appends results across runs. 


19.Compute and report simple uptime statistics from the history file: % uptime per URL, average response time, 
slowest URL, and most frequent failure reason. 

    
20.Handle tricky cases: URLs that hang until timeout, servers that close the connection early, TLS errors (ssl.SSLError), 
    and huge redirect chains (cap at N hops and report "too many redirects"). 
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


# --------------------------------------------------
# 3. CHECK ONE WEBSITE

# Limit automatic redirects to 5 hops.
urllib.request.HTTPRedirectHandler.max_redirections = 5
urllib.request.HTTPRedirectHandler.max_repeats = 5


def check_website(url, timeout, allow_redirects):

    result = {
        "timestamp": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
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

        with urllib.request.urlopen(url, timeout=timeout) as response:

            result["status_code"] = response.getcode()

            final_url = response.geturl()

            # Detect redirects
            if final_url != url:
                result["redirect_target"] = final_url
                if allow_redirects:
                    result["status_code"] = 300

            # Only 2xx responses are successful.
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

        # Capture redirect destination if available
        location = error.headers.get("Location")

        if location:
            result["redirect_target"] = urljoin(url, location)

        # Identify excessive redirects
        if "redirect" in str(error.reason).lower():
            result["error_type"] = "TooManyRedirects"

    except socket.timeout:

        result["error_type"] = "Timeout"

    except ssl.SSLError:

        result["error_type"] = "SSLError"

    except http.client.IncompleteRead:

        result["error_type"] = "IncompleteRead"

    except URLError as error:

        reason = error.reason

        if isinstance(reason, socket.timeout):
            result["error_type"] = "Timeout"

        elif isinstance(reason, ssl.SSLError):
            result["error_type"] = "SSLError"

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

        # Retry only temporary failures
        retryable_errors = (
            "Timeout",
            "URLError",
            "IncompleteRead"
        )

        retryable = (
            result["error_type"] in retryable_errors
            or (
                result["error_type"] == "HTTPError"
                and result["status_code"] is not None
                and 500 <= result["status_code"] < 600
            )
        )

        if not retryable or attempt == retries:
            break

        time.sleep(2)

    return result


# --------------------------------------------------
# 5. CHECK WEBSITES CONCURRENTLY
# --------------------------------------------------

def check_websites(urls, timeout, retries, allow_redirects, workers):

    with ThreadPoolExecutor(max_workers=workers) as executor:

        results = list(
            executor.map(
                lambda url: monitor_website(
                    url,
                    timeout,
                    retries,
                    allow_redirects
                ),
                urls
            )
        )

    return results


# --------------------------------------------------
# 6. DISPLAY RESULTS AND SUMMARY
# --------------------------------------------------

def print_results(results):

    print("\nWEBSITE CHECK RESULTS")
    print("-" * 90)

    for result in results:

        print(
            f"{result['url']:<40} "
            f"{result['status']:<6} "
            f"{str(result['status_code'] or result['error_type']):<20} "
            f"{result['response_time_ms']} ms "
            f"Attempts: {result['attempts']}"
        )

        if result["redirect_target"]:
            print(
                f"  Redirected to: {result['redirect_target']}"
            )

    print("-" * 90)


def print_summary(results):

    total = len(results)

    up = sum(
        result["status"] == "UP"
        for result in results
    )

    down = total - up

    print(f"\nSummary: {up} UP, {down} DOWN")


# --------------------------------------------------
# 7. TRACK STATUS CHANGES
# --------------------------------------------------

def track_changes(results, previous_status, state_history):

    print("\nSTATUS CHANGES")
    print("-" * 60)

    changes_found = False

    for result in results:

        url = result["url"]
        current_status = result["status"]

        # Maintain state history
        state_history.setdefault(url, []).append(current_status)

        # Compare previous and current status
        old_status = previous_status.get(url)

        if old_status and old_status != current_status:

            print(f"{url}: {old_status} -> {current_status}")
            changes_found = True

        previous_status[url] = current_status

    if not changes_found:
        print("No status changes detected.")

    print("-" * 60)


# --------------------------------------------------
# 8. SAVE CSV AND JSON REPORTS
# --------------------------------------------------

def save_reports(results, csv_filepath, json_filepath):

    if not results:
        return

    os.makedirs(os.path.dirname(csv_filepath) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(json_filepath) or ".", exist_ok=True)

    # CSV report
    with open(csv_filepath, "w", newline="", encoding="utf-8") as file:

        writer = csv.DictWriter(
            file,
            fieldnames=results[0].keys()
        )

        writer.writeheader()
        writer.writerows(results)

    # JSON report
    with open(json_filepath, "w", encoding="utf-8") as file:

        json.dump(results, file, indent=4)


# --------------------------------------------------
# 9. APPEND RESULTS TO HISTORY FILE
# --------------------------------------------------

def append_history(results, filepath):

    if not results:
        return

    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

    file_exists = (
        os.path.exists(filepath)
        and os.path.getsize(filepath) > 0
    )

    with open(filepath, "a", newline="", encoding="utf-8") as file:

        writer = csv.DictWriter(
            file,
            fieldnames=results[0].keys()
        )

        if not file_exists:
            writer.writeheader()

        writer.writerows(results)


# --------------------------------------------------
# 10. CALCULATE UPTIME STATISTICS
# --------------------------------------------------

def uptime_statistics(filepath):

    if not os.path.exists(filepath):
        return

    website_history = {}
    failure_reasons = {}

    with open(filepath, "r", newline="", encoding="utf-8") as file:

        reader = csv.DictReader(file)

        for row in reader:

            url = row["url"]

            website_history.setdefault(
                url,
                {
                    "total_checks": 0,
                    "successful_checks": 0,
                    "response_times": []
                }
            )

            data = website_history[url]

            data["total_checks"] += 1

            if row["status"] == "UP":
                data["successful_checks"] += 1

            data["response_times"].append(
                float(row["response_time_ms"])
            )

            if row["status"] == "DOWN" and row["error_type"]:

                reason = row["error_type"]

                failure_reasons[reason] = (
                    failure_reasons.get(reason, 0) + 1
                )

    if not website_history:
        return

    print("\nHISTORICAL UPTIME STATISTICS")
    print("-" * 60)

    average_times = {}

    for url, data in website_history.items():

        uptime = (
            data["successful_checks"] / data["total_checks"]
        ) * 100

        average_time = (
            sum(data["response_times"])
            / len(data["response_times"])
        )

        average_times[url] = average_time

        print(f"\nWebsite: {url}")
        print(f"Uptime: {uptime:.2f}%")
        print(f"Average response time: {average_time:.2f} ms")

    # Slowest website
    if average_times:

        slowest = max(
            average_times,
            key=average_times.get
        )

        print("\nSlowest website:")
        print(f"{slowest}: {average_times[slowest]:.2f} ms")

    # Most frequent failure
    if failure_reasons:

        frequent_failure = max(
            failure_reasons,
            key=failure_reasons.get
        )

        print("\nMost frequent failure reason:")
        print(
            f"{frequent_failure}: "
            f"{failure_reasons[frequent_failure]} occurrences"
        )


# --------------------------------------------------
# 11. DISPLAY STATE HISTORY
# --------------------------------------------------

def print_state_history(state_history):

    print("\nWEBSITE STATE HISTORY")
    print("-" * 60)

    for url, states in state_history.items():

        print(f"\n{url}")
        print(" -> ".join(states))


# --------------------------------------------------
# 12. COMMAND-LINE ARGUMENTS
# --------------------------------------------------

def parse_arguments():

    parser = argparse.ArgumentParser(
        description="Website Status Checker"
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
        "--workers",
        type=int,
        default=5,
        help="Number of concurrent workers"
    )

    parser.add_argument(
        "--allow-redirects",
        action="store_true",
        help="Accept redirected websites if the final response is 2xx"
    )

    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously monitor websites"
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Seconds between monitoring cycles"
    )

    parser.add_argument(
        "--csv-output",
        default="./reports/results.csv"
    )

    parser.add_argument(
        "--json-output",
        default="./reports/results.json"
    )

    parser.add_argument(
        "--history",
        default="./reports/history.csv"
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

    if args.workers <= 0:
        parser.error("--workers must be greater than zero.")

    if args.interval <= 0:
        parser.error("--interval must be greater than zero.")

    return args


# --------------------------------------------------
# 13. MAIN FUNCTION
# --------------------------------------------------

def main():

    args = parse_arguments()

    # Read URLs
    if args.urls:
        urls = args.urls

    else:
        filepath = args.input or "sites.txt"
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

    previous_status = {}
    state_history = {}

    cycle = 1

    try:

        while True:

            print(f"\nMONITORING CYCLE: {cycle}")
            print("=" * 60)

            # Check websites
            results = check_websites(
                urls,
                args.timeout,
                args.retries,
                args.allow_redirects,
                args.workers
            )

            # Display results
            print_results(results)
            print_summary(results)

            # Track changes
            if args.watch:
                track_changes(
                    results,
                    previous_status,
                    state_history
                )

            # Save reports
            save_reports(
                results,
                args.csv_output,
                args.json_output
            )

            append_history(
                results,
                args.history
            )

            # Calculate historical statistics
            uptime_statistics(args.history)

            cycle += 1

            if not args.watch:
                break

            print(
                f"\nNext check in {args.interval} seconds..."
            )

            time.sleep(args.interval)

    except KeyboardInterrupt:

        print("\nMonitoring stopped.")

    finally:

        if state_history:
            print_state_history(state_history)

        print("\nMonitoring completed.")


# PROGRAM ENTRY POINT

if __name__ == "__main__":
    main()