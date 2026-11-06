#!/usr/bin/env python3
"""
WEBSITE STATUS CHECKER  -  Levels 1 to 4
Python, procedural (no classes defined), standard library only.

STATUS CODE POLICY  (requirement 15 - the documented rule)
----------------------------------------------------------
  2xx                      -> UP.
  3xx  (default)           -> DOWN. Redirects are NOT followed. The 3xx code is
                              reported, the Location header is shown as the
                              redirect target, and error_type is HTTPStatus_<code>.
  3xx  with --allow-redirects
                           -> Redirects 301, 302, 303, 307 and 308 ARE followed,
                              up to --max-redirects hops (default 5). The FINAL
                              status then decides: 2xx = UP (marked "redirected"),
                              anything else = DOWN with that final code.
                              More hops than the cap = DOWN, "too many redirects".
                              A bad or non-http(s) Location = DOWN, "BadRedirect".
  Other 3xx (300, 304, 305...) and 3xx without a Location header are never
                              followed: DOWN with HTTPStatus_<code>.
  4xx / 5xx                -> DOWN with HTTPStatus_<code>.
  No HTTP answer at all    -> DOWN with a network error label (see below).

NETWORK ERROR LABELS
  DNS error, TimeoutError, SSLError, ConnectionClosedEarly, URLError_<type>,
  Exception_<type>, ThreadException_<type>.
  Retried (when --retries > 1): DNS error, TimeoutError, ConnectionClosedEarly,
  URLError_<type>. Never retried: HTTP statuses, SSLError, redirect problems.

REQUIREMENTS MAP
  1-3, 5      parse_and_validate_urls(), main(), format_result_line()
  4, 11, 18   save_reports()
  6-7         fetch_once(), check_single_url()
  8-10        check_single_url(), classify_network_error(), fetch_once()
  12-13       validate_url(), parse_and_validate_urls()
  14          check_single_url() retry loop, "attempts" in every result
  15          the policy above, fetch_once(), check_single_url()
  16          run_concurrent_checks()
  17          execute_run(), print_watch_table(), print_state_history()
  19          compute_uptime_statistics()
  20          fetch_once() (hop cap), check_single_url() (hangs, early close, TLS)
"""

import argparse
from collections import defaultdict
import concurrent.futures
import csv
import http.client
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HEADERS = {"User-Agent": "PythonStatusChecker/2.0"}

# Only these 3xx codes are real "go to another address" answers.
REDIRECT_CODES = (301, 302, 303, 307, 308)

# Labels that mean "worth trying again". Everything else fails immediately.
RETRYABLE_ERRORS = ("DNS error", "TimeoutError", "ConnectionClosedEarly")

# Labels produced by redirect handling (they keep the last HTTP status code).
REDIRECT_PROBLEMS = ("too many redirects", "BadRedirect")

DNS_MARKERS = (
    "getaddrinfo failed",
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname provided",
)

# Column order of the CSV / history files. Keep it stable: history is append-only.
CSV_FIELDS = ["url", "status", "status_code", "response_time", "attempts",
              "redirect_target", "redirected", "error_type", "timestamp"]


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# One-time HTTP client setup
# ---------------------------------------------------------------------------
def build_no_redirect_opener():
    """
    urllib normally follows redirects silently, which would hide every 3xx
    status code from us. We reuse the standard redirect handler but replace its
    redirect_request method with one that returns None ("do not follow"). urlopen
    then raises HTTPError for a 3xx answer, so we can apply our OWN policy.
    (This configures an existing object; it defines no class.)
    """
    handler = urllib.request.HTTPRedirectHandler()
    handler.redirect_request = lambda *args, **kwargs: None
    return urllib.request.build_opener(handler)


# Done once at import time, before any worker thread exists, so it is thread-safe.
urllib.request.install_opener(build_no_redirect_opener())


# ===========================================================================
# LEVEL 3: URL validation (requirements 12 and 13)
# ===========================================================================
def validate_url(url):
    """Return None when the URL is acceptable, otherwise the reason it is not."""
    if not url:
        return "empty value"
    if any(ch.isspace() for ch in url):
        return "contains whitespace"
    try:
        parsed = urllib.parse.urlparse(url)
        _ = parsed.port            # raises ValueError for a bad port number
    except ValueError:
        return "invalid port"
    if parsed.scheme not in ("http", "https"):
        return f"unsupported scheme '{parsed.scheme or 'none'}' (only http/https)"
    if not parsed.hostname:
        return "missing host name"
    return None


