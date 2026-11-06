import time
from scripts.read_urls import read_urls
from scripts.validator import validate_urls
from scripts.concurrency import check_websites
from scripts.printer import print_results, print_summary, print_state_history
from scripts.tracker import track_changes
from scripts.parser import parse_arguments
from scripts.stats import uptime_statistics
from scripts.save_report import save_reports, append_history





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
            worker_start_time = time.perf_counter()
            results = check_websites(
                urls,
                args.timeout,
                args.retries,
                args.allow_redirects,
                args.workers
            )

            worker_end_time = time.perf_counter()

            print(f"{args.workers} Workers took {worker_end_time - worker_start_time} to process {len(results)} Results")
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
        pass
        # if state_history:
        #     print_state_history(state_history)

        print("\nMonitoring completed.")


if __name__ == "__main__":
    main()