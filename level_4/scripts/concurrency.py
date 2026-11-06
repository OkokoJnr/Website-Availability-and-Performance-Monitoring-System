
from concurrent.futures import ThreadPoolExecutor
from .monitor import monitor_website

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
