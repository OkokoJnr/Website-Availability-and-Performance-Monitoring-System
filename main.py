import urllib.request


def main():
    with open("./input/websites.txt") as websites:
        urls = websites.readlines()
        print(urls)
    for url in urls:
        url = url.strip()
        with urllib.request.urlopen(url) as response:
            if 200 <= response.status < 300:
                status = "UP"
            else:
                status = "DOWN"
            print(f"{url} is {status}")


if __name__ == "__main__":
    main()