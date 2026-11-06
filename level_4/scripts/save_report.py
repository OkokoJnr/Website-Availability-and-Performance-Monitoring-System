import os
import json
import csv

def save_reports(results, csv_filepath, json_filepath):

    if not results:
        return

    os.makedirs(os.path.dirname(csv_filepath) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(json_filepath) or ".", exist_ok=True)

    # CSV report
    with open(csv_filepath, "w", newline="", encoding="utf-8") as file:

        writer = csv.DictWriter(
            file,
            fieldnames=results[0].keys()
        )

        writer.writeheader()
        writer.writerows(results)

    # JSON report
    with open(json_filepath, "w", encoding="utf-8") as file:

        json.dump(results, file, indent=4)



def append_history(results, filepath):

    if not results:
        return

    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

    file_exists = (
        os.path.exists(filepath)
        and os.path.getsize(filepath) > 0
    )

    with open(filepath, "a", newline="", encoding="utf-8") as file:

        writer = csv.DictWriter(
            file,
            fieldnames=results[0].keys()
        )

        if not file_exists:
            writer.writeheader()

        writer.writerows(results)

