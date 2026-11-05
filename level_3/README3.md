
How to run Level 3


1. Basic execution
python3 main.py --input sites.txt

2. Change timeout
python3 main.py --input sites.txt --timeout 10

3. Enable retries
python3 main.py --input sites.txt --retries 5

This means the program can make up to six attempts: one initial attempt and five additional attempts.

4. Allow redirects
python3 main.py --input sites.txt --allow-redirects

5. Combine options


python3 main.py \
    --input sites.txt \
    --timeout 5 \
    --retries 3 \
    --allow-redirects \
    --output reports/status_report.csv