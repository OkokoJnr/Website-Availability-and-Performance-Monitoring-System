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

