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




def print_state_history(state_history):

    print("\nWEBSITE STATE HISTORY")
    print("-" * 60)

    for url, states in state_history.items():

        print(f"\n{url}")
        print(" -> ".join(states))
