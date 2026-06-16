# type: ignore

from pymysql import ProgrammingError

from mycli.packages.special.grepcommands import (
    GREP_NUMERIC_DATA_TYPES,
    GREP_TEXT_DATA_TYPES,
    grep_data,
    grep_schema,
)


class SequenceCursor:
    """A fake cursor that returns predefined steps in execute() order.

    Each step is a dict with optional keys:
      - 'description': value assigned to cursor.description after this execute
      - 'rows': what fetchall()/fetchone() return until the next execute
      - 'raise': an exception instance raised on execute (the step is still consumed)
    """

    def __init__(self, steps):
        self.steps = list(steps)
        self.executed = []  # list of (query, args)
        self.description = None
        self._rows = []

    def execute(self, query, args=None):
        self.executed.append((query, args))
        step = self.steps.pop(0)
        if 'raise' in step:
            raise step['raise']
        self.description = step.get('description')
        self._rows = list(step.get('rows', []))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


# ---------------------------------------------------------------------------
# \grep
# ---------------------------------------------------------------------------


def test_grep_schema_current_db_returns_tables_and_columns():
    cur = SequenceCursor([
        {  # tables query
            'description': [('TABLE_SCHEMA',), ('TABLE_NAME',), ('TABLE_TYPE',)],
            'rows': [('app', 'orders', 'BASE TABLE')],
        },
        {  # columns query
            'description': [('TABLE_SCHEMA',), ('TABLE_NAME',), ('COLUMN_NAME',), ('COLUMN_TYPE',), ('COLUMN_COMMENT',)],
            'rows': [('app', 'order_items', 'order_id', 'int', '订单ID')],
        },
    ])

    results = grep_schema(cur, arg='order')

    assert len(results) == 2
    assert results[0].preamble == 'Tables'
    assert results[0].header == ['TABLE_SCHEMA', 'TABLE_NAME', 'TABLE_TYPE']
    assert results[0].rows == [('app', 'orders', 'BASE TABLE')]
    assert results[1].preamble == 'Columns'
    assert results[1].rows == [('app', 'order_items', 'order_id', 'int', '订单ID')]

    tables_sql, tables_args = cur.executed[0]
    columns_sql, columns_args = cur.executed[1]
    assert 'information_schema.tables' in tables_sql
    assert 'TABLE_SCHEMA = DATABASE()' in tables_sql
    assert tables_args == ('%order%',)
    assert 'information_schema.columns' in columns_sql
    assert 'COLUMN_COMMENT LIKE' in columns_sql
    assert columns_args == ('%order%', '%order%')


def test_grep_schema_verbose_searches_all_databases():
    cur = SequenceCursor([
        {'description': [('TABLE_SCHEMA',)], 'rows': []},
        {'description': [('TABLE_SCHEMA',)], 'rows': []},
    ])

    results = grep_schema(cur, arg='order', command_verbosity=True)

    assert 'DATABASE()' not in cur.executed[0][0]
    assert 'DATABASE()' not in cur.executed[1][0]
    assert len(results) == 1
    assert results[0].status == "No schema objects matching 'order'."


def test_grep_schema_requires_pattern():
    cur = SequenceCursor([])
    results = grep_schema(cur, arg='')
    assert results[0].status.startswith('Usage:')
    assert cur.executed == []


# ---------------------------------------------------------------------------
# \dgrep
# ---------------------------------------------------------------------------


def test_grep_data_groups_matches_by_table_and_limits():
    cur = SequenceCursor([
        {'rows': [('app',)]},  # SELECT DATABASE()
        {'rows': [('users', 'name', 'varchar'), ('users', 'email', 'varchar'), ('orders', 'note', 'text')]},  # columns
        {  # users matches
            'description': [('id',), ('name',), ('email',)],
            'rows': [(1, 'Alice', 'a@x'), (2, 'alice2', 'b@x')],
        },
        {  # orders no match
            'description': [('id',), ('note',)],
            'rows': [],
        },
    ])

    results = grep_data(cur, arg='ali')

    assert len(results) == 1
    assert results[0].preamble == 'users (2)'
    assert results[0].header == ['id', 'name', 'email']
    assert results[0].rows == [(1, 'Alice', 'a@x'), (2, 'alice2', 'b@x')]

    users_sql, users_args = cur.executed[2]
    orders_sql, _ = cur.executed[3]
    assert users_sql == 'SELECT * FROM `users` WHERE `name` LIKE %s OR `email` LIKE %s LIMIT 100'
    assert users_args == ('%ali%', '%ali%')
    assert orders_sql == 'SELECT * FROM `orders` WHERE `note` LIKE %s LIMIT 100'


