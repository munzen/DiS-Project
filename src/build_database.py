"""
Script turns the raw MySQL dump (endofday.sql) into the
project's PostgreSQL database. Then exports everything as a single
restorable SQL file (database.sql).
We process by transforming the table, COPY'ing the table to the database 
Functions:
  1. Apply schema.sql      -> create tables
  2. Parse endofday.sql    -> extract ticker / date / close from each row
  3. Bulk-load with COPY   -> staging table, then transform into the schema
  4. Export with pg_dump   -> database.sql (the single project DB file)

  
Prerequisites: the database must already exist (createdb portfolio_app),
and schema.sql + endofday.sql must be in the current directory.

Run with:  python build_database.py
"""

import csv
import io
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

import psycopg2

# --- Configuration -------------------------------------------------------
DB_NAME     = "portfolio_app"
DB_USER     = "postgres"
DB_HOST     = "localhost"
DB_PORT     = "5432"

DUMP_FILE   = "src/endofday.sql"
SCHEMA_FILE = "src/schema.sql"
OUTPUT_FILE = "src/database.sql"

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL  = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

NASDAQ_MKT = {
    'Q': 'NASDAQ Global Select',
    'G': 'NASDAQ Global Market',
    'S': 'NASDAQ Capital Market',
}
OTHER_EXCH = {
    'N': 'NYSE',
    'A': 'NYSE American',
    'P': 'NYSE Arca',
    'Z': 'Cboe BZX',
    'V': 'IEX',
}

# Grabs the contents of each "(...)" row group on an INSERT line.
ROW_RE = re.compile(r"\(([^)]*)\)")


def fetch_listing():
    """Fetch NASDAQ trader listing files; return dict ticker -> (company_name, exchange)."""
    lookup = {}

    def fetch_lines(url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8").splitlines()

    # nasdaqlisted.txt: Symbol|Security Name|Market Category|...
    for line in fetch_lines(NASDAQ_LISTED_URL)[1:]:  # skip header
        line = line.strip()
        if not line or line.startswith("File Creation"):
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        sym, name, mkt = parts[0], parts[1], parts[2]
        lookup[sym] = (name, NASDAQ_MKT.get(mkt, f"NASDAQ({mkt})"))

    # otherlisted.txt: ACT Symbol|Security Name|Exchange|...
    for line in fetch_lines(OTHER_LISTED_URL)[1:]:  # skip header
        line = line.strip()
        if not line or line.startswith("File Creation"):
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        sym, name, exch = parts[0], parts[1], parts[2]
        lookup[sym] = (name, OTHER_EXCH.get(exch, f"Other({exch})"))

    return lookup


def parse_field(raw):
    """Clean one MySQL value: drop surrounding quotes, NULL -> None."""
    raw = raw.strip()
    if raw.upper() == "NULL":
        return None
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1]
    return raw


def extract_rows(dump_path, csv_path):
    """Stream the dump; write ticker,date,close rows to csv_path."""
    written = 0
    with open(dump_path, "r", encoding="latin-1") as src, \
         open(csv_path, "w", newline="", encoding="utf-8") as dst:
        writer = csv.writer(dst)
        for line in src:
            if not line.lstrip().upper().startswith("INSERT INTO"):
                continue
            # Work only with the part after VALUES, ignoring the table name.
            marker = line.upper().find(" VALUES ")
            if marker == -1:
                continue
            for group in ROW_RE.findall(line[marker + 8:]):
                fields = group.split(",")
                if len(fields) < 3:
                    continue
                # surf_eod column order: ticker, date, close, high, low...
                ticker = parse_field(fields[0])
                date   = parse_field(fields[1])
                close  = parse_field(fields[2])
                if ticker and date and close:
                    writer.writerow([ticker, date, close])
                    written += 1
                    if written % 1_000_000 == 0:
                        print(f"  ...parsed {written:,} rows")
    return written


def main():
    for path in (DUMP_FILE, SCHEMA_FILE):
        if not os.path.exists(path):
            sys.exit(f"Missing required file: {path}")

    print("Fetching exchange listing from nasdaqtrader.com ...")
    listing = fetch_listing()
    print(f"Listing loaded: {len(listing):,} entries.")

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8")
    tmp.close()
    csv_path = tmp.name

    try:
        print("Parsing endofday.sql ...")
        rows = extract_rows(DUMP_FILE, csv_path)
        print(f"Parsed {rows:,} price rows.")

        print("Connecting to PostgreSQL ...")
        conn = psycopg2.connect(
            dbname=DB_NAME, user=DB_USER,
            password=None,
            host=DB_HOST, port=DB_PORT)
        conn.autocommit = False
        cur = conn.cursor()

        try:
            print("Applying schema.sql ...")
            with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
                cur.execute(f.read())

            print("Creating staging table ...")
            # UNLOGGED skips write-ahead logging -> faster bulk load.
            cur.execute("""
                DROP TABLE IF EXISTS price_staging;
                CREATE UNLOGGED TABLE price_staging (
                    ticker      varchar(10),
                    price_date  date,
                    close_price varchar(20)
                );
            """)

            print("Bulk-loading rows with COPY ...")
            with open(csv_path, "r", encoding="utf-8") as f:
                cur.copy_expert(
                    "COPY price_staging FROM STDIN WITH CSV", f)

            print("Transforming into stocks / stock_price ...")
            # stocks first: stock_price's foreign key depends on it.
            cur.execute("SELECT DISTINCT ticker FROM price_staging;")
            tickers = [row[0] for row in cur.fetchall()]
            matched = 0
            stock_rows = []
            for ticker in tickers:
                if ticker in listing:
                    name, exch = listing[ticker]
                    matched += 1
                else:
                    name, exch = ticker, None
                stock_rows.append((ticker, name, exch))
            cur.executemany(
                "INSERT INTO stocks (ticker, company_name, exchange) VALUES (%s, %s, %s)"
                " ON CONFLICT (ticker) DO NOTHING;",
                stock_rows,
            )
            print(f"  Stocks: {len(stock_rows):,} inserted, "
                  f"{matched:,} with company name/exchange ({matched/len(stock_rows):.1%}).")
            cur.execute("""
                INSERT INTO stock_price (ticker, price_date, close_price)
                SELECT ticker, price_date, NULLIF(close_price, '')::numeric
                FROM price_staging
                ON CONFLICT (ticker, price_date) DO NOTHING;
            """)
            cur.execute("DROP TABLE price_staging;")

            conn.commit()
            print("Database populated successfully.")
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

        print(f"Exporting database to {OUTPUT_FILE} ...")
        env = os.environ.copy()
        subprocess.run(
            ["pg_dump", "-d", DB_NAME, "-U", DB_USER,
             "-h", DB_HOST, "-p", DB_PORT,
             "--no-owner", "--no-privileges", "-f", OUTPUT_FILE],
            env=env, check=True)
        print(f"Done. {OUTPUT_FILE} is the single project database file.")

    finally:
        if os.path.exists(csv_path):
            os.remove(csv_path)


if __name__ == "__main__":
    main()
