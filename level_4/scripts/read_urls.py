def read_urls(filepath):

    try:
        with open(filepath, "r", encoding="utf-8") as file:
            return file.readlines()

    except FileNotFoundError:
        print(f"Input file not found: {filepath}")
        return []