def parse_and_validate_urls(file_path=None, url_args=None):
    """
    Collect URLs from a file (one per line) and/or the command line, then:
      - skip blank lines and # comment lines silently,
      - warn (per line) about malformed entries and skip them,
      - warn about duplicates and skip them.
    Returns the clean, de-duplicated list in the order the URLs were given.
    """
    raw_urls = []   # (url text, where it came from)

    if file_path:
        if not os.path.isfile(file_path):
            print(f"[-] Error: Target file '{file_path}' not found.", file=sys.stderr)
            sys.exit(1)
        try:
            # utf-8-sig quietly removes the invisible BOM that Windows Notepad adds.
            with open(file_path, "r", encoding="utf-8-sig") as f:
                for line_num, line in enumerate(f, 1):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    raw_urls.append((stripped, f"line {line_num}"))
        except (OSError, UnicodeDecodeError) as exc:
            print(f"[-] Error: could not read '{file_path}': {exc}", file=sys.stderr)
            sys.exit(1)

    if url_args:
        for u in url_args:
            raw_urls.append((u.strip(), "command line"))

    valid_urls = []
    seen = set()   # a set answers "have I seen this?" very quickly
    for url, source in raw_urls:
        reason = validate_url(url)
        if reason:
            print(f"[!] Warning [{source}]: skipped '{url}' - {reason}", file=sys.stderr)
            continue
        if url in seen:
            print(f"[!] Warning [{source}]: duplicate ignored -> '{url}'", file=sys.stderr)
            continue
        seen.add(url)
        valid_urls.append(url)
    return valid_urls


# ===========================================================================
# LEVELS 1-3: probing one URL (requirements 6-10, 14, 15, 20)
# ===========================================================================
def classify_network_error(exc):
    """Turn a network exception into one short, readable label."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    text = str(reason).lower()

    if isinstance(reason, socket.gaierror) or any(m in text for m in DNS_MARKERS):
        return "DNS error"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "TimeoutError"
    if isinstance(reason, ssl.SSLError):
        return "SSLError"
    if "timed out" in text:
        return "TimeoutError"
    if isinstance(reason, (ConnectionError, http.client.HTTPException)):
        return "ConnectionClosedEarly"
    return f"URLError_{type(reason).__name__}"


def is_retryable(error_type):
    return error_type in RETRYABLE_ERRORS or error_type.startswith("URLError_")


def fetch_once(url, timeout, allow_redirects, max_redirects):
    """
    ONE attempt at a URL, applying the STATUS CODE POLICY.
    Returns a dictionary:
        status_code      the last HTTP status received
        redirect_target  the address a redirect pointed to (or None)
        problem          "too many redirects" / "BadRedirect" / None
    Network failures (DNS, timeout, TLS, early close) are NOT handled here:
    they propagate to check_single_url(), which classifies and retries them.
    """
    current_url = url
    hops = 0
    redirect_target = None

    while True:
        request = urllib.request.Request(current_url, headers=HEADERS)
        try:
            # timeout=N applies to connecting and to each wait for data (req. 9).
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return {"status_code": response.getcode(),
                        "redirect_target": redirect_target, "problem": None}
        except urllib.error.HTTPError as http_err:
            # The server DID answer, just not with 2xx (or it is a 3xx because
            # we disabled automatic redirects). Read what we need, then move on.
            status_code = http_err.code
            location = http_err.headers.get("Location") if http_err.headers else None
            http_err.close()

        outcome = {"status_code": status_code,
                   "redirect_target": redirect_target, "problem": None}

        # 4xx, 5xx and the 3xx codes that are not real redirects end here.
        if status_code not in REDIRECT_CODES or not location:
            return outcome

        # A genuine redirect. Work out where it points (Location may be relative).
        redirect_target = urllib.parse.urljoin(current_url, location)
        outcome["redirect_target"] = redirect_target

        if not allow_redirects:
            return outcome          # default rule: a 3xx is DOWN, not followed

        hops += 1
        if hops > max_redirects:
            outcome["problem"] = "too many redirects"
            return outcome
        if validate_url(redirect_target):
            outcome["problem"] = "BadRedirect"
            return outcome
        current_url = redirect_target


def build_record(url, status, status_code, response_time, attempts, redirect_target, error_type):
    """The one place that defines what a result looks like."""
    return {
        "url": url,
        "status": status,
        "status_code": status_code,
        "response_time": round(response_time, 2),
        "attempts": attempts,
        "redirect_target": redirect_target,
        "redirected": redirect_target is not None,
        "error_type": error_type,
        "timestamp": now_text(),
    }


def check_single_url(url, timeout=5, retries=1, delay=1.0, allow_redirects=False, max_redirects=5):
    """
    Probe one URL with up to `retries` attempts and return a result record.
    response_time is the time of the LAST attempt, including every redirect hop.
    """
    attempts = 0
    status = "DOWN"
    status_code = None
    response_time = 0.0
    error_type = None
    redirect_target = None

    for attempt in range(1, retries + 1):
        attempts = attempt
        failure = None
        outcome = None
        start = time.perf_counter()      # monotonic clock, immune to clock changes

        try:
            outcome = fetch_once(url, timeout, allow_redirects, max_redirects)
        except urllib.error.URLError as exc:
            # DNS failure, refused connection, TLS error wrapped by urllib ...
            failure = classify_network_error(exc)
        except socket.timeout:
            # The server accepted us, then hung past the timeout (req. 20).
            failure = "TimeoutError"
        except ssl.SSLError:
            # Expired / self-signed / mismatched certificate (req. 20).
            failure = "SSLError"
        except (ConnectionError, http.client.HTTPException):
            # The server closed the connection early or sent garbage (req. 20).
            failure = "ConnectionClosedEarly"
        except Exception as exc:
            failure = f"Exception_{type(exc).__name__}"

        response_time = time.perf_counter() - start

        if failure is None:
            status_code = outcome["status_code"]
            redirect_target = outcome["redirect_target"]
            if outcome["problem"]:
                status, error_type = "DOWN", outcome["problem"]
            elif 200 <= status_code < 300:
                status, error_type = "UP", None
            else:
                status, error_type = "DOWN", f"HTTPStatus_{status_code}"
            break                      # we got an HTTP answer: no retry needed

        status, status_code, redirect_target, error_type = "DOWN", None, None, failure
        if is_retryable(failure) and attempt < retries:
            time.sleep(delay)
            continue
        break

    return build_record(url, status, status_code, response_time, attempts, redirect_target, error_type)


# ===========================================================================
# LEVEL 4: concurrency (requirement 16)
# ===========================================================================
def run_concurrent_checks(urls, timeout, retries, delay, allow_redirects, max_redirects, workers):
    """
    Check all URLs with a thread pool. Each worker builds its OWN result
    dictionary and returns it; nothing is shared or modified by two threads.
    Results go into a pre-sized list at the URL's own position, so the output
    order always matches the input order, however the threads finish.
    """
    results = [None] * len(urls)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(check_single_url, url, timeout, retries, delay,
                            allow_redirects, max_redirects): i
            for i, url in enumerate(urls)
        }
        for future in concurrent.futures.as_completed(future_to_index):
            i = future_to_index[future]
            try:
                results[i] = future.result()
            except Exception as exc:
                results[i] = build_record(urls[i], "DOWN", None, 0.0, 1, None,
                                          f"ThreadException_{type(exc).__name__}")
    return results


# ===========================================================================
# Reports and history (requirement 18)
# ===========================================================================
def save_reports(results, csv_filename, json_filename, history_filename):
    """CSV and JSON are overwritten each run; the history file is appended to."""
    try:
        if csv_filename:
            with open(csv_filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(results)

        if json_filename:
            with open(json_filename, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=4)

        if history_filename:
            is_new = not os.path.exists(history_filename)
            with open(history_filename, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                if is_new:
                    writer.writeheader()
                writer.writerows(results)
    except OSError as exc:
        print(f"[-] Could not write a report file: {exc}", file=sys.stderr)


# ===========================================================================
# Uptime statistics (requirement 19)
# ===========================================================================
def compute_uptime_statistics(history_filename):
    """
    Per URL: checks, uptime %, average response time (successful checks only,
    so timeouts do not distort it) and the most frequent failure reason.
    Overall: the slowest single successful response seen.
    Returns None if there is no history file, else (summary list, slowest).
    """
    if not history_filename or not os.path.exists(history_filename):
        return None

    stats = defaultdict(lambda: {"total": 0, "up": 0, "times": [], "failures": defaultdict(int)})

    with open(history_filename, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            url = row.get("url")
            if not url:
                continue
            entry = stats[url]
            entry["total"] += 1
            if row.get("status") == "UP":
                entry["up"] += 1
                try:
                    entry["times"].append(float(row.get("response_time", "")))
                except ValueError:
                    pass
            else:
                entry["failures"][row.get("error_type") or "UnknownError"] += 1

    summary = []
    slowest = ("", 0.0)
    for url, e in stats.items():
        uptime_pct = (e["up"] / e["total"]) * 100 if e["total"] else 0.0
        avg_time = sum(e["times"]) / len(e["times"]) if e["times"] else 0.0
        if e["times"] and max(e["times"]) > slowest[1]:
            slowest = (url, max(e["times"]))
        top_failure = max(e["failures"].items(), key=lambda kv: kv[1])[0] if e["failures"] else "None"
        summary.append({
            "url": url,
            "checks": e["total"],
            "uptime_pct": round(uptime_pct, 2),
            "avg_response_time": round(avg_time, 2),
            "most_frequent_failure": top_failure,
        })
    return summary, slowest


# ===========================================================================
# Output formatting (requirements 3 and 17)
# ===========================================================================
def format_result_line(r):
    """One line per URL, matching the sample output in the project brief."""
    url, status, code = r["url"], r["status"], r["status_code"]

    if code is None:                                   # no HTTP answer at all
        line = f"{url} {status} {r['error_type']}"
    else:
        seconds = f"{r['response_time']:.2f}s"
        if r["error_type"] in REDIRECT_PROBLEMS:
            line = f"{url} {status} {r['error_type']} {seconds}"
        else:
            line = f"{url} {status} {code} {seconds}"
            if r["redirected"]:
                if 300 <= code < 400:
                    line += f" (redirect to {r['redirect_target']} not followed; use --allow-redirects)"
                else:
                    line += " (redirected)"

    if r["attempts"] > 1:
        line += f" [{r['attempts']} attempts]"
    return line


def describe_change(url, status, state_history):
    """Compare this status with the previous one stored in memory."""
    history = state_history.get(url)
    if not history:
        return "first check"
    previous = history[-1][1]
    return "no change" if previous == status else f"{previous} -> {status}"


def print_watch_table(results, changes, cycle):
    """The running table printed on every cycle in watch mode."""
    print(f"\n--- Check #{cycle} at {now_text()} ---")
    width = max([len("URL")] + [len(r["url"]) for r in results])
    print(f"{'URL':<{width}}  {'STATUS':<6}  {'CODE':<5}  {'TIME':<7}  {'TRIES':<5}  CHANGE")
    for r, change in zip(results, changes):
        code = str(r["status_code"]) if r["status_code"] is not None else "-"
        seconds = f"{r['response_time']:.2f}s"
        note = "" if r["status"] == "UP" else f"  ({r['error_type']})"
        print(f"{r['url']:<{width}}  {r['status']:<6}  {code:<5}  {seconds:<7}  {r['attempts']:<5}  {change}{note}")
    changed = sum(1 for ch in changes if "->" in ch)
    print(f"Changes since last check: {changed}")


def print_state_history(state_history):
    """Printed when watch mode stops: every status change seen, per URL."""
    print("\n=== STATE HISTORY (this session, in memory) ===")
    for url, entries in state_history.items():
        flips = [(t, entries[i - 1][1], s) for i, (t, s) in enumerate(entries) if i and entries[i - 1][1] != s]
        print(f"{url}: {len(entries)} checks, {len(flips)} state changes")
        for t, old, new in flips:
            print(f"   [{t}] {old} -> {new}")


# ===========================================================================
# Running a cycle and the command line
# ===========================================================================
def execute_run(urls, args, state_history, cycle):
    """One full cycle: check everything, print, update memory, save files."""
    results = run_concurrent_checks(urls, args.timeout, args.retries, args.delay,
                                    args.allow_redirects, args.max_redirects, args.workers)

    changes = [describe_change(r["url"], r["status"], state_history) for r in results]

    if args.watch:
        print_watch_table(results, changes, cycle)
    else:
        for r in results:
            print(format_result_line(r))

    for r in results:                                   # remember for the next cycle
        state_history.setdefault(r["url"], []).append((r["timestamp"], r["status"]))

    up_count = sum(1 for r in results if r["status"] == "UP")
    print(f"Summary: {up_count} up, {len(results) - up_count} down")

    save_reports(results, args.report, args.json_report, args.history)
    if args.report:
        print(f"Report written to {args.report}")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Website Status Checker (standard library only).",
        epilog="3xx rule: by default any 3xx is DOWN and is not followed. With "
               "--allow-redirects, 301/302/303/307/308 are followed (up to "
               "--max-redirects) and the final status decides UP or DOWN.")
    parser.add_argument("urls", nargs="*", help="URLs to check, typed directly")
    parser.add_argument("-f", "--file", help="text file of URLs, one per line (# starts a comment)")
    parser.add_argument("-t", "--timeout", type=float, default=5, help="seconds to wait per attempt (default 5)")
    parser.add_argument("-r", "--retries", type=int, default=1, help="total attempts per URL (default 1)")
    parser.add_argument("-d", "--delay", type=float, default=1.0, help="seconds between attempts (default 1.0)")
    parser.add_argument("--allow-redirects", action="store_true",
                        help="follow 301/302/303/307/308; the final status decides UP/DOWN")
    parser.add_argument("--max-redirects", type=int, default=5,
                        help="redirect hop cap when following redirects (default 5)")
    parser.add_argument("-w", "--workers", type=int, default=5, help="concurrent worker threads (default 5)")
    parser.add_argument("--report", default="status_report.csv", help="CSV report file")
    parser.add_argument("--json-report", help="optional JSON report file")
    parser.add_argument("--history", default="status_history.csv", help="history file appended on every run")
    parser.add_argument("--watch", type=int, help="re-check every N seconds until Ctrl+C")
    parser.add_argument("--stats", action="store_true", help="print uptime statistics from the history file, then exit")
    args = parser.parse_args()

    # Reject nonsense values with a clear message instead of failing later.
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    if args.retries < 1:
        parser.error("--retries must be at least 1")
    if args.delay < 0:
        parser.error("--delay cannot be negative")
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.max_redirects < 0:
        parser.error("--max-redirects cannot be negative")
    if args.watch is not None and args.watch < 1:
        parser.error("--watch must be at least 1 second")
    return args


def main():
    args = parse_arguments()

    if args.stats:
        print("\n=== HISTORICAL UPTIME & TELEMETRY STATISTICS ===")
        result = compute_uptime_statistics(args.history)
        if result is None or not result[0]:
            print("No history found or history is empty.")
        else:
            summary, slowest = result
            for item in summary:
                print(f"URL: {item['url']}")
                print(f"  - Checks: {item['checks']}")
                print(f"  - Uptime: {item['uptime_pct']}%")
                print(f"  - Avg response time (UP checks): {item['avg_response_time']}s")
                print(f"  - Most frequent failure: {item['most_frequent_failure']}")
            if slowest[0]:
                print(f"\nSlowest single response: {slowest[0]} ({slowest[1]}s)")
        sys.exit(0)

    urls = parse_and_validate_urls(file_path=args.file, url_args=args.urls)
    if not urls:
        print("[-] No valid URLs provided to check. Exiting.", file=sys.stderr)
        sys.exit(1)

    print(f"\nChecking {len(urls)} sites (timeout {args.timeout:g}s)...")

    state_history = {}
    if args.watch:
        print(f"Watch mode: re-checking every {args.watch} seconds. Press Ctrl+C to stop.")
        cycle = 0
        try:
            while True:
                cycle += 1
                execute_run(urls, args, state_history, cycle)
                print(f"\nWaiting {args.watch} seconds for the next check...")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nWatch mode stopped.")
            print_state_history(state_history)
    else:
        execute_run(urls, args, state_history, 1)


if __name__ == "__main__":
    main()
