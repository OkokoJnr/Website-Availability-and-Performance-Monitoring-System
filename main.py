import urllib.request
import time
import socket
import csv
from urllib.error import URLError, HTTPError


def read_urls(filepath:str) -> list[str]:
    with open(filepath) as websites:
        urls = websites.readlines()
    return urls

def validate_urls(urls:list[str]) -> list[str]:
    for i in range(len(urls)):
        urls[i]=urls[i].strip()
    return urls

def check_website(url):
    start_time = time.perf_counter()
    result = {
        "website": url,
        "status_code": "",
        "status": "DOWN",
        "response_time_ms": 0,
        "final_url": "",
        "error": ""  
    }
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            
            result["status_code"] = response.status
            result["final_url"] = response.url

            if 200 <= response.status < 300:
                result["status"] = "UP"
            else:
                result["status"] = "DOWN"
            if url != response.url:
                print(f"Redirect detected: {url} => {response.url}")
                
    except socket.timeout:
        result["error"] = "Request timeout"
    except HTTPError as error:
        result["status_code"] = error.code
        result["final_url"] = error.url
        result["error"] = f"HTTP Error: {error.code}"
    except Exception as error:
        result["error"] = f"Unexpected Error: {error}"
    finally:
        end_time = time.perf_counter()
        result["response_time_ms"] = round((end_time - start_time)*1000, 2)
    return result

def write_to_csv(input):
    fieldnames = [
        "website",
        "status_code",
        "status",
        "response_time_ms",
        "final_url",
        "error"    
    ]
    with open("results.csv", "w", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)

        writer.writeheader()
        writer.writerows(input)

def main():
    urls = read_urls("./input/websites.txt")
    urls = validate_urls(urls)
    results = []
    for url in urls:
        if not url:
            continue
        response = check_website(url)
        results.append(response)

    write_to_csv(results)
  
if __name__ == "__main__":
    main()