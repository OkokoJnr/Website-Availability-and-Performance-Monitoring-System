import urllib.request
import time
import socket
import csv
import argparse

from urllib.error import URLError, HTTPError



""" TASK
8.Measure response time with time.perf_counter() and display it per URL. 


9.Set a timeout (via urlopen(..., timeout=N)) and catch URLError, HTTPError, and socket.timeout separately 
with clear messages. 

10.Detect and report redirects (final URL differs from the requested URL). 

11.Print a summary count of up/down and write a CSV report. 

"""

#LEVEL CHALLENGE

"""
- INVALID URLS NOT HANDLE

"""

# 1. READ URLS FROM FILE

def read_urls(filepath):

    try:
        with open(filepath, "r", encoding="utf-8") as file:
            return file.readlines()

    except FileNotFoundError:
        print(f"Input file not found: {filepath}")
        return []


# 2. CHECK WEBSITE STATUS

def check_website(url, timeout):

    result = {
        "url": url,
        "status": "DOWN",
        "status_code": None,
        "response_time_ms": 0,
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

            # Detect redirects
            final_url = response.geturl()

            if final_url != url:
                result["redirect_target"] = final_url

            # Check HTTP status
            if 200 <= result["status_code"] < 300:
                result["status"] = "UP"

            else:
                result["error_type"] = "HTTPError"

    except HTTPError as error:

        result["status_code"] = error.code
        result["error_type"] = "HTTPError"

    except socket.timeout:

        result["error_type"] = "Timeout"

    except URLError as error:

        if isinstance(error.reason, socket.timeout):
            result["error_type"] = "Timeout"

        else:
            result["error_type"] = "URLError"

    # Calculate response time
    result["response_time_ms"] = round(
        (time.perf_counter() - start_time) * 1000,
        2
    )

    return result


# 3. CHECK ALL WEBSITES

def check_websites(urls, timeout):

    results = []

    for url in urls:

        url = url.strip()

        if not url:
            continue

        result = check_website(url, timeout)

        results.append(result)

    return results


# 4. DISPLAY RESULTS

def print_results(results):

    print("\nWEBSITE STATUS RESULTS")
    print("-" * 90)

    for result in results:

        status_code = (
            result["status_code"]
            if result["status_code"] is not None
            else result["error_type"]
        )

        print(
            f"{result['url']:<40} "
            f"{result['status']:<6} "
            f"{str(status_code):<15} "
            f"{result['response_time_ms']} ms"
        )

        if result["redirect_target"]:

            print(
                f"  Redirected to: "
                f"{result['redirect_target']}"
            )

    print("-" * 90)


# 5. DISPLAY SUMMARY

def print_summary(results):

    total = len(results)

    up = sum(
        result["status"] == "UP"
        for result in results
    )

    down = total - up

    print(f"\nSummary: {up} UP, {down} DOWN")


# 6. SAVE CSV REPORT

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


# 7. COMMAND-LINE ARGUMENTS

def parse_arguments():

    parser = argparse.ArgumentParser(
        description="Website Status Checker - Level 2"
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

    return args


# 8. MAIN FUNCTION

def main():

    args = parse_arguments()

    # Read URLs
    if args.urls:
        urls = args.urls

    else:
        filepath = args.input or "websites.txt"
        urls = read_urls(filepath)

    if not urls:
        print("No URLs provided.")
        return

    print(
        f"\nChecking {len(urls)} websites "
        f"(timeout {args.timeout}s)..."
    )

    # Check websites
    results = check_websites(
        urls,
        args.timeout
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