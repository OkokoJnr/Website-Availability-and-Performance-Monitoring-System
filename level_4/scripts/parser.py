
import argparse
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
        default=1,
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
        default=5,
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
