
import urllib.request
import time
import socket
import ssl
import http.client


from datetime import datetime
from urllib.parse import urlparse, urljoin
from urllib.error import URLError, HTTPError

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

