"""
The tables, from the torgucols store in diversity/data (see scripts/build_data.py).

Columns are loaded on first use and kept while the function is warm. A request
selects row indexes or sums the count column (torgucols' Table.select and
group, cached by their validated parameters), with no SQLite or pandas, and only
the page it returns is made into dicts.
"""

import os

import torgucols

from .params import InvalidRequest, NotFound

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

MAX_DIMENSIONS = 4

# Loaded on first use and kept while the function is warm
_data = None


def get_data():
    global _data
    if _data is None:
        _data = torgucols.open(os.environ.get("DATA_DIR", DEFAULT_DATA_DIR))
    return _data


def keys(table):
    """The table's key columns: all but its count column"""
    return [n for n in table.names if n != table.meta["weight"]]


def parse_by(table, by):
    """The by parameter as a tuple of the table's key columns"""
    if by is None:
        raise InvalidRequest("by is required")
    dimensions = tuple(by.split(","))
    if len(dimensions) > MAX_DIMENSIONS:
        raise InvalidRequest(f"by needs 1 to {MAX_DIMENSIONS} columns")
    if len(set(dimensions)) != len(dimensions):
        raise InvalidRequest(f"Repeated column in by={by}")
    for d in dimensions:
        if d not in keys(table):
            raise InvalidRequest(f"Unknown column {d} in by")
    return dimensions


def paged(rows, page, convert):
    """
    One page of rows, converted.

    Returns:
        list of records, aux dict for torguapi_make_links_and_meta

    Raises:
        NotFound when there are no rows, or the page is past the last one
    """
    try:
        items, aux = torgucols.page(rows, page["page_number"], page["page_size"])
    except (torgucols.EmptyResult, torgucols.PageOutOfRange) as e:
        raise NotFound(str(e)) from None
    return [convert(r) for r in items], aux


def row_converter(table):
    """Function from a row index to its record"""
    columns = [(n, table.values(n), table.decoder(n)) for n in table.names]
    return lambda i: {n: decode(values[i]) for n, values, decode in columns}


def get_rows(table, filters, page):
    """One page of the rows where the filters hold"""
    return paged(table.select(filters), page, row_converter(table))


def get_row(table, filters):
    """The one row where the filters (the table's key) hold, or None"""
    rows = table.select(filters)
    if not rows:
        return None
    return row_converter(table)(rows[0])


def get_counts(table, dimensions, filters, page):
    """One page of {<dimension>: value, ..., "count": total}"""
    decoders = [table.decoder(d) for d in dimensions]

    def convert(item):
        key, total = item
        record = {d: f(v) for d, f, v in zip(dimensions, decoders, key)}
        record["count"] = total
        return record

    return paged(table.group(dimensions, filters, table.meta["weight"]), page, convert)
