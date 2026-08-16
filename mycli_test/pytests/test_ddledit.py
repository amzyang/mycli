"""Tests for the \\ed command: edit table DDL in an editor, diff via mysqldef."""

from __future__ import annotations

import pytest

from mycli.packages.special import ddledit


class FakeCursor:
    def __init__(self, ddl: str) -> None:
        self.ddl = ddl
        self.executed: str | None = None

    def execute(self, query: str) -> None:
        self.executed = query

    def fetchone(self) -> tuple[str, str]:
        return ('users', self.ddl)


CURRENT_DDL = "CREATE TABLE `users` (\n  `id` int NOT NULL,\n  PRIMARY KEY (`id`)\n)"
EDITED_DDL = "CREATE TABLE `users` (\n  `id` int NOT NULL COMMENT 'user id',\n  PRIMARY KEY (`id`)\n)"
ALTER_SQL = "ALTER TABLE `users` CHANGE COLUMN `id` `id` int NOT NULL COMMENT 'user id';"


@pytest.mark.parametrize(
    'command,expected',
    [
        ('\\ed users', True),
        ('/ed users', True),
        ('\\ed', True),
        ('\\ed db.users;', True),
        ('\\e users', False),
        ('\\edit users', False),
        ('select * from users \\e', False),
        ('select 1', False),
    ],
)
def test_is_ddl_edit_command(command, expected):
    assert ddledit.is_ddl_edit_command(command) is expected


@pytest.mark.parametrize(
    'name,expected',
    [
        ('users', '`users`'),
        ('mydb.users', '`mydb`.`users`'),
        ('weird`name', '`weird``name`'),
    ],
)
def test_quote_identifier(name, expected):
    assert ddledit.quote_identifier(name) == expected


def test_extract_statements_filters_comment_lines():
    output = "-- dry run --\nALTER TABLE `users` ADD COLUMN `age` int;\n\n-- trailing note --\n"
    assert ddledit.extract_statements(output) == "ALTER TABLE `users` ADD COLUMN `age` int;"


def test_extract_statements_empty_when_nothing_modified():
    assert ddledit.extract_statements("-- Nothing is modified --\n") == ''
    assert ddledit.extract_statements('') == ''


def test_fetch_create_table_quotes_identifier():
    cur = FakeCursor(CURRENT_DDL)
    ddl = ddledit.fetch_create_table(cur, 'mydb.users')
    assert cur.executed == 'SHOW CREATE TABLE `mydb`.`users`'
    assert ddl == CURRENT_DDL


def test_handle_ddl_edit_without_table_returns_usage():
    sql, message = ddledit.handle_ddl_edit(FakeCursor(CURRENT_DDL), '\\ed')
    assert sql is None
    assert message == ddledit.USAGE


def test_handle_ddl_edit_editor_cancelled(monkeypatch):
    monkeypatch.setattr(ddledit.click, 'edit', lambda *a, **kw: None)
    sql, message = ddledit.handle_ddl_edit(FakeCursor(CURRENT_DDL), '\\ed users')
    assert sql is None
    assert message == 'DDL edit cancelled.'


def test_handle_ddl_edit_no_changes(monkeypatch):
    monkeypatch.setattr(ddledit.click, 'edit', lambda *a, **kw: CURRENT_DDL)
    sql, message = ddledit.handle_ddl_edit(
        FakeCursor(CURRENT_DDL),
        '\\ed users',
        runner=lambda current, desired: '-- Nothing is modified --\n',
    )
    assert sql is None
    assert message == 'No schema changes detected.'


def test_handle_ddl_edit_returns_alter(monkeypatch):
    monkeypatch.setattr(ddledit.click, 'edit', lambda *a, **kw: EDITED_DDL)
    seen = {}

    def runner(current: str, desired: str) -> str:
        seen['current'] = current
        seen['desired'] = desired
        return f"-- dry run --\n{ALTER_SQL}\n"

    sql, message = ddledit.handle_ddl_edit(FakeCursor(CURRENT_DDL), '\\ed users', runner=runner)
    assert sql == ALTER_SQL
    assert message is None
    assert seen['current'] == CURRENT_DDL
    assert seen['desired'] == EDITED_DDL


def test_run_mysqldef_missing_binary(monkeypatch):
    monkeypatch.setattr(ddledit.shutil, 'which', lambda name: None)
    with pytest.raises(RuntimeError, match='brew install sqldef/sqldef/mysqldef'):
        ddledit.run_mysqldef(CURRENT_DDL, EDITED_DDL)
