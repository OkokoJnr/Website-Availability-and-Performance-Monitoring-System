import os
import csv

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

