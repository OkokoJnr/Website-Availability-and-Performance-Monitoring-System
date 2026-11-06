import time
from .checker import check_website

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

