import urllib.request
import argparse



"""

5.Accept URLs from a text file (one per line) or from command-line arguments. 

6.Use urllib.request to fetch each URL and record the HTTP status code. 

7.Print UP/DOWN per URL with the status code. 
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

def check_website(url):

    try:

        with urllib.request.urlopen(url) as response:

            status_code = response.getcode()

            if 200 <= status_code < 300:
                status = "UP"
            else:
                status = "DOWN"

            return status, status_code

    except urllib.error.HTTPError as error:

        return "DOWN", error.code

    except urllib.error.URLError:

        return "DOWN", "Connection error"


# 3. CHECK ALL WEBSITES

def check_websites(urls):

    results = []

    for url in urls:

        url = url.strip()

        if not url:
            continue

        status, status_code = check_website(url)

        results.append({
            "url": url,
            "status": status,
            "status_code": status_code
        })

    return results


# 4. DISPLAY RESULTS

def print_results(results):

    print("\nWEBSITE STATUS RESULTS")
    print("-" * 70)

    for result in results:

        print(
            f"{result['url']:<40} "
            f"{result['status']:<6} "
            f"{result['status_code']}"
        )

    print("-" * 70)


# 5. COMMAND-LINE ARGUMENTS

def parse_arguments():

    parser = argparse.ArgumentParser(
        description="Website Status Checker - Level 1"
    )

    parser.add_argument(
        "--input",
        help="Path to the file containing URLs"
    )

    parser.add_argument(
        "urls",
        nargs="*",
        help="URLs supplied directly"
    )

    args = parser.parse_args()

    if args.input and args.urls:
        parser.error("Use either --input or positional URLs.")

    return args


# 6. MAIN FUNCTION

def main():

    args = parse_arguments()

    # Read URLs
    if args.urls:
        urls = args.urls

    else:
        filepath = args.input or "./input/websites.txt"
        urls = read_urls(filepath)

    if not urls:
        print("No URLs provided.")
        return

    print(f"\nChecking {len(urls)} websites...\n")

    results = check_websites(urls)

    print_results(results)


if __name__ == "__main__":
    main()