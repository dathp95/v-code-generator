"""Bounded repository pages with literal search and allowlisted sorting."""

from dataclasses import dataclass


@dataclass
class Page:
    records: list[dict]
    total: int
    number: int
    size: int


def search_pattern(value):
    return "%" + value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def query_page(connection, select, where, parameters, *, sorts, sort, descending, page, size):
    if sort not in sorts:
        raise ValueError("Unsupported sort column.")
    number, size = max(0, int(page)), max(1, min(int(size), 100))
    base = select + " WHERE " + where
    total = connection.execute("SELECT count(*) FROM (" + base + ")", parameters).fetchone()[0]
    number = min(number, max(0, (total - 1) // size))
    # Only repository-owned expressions and these two fixed direction keywords enter SQL.
    order = sorts[sort] + (" DESC" if descending else " ASC")
    rows = connection.execute(base + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                              (*parameters, size, number * size)).fetchall()
    return Page([dict(row) for row in rows], total, number, size)
