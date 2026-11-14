"""
Build the Lambda's data from the exported tables (make data).

Usage: python scripts/build_data.py [data dir] [repo root]

Reads data/ as written by scripts/export_data.R (<table>.csv.gz, columns.csv,
levels.csv, SOURCE) and writes diversity/data/ under the repo root. Only the
standard library is used.

The output is a torgucols store (github.com/tor-gu/torgucols): one int32 file per
column, so a request loads only the columns it uses and the cold start doesn't
parse the tables. Factor and character columns (agency_id) are stored as codes
whose order is the API's: declared levels, FALSE before TRUE, null last. Each
table's meta has its weight, the count column (None: one row per key).
"""

import csv
import gzip
import os
import re
import sys
from collections import Counter, defaultdict

import torgucols

OUT = os.path.join("diversity", "data")

# Table -> its count column (None: a table of one row per key)
TABLES = {
    "officer": "officer_count",
    "applicant": "applicant_count",
    "agency_practice": None,
}
PRACTICE_KEY = ["year", "agency_id"]

AGENCY_ID = re.compile(r"^NJLEA[0-9]{4}$")


class BuildError(Exception):
    pass


def read_csv(path):
    """Header and rows of a CSV file (gzipped if .gz), as lists of strings"""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        return header, list(reader)


def read_source(path):
    with open(path, encoding="utf-8") as f:
        return dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)


def encode_column(name, type_, values, levels):
    """The column, encoded by torgucols ({name, type, codes, dictionary})"""
    if type_ == "factor" and not levels:
        raise BuildError(f"{name}: no levels in levels.csv")
    try:
        column = torgucols.encode_column(name, type_, values, levels)
    except torgucols.EncodeError as e:
        raise BuildError(str(e)) from None
    column["name"] = name.split(".", 1)[1]
    return column


def read_table(data_dir, table, types, levels):
    """{"columns": [encoded columns], "rows": n}, as read from the export"""
    names = [c for c, _ in types]
    header, rows = read_csv(os.path.join(data_dir, f"{table}.csv.gz"))
    if header != names:
        raise BuildError(f"{table}: header {header} doesn't match columns.csv {names}")
    for i, row in enumerate(rows):
        if len(row) != len(names):
            raise BuildError(f"{table} row {i + 1}: {len(row)} fields")
    columns = [
        encode_column(
            f"{table}.{name}", type_, [r[j] for r in rows], levels.get((table, name))
        )
        for j, (name, type_) in enumerate(types)
    ]
    return {"columns": columns, "rows": len(rows)}


def read_export(data_dir):
    """{table: read_table()} and SOURCE"""
    _, rows = read_csv(os.path.join(data_dir, "columns.csv"))
    types = defaultdict(list)
    for table, _, column, type_ in sorted(rows, key=lambda r: (r[0], int(r[1]))):
        types[table].append((column, type_))
    _, rows = read_csv(os.path.join(data_dir, "levels.csv"))
    levels = defaultdict(list)
    for table, column, _, level in sorted(rows, key=lambda r: (r[0], r[1], int(r[2]))):
        levels[(table, column)].append(level)
    tables = {}
    for table in TABLES:
        if table not in types:
            raise BuildError(f"{table} is not in columns.csv")
        tables[table] = read_table(data_dir, table, types[table], levels)
    return tables, read_source(os.path.join(data_dir, "SOURCE"))


def check_table(name, table, weight):
    """The build fails on data the routes rely on not having"""
    if table["rows"] == 0:
        raise BuildError(f"{name}: no rows")
    columns = {c["name"]: c for c in table["columns"]}
    for required in ("year", "agency_id"):
        if required not in columns:
            raise BuildError(f"{name}: no {required} column")
        null = torgucols.INT32_MIN if columns[required]["type"] == "integer" else 0
        if null in columns[required]["codes"]:
            raise BuildError(f"{name}: null {required}")
    for agency_id in columns["agency_id"]["dictionary"]:
        if not AGENCY_ID.match(agency_id):
            raise BuildError(f"{name}: bad agency_id {agency_id!r}")
    if weight is not None:
        if weight not in columns:
            raise BuildError(f"{name}: no {weight} column")
        if min(columns[weight]["codes"]) < 1:
            raise BuildError(f"{name}: {weight} must be positive")
        key = [c for c in columns if c != weight]
    else:
        key = PRACTICE_KEY
    keys = zip(*(columns[c]["codes"] for c in key))
    duplicates = [k for k, n in Counter(keys).items() if n > 1]
    if duplicates:
        raise BuildError(f"{name}: duplicate keys {duplicates[:5]}")


def build(data_dir, root):
    tables, source = read_export(data_dir)
    for name, weight in TABLES.items():
        check_table(name, tables[name], weight)
    return torgucols.write(
        os.path.join(root, OUT),
        {
            name: {"columns": table["columns"], "meta": {"weight": TABLES[name]}}
            for name, table in tables.items()
        },
        source,
    )


def main(argv):
    data_dir = argv[1] if len(argv) > 1 else "data"
    root = argv[2] if len(argv) > 2 else "."
    try:
        out_dir = build(data_dir, root)
    except BuildError as e:
        sys.exit(f"build_data: {e}")
    size = sum(os.path.getsize(os.path.join(out_dir, f)) for f in os.listdir(out_dir))
    print(f"Wrote {out_dir} ({size:,} bytes)")


if __name__ == "__main__":
    main(sys.argv)