def test_grep_data_verbose_removes_limit():
    cur = SequenceCursor([
        {'rows': [('app',)]},
        {'rows': [('users', 'name', 'varchar')]},
        {'description': [('id',), ('name',)], 'rows': [(1, 'Alice')]},
    ])

    grep_data(cur, arg='ali', command_verbosity=True)

    users_sql = cur.executed[2][0]
    assert users_sql == 'SELECT * FROM `users` WHERE `name` LIKE %s'
    assert 'LIMIT' not in users_sql


def test_grep_data_skips_unreadable_tables():
    cur = SequenceCursor([
        {'rows': [('app',)]},
        {'rows': [('broken_view', 'col', 'varchar'), ('users', 'name', 'varchar')]},
        {'raise': ProgrammingError()},  # broken_view fails — should be skipped
        {'description': [('id',), ('name',)], 'rows': [(1, 'Alice')]},  # users matches
    ])

    results = grep_data(cur, arg='ali')

    assert len(results) == 1
    assert results[0].preamble == 'users (1)'


def test_grep_data_without_database_returns_status():
    cur = SequenceCursor([{'rows': []}])  # SELECT DATABASE() -> no current db
    results = grep_data(cur, arg='ali')
    assert results[0].status.startswith('No database selected')
    assert len(cur.executed) == 1


def test_grep_data_no_matches_returns_status():
    cur = SequenceCursor([
        {'rows': [('app',)]},
        {'rows': [('users', 'name', 'varchar')]},
        {'description': [('id',), ('name',)], 'rows': []},
    ])
    results = grep_data(cur, arg='zzz')
    assert results[0].status == "No matches for 'zzz' in `app`."


def test_grep_data_requires_pattern():
    cur = SequenceCursor([])
    results = grep_data(cur, arg='')
    assert results[0].status.startswith('Usage:')
    assert cur.executed == []


def test_grep_data_default_scope_queries_text_types_only():
    cur = SequenceCursor([
        {'rows': [('app',)]},
        {'rows': []},  # no in-scope columns
    ])
    grep_data(cur, arg='ali')
    _, columns_args = cur.executed[1]
    assert columns_args == ('app', *GREP_TEXT_DATA_TYPES)


def test_grep_data_numeric_flag_casts_numeric_columns():
    cur = SequenceCursor([
        {'rows': [('app',)]},  # SELECT DATABASE()
        {'rows': [('events', 'id', 'int'), ('events', 'code', 'varchar')]},  # columns in -n scope
        {'description': [('id',), ('code',)], 'rows': [(100, 'x')]},
    ])

    results = grep_data(cur, arg='-n 100')

    # The -n flag is stripped from the pattern and widens the column scope to numeric types.
    _, columns_args = cur.executed[1]
    assert columns_args == ('app', *(GREP_TEXT_DATA_TYPES + GREP_NUMERIC_DATA_TYPES))
    events_sql, events_args = cur.executed[2]
    assert events_sql == 'SELECT * FROM `events` WHERE CAST(`id` AS CHAR) LIKE %s OR `code` LIKE %s LIMIT 100'
    assert events_args == ('%100%', '%100%')
    assert results[0].preamble == 'events (1)'


def test_grep_data_all_flag_casts_temporal_and_json():
    cur = SequenceCursor([
        {'rows': [('app',)]},
        {'rows': [('events', 'note', 'text'), ('events', 'created_at', 'datetime'), ('events', 'meta', 'json')]},
        {'description': [('note',), ('created_at',), ('meta',)], 'rows': [('x', '2026-06-16', '{}')]},
    ])

    grep_data(cur, arg='-a 2026-06')

    events_sql, events_args = cur.executed[2]
    assert events_sql == (
        'SELECT * FROM `events` WHERE `note` LIKE %s OR CAST(`created_at` AS CHAR) LIKE %s OR CAST(`meta` AS CHAR) LIKE %s LIMIT 100'
    )
    assert events_args == ('%2026-06%', '%2026-06%', '%2026-06%')


def test_grep_data_flag_without_pattern_returns_usage():
    cur = SequenceCursor([])
    results = grep_data(cur, arg='-n')
    assert results[0].status.startswith('Usage:')
    assert cur.executed == []
