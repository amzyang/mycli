"""Grep-style search commands for mycli.

Two standalone special commands inspired by DataGrip:

* ``\\grep``  — search table/column names and column comments (information_schema).
* ``\\dgrep`` — search the data in every text column of the current database,
  the CLI equivalent of DataGrip's "Find in database / Full-text search".

Kept in its own module so the feature stays isolated from the upstream
``dbcommands.py``; registration happens via the import in
``mycli/packages/special/__init__.py``.
"""

from __future__ import annotations

import logging

from pymysql import Error
from pymysql.cursors import Cursor

from mycli.packages.special.main import ArgType, special_command
from mycli.packages.sqlresult import SQLResult

logger = logging.getLogger(__name__)

# DataGrip's "Text columns" set: string types that support a plain LIKE.
# JSON is intentionally excluded (DataGrip puts it under "All columns", which needs CAST).
GREP_TEXT_DATA_TYPES = ('char', 'varchar', 'tinytext', 'text', 'mediumtext', 'longtext', 'enum', 'set')


def _quote_identifier(name: str) -> str:
    return '`' + name.replace('`', '``') + '`'


@special_command(
    "\\grep",
    "\\grep[+] <pattern>",
    "Search table/column names and column comments for a substring. '+' searches all databases.",
    arg_type=ArgType.PARSED_QUERY,
    case_sensitive=True,
)
def grep_schema(
    cur: Cursor,
    arg: str | None = None,
    command_verbosity: bool = False,
    **_: object,
) -> list[SQLResult]:
    if not arg:
        return [SQLResult(status="Usage: \\grep[+] <pattern>")]

    pattern = f"%{arg}%"
    # '+' widens the scope from the current database to every database.
    schema_filter = '' if command_verbosity else 'AND TABLE_SCHEMA = DATABASE()'

    tables_query = (
        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE "
        "FROM information_schema.tables "
        f"WHERE TABLE_NAME LIKE %s {schema_filter} "
        "ORDER BY TABLE_SCHEMA, TABLE_NAME"
    )
    logger.debug(tables_query)
    cur.execute(tables_query, (pattern,))
    tables_header = [x[0] for x in cur.description] if cur.description else None
    # Fetch before the next execute() so the cursor isn't overwritten.
    tables_rows = list(cur.fetchall())

    columns_query = (
        "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT "
        "FROM information_schema.columns "
        f"WHERE (COLUMN_NAME LIKE %s OR COLUMN_COMMENT LIKE %s) {schema_filter} "
        "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
    )
    logger.debug(columns_query)
    cur.execute(columns_query, (pattern, pattern))
    columns_header = [x[0] for x in cur.description] if cur.description else None
    columns_rows = list(cur.fetchall())

    results: list[SQLResult] = []
    if tables_rows:
        results.append(SQLResult(preamble="Tables", header=tables_header, rows=tables_rows))
    if columns_rows:
        results.append(SQLResult(preamble="Columns", header=columns_header, rows=columns_rows))
    if not results:
        results.append(SQLResult(status=f"No schema objects matching {arg!r}."))
    return results


@special_command(
    "\\dgrep",
    "\\dgrep[+] <pattern>",
    "Search every text column of the current database for a substring. '+' removes the per-table row limit.",
    arg_type=ArgType.PARSED_QUERY,
    case_sensitive=True,
)
def grep_data(
    cur: Cursor,
    arg: str | None = None,
    command_verbosity: bool = False,
    **_: object,
) -> list[SQLResult]:
    if not arg:
        return [SQLResult(status="Usage: \\dgrep[+] <pattern>")]

    cur.execute("SELECT DATABASE()")
    row = cur.fetchone()
    dbname = row[0] if row else None
    if not dbname:
        return [SQLResult(status="No database selected. Use \\u <db> first.")]

    placeholders = ', '.join(['%s'] * len(GREP_TEXT_DATA_TYPES))
    columns_query = (
        "SELECT TABLE_NAME, COLUMN_NAME "
        "FROM information_schema.columns "
        f"WHERE TABLE_SCHEMA = %s AND DATA_TYPE IN ({placeholders}) "
        "ORDER BY TABLE_NAME, ORDINAL_POSITION"
    )
    logger.debug(columns_query)
    cur.execute(columns_query, (dbname, *GREP_TEXT_DATA_TYPES))
    # Only tables that have at least one text column show up here — the natural,
    # correctness-safe pruning (no unreliable TABLE_ROWS guessing).
    columns_by_table: dict[str, list[str]] = {}
    for table_name, column_name in cur.fetchall():
        columns_by_table.setdefault(table_name, []).append(column_name)

    pattern = f"%{arg}%"
    limit_clause = '' if command_verbosity else ' LIMIT 100'

    results: list[SQLResult] = []
    for table_name, columns in columns_by_table.items():
        where = ' OR '.join(f"{_quote_identifier(col)} LIKE %s" for col in columns)
        query = f"SELECT * FROM {_quote_identifier(table_name)} WHERE {where}{limit_clause}"
        logger.debug(query)
        try:
            cur.execute(query, (pattern,) * len(columns))
        except Error:
            # Skip objects we cannot scan (e.g. views over missing tables, no privilege).
            logger.debug("Skipped %s during \\dgrep", table_name, exc_info=True)
            continue
        header = [x[0] for x in cur.description] if cur.description else None
        rows = list(cur.fetchall())
        if rows:
            results.append(SQLResult(preamble=f"{table_name} ({len(rows)})", header=header, rows=rows))

    if not results:
        return [SQLResult(status=f"No matches for {arg!r} in `{dbname}`.")]
    return results
