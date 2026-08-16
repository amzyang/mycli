"""\\ed command: edit a table's DDL in an external editor and turn the diff
into ALTER statements via mysqldef's offline mode."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from typing import Callable

import click
from pymysql.cursors import Cursor

from mycli.packages.special.main import ArgType, parse_special_command, special_command

logger = logging.getLogger(__name__)

USAGE = 'Usage: \\ed <table>. Opens the table DDL in $EDITOR; the diff comes back as ALTER statements.'

INSTALL_HINT = 'mysqldef not found. Install it with: brew install sqldef/sqldef/mysqldef'


@special_command(
    '\\ed',
    '/ed <table>',
    'Edit table DDL in editor; the diff comes back as ALTER statements.',
    arg_type=ArgType.RAW_QUERY,
    case_sensitive=True,
)
def ed_stub() -> None:
    raise NotImplementedError


def is_ddl_edit_command(command: str) -> bool:
    cmd, _, _ = parse_special_command(command)
    return cmd in ('\\ed', '/ed')


def quote_identifier(name: str) -> str:
    return '.'.join('`{}`'.format(part.replace('`', '``')) for part in name.split('.'))


def fetch_create_table(cur: Cursor, table: str) -> str:
    query = f'SHOW CREATE TABLE {quote_identifier(table)}'
    logger.debug(query)
    cur.execute(query)
    row = cur.fetchone()
    return row[1] if row else ''


def extract_statements(output: str) -> str:
    """Strip mysqldef's comment lines (e.g. "-- Nothing is modified --"), keep the DDL."""
    lines = [line for line in output.splitlines() if line.strip() and not line.strip().startswith('--')]
    return '\n'.join(lines).strip()


def _terminated(sql: str) -> str:
    sql = sql.strip()
    return sql + ('\n' if sql.endswith(';') else ';\n')


def run_mysqldef(current_ddl: str, desired_ddl: str) -> str:
    """Diff two DDL texts with mysqldef's offline mode (no database connection)."""
    if shutil.which('mysqldef') is None:
        raise RuntimeError(INSTALL_HINT)
    with tempfile.NamedTemporaryFile('w', suffix='.sql') as current_file:
        current_file.write(_terminated(current_ddl))
        current_file.flush()
        proc = subprocess.run(
            ['mysqldef', current_file.name],
            input=_terminated(desired_ddl),
            capture_output=True,
            text=True,
        )
    if proc.returncode != 0:
        raise RuntimeError(f'mysqldef: {proc.stderr.strip() or proc.stdout.strip()}')
    return proc.stdout


def handle_ddl_edit(
    cur: Cursor,
    text: str,
    runner: Callable[[str, str], str] = run_mysqldef,
) -> tuple[str | None, str | None]:
    """Handle a \\ed command. Returns (alter_sql, message); exactly one is set."""
    _, _, arg = parse_special_command(text)
    table = arg.strip().rstrip(';').strip()
    if not table:
        return (None, USAGE)
    current_ddl = fetch_create_table(cur, table)
    try:
        edited = click.edit(current_ddl + '\n', extension='.sql')
    except click.ClickException:
        # Editor exited non-zero (e.g. vim :cq); treat as a deliberate abort.
        edited = None
    if edited is None:
        return (None, 'DDL edit cancelled.')
    migration = extract_statements(runner(current_ddl, edited))
    if not migration:
        return (None, 'No schema changes detected.')
    return (migration, None)
