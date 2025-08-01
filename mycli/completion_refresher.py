from __future__ import annotations

import threading
from typing import Callable

from mycli.packages.special.main import COMMANDS
from mycli.sqlcompleter import SQLCompleter
from mycli.sqlexecute import ServerSpecies, SQLExecute


class CompletionRefresher:
    refreshers: dict = {}

    def __init__(self) -> None:
        self._completer_thread: threading.Thread | None = None
        self._restart_refresh = threading.Event()

    def refresh(
        self,
        executor: SQLExecute,
        callbacks: Callable | list[Callable],
        completer_options: dict | None = None,
    ) -> list[tuple]:
        """Creates a SQLCompleter object and populates it with the relevant
        completion suggestions in a background thread.

        executor - SQLExecute object, used to extract the credentials to connect
                   to the database.
        callbacks - A function or a list of functions to call after the thread
                    has completed the refresh. The newly created completion
                    object will be passed in as an argument to each callback.
        completer_options - dict of options to pass to SQLCompleter.

        """
        if completer_options is None:
            completer_options = {}

        if self.is_refreshing():
            self._restart_refresh.set()
            return [(None, None, None, "Auto-completion refresh restarted.")]
        else:
            self._completer_thread = threading.Thread(
                target=self._bg_refresh, args=(executor, callbacks, completer_options), name="completion_refresh"
            )
            self._completer_thread.daemon = True
            self._completer_thread.start()
            return [(None, None, None, "Auto-completion refresh started in the background.")]

    def is_refreshing(self) -> bool:
        return bool(self._completer_thread and self._completer_thread.is_alive())

    def _bg_refresh(
        self,
        sqlexecute: SQLExecute,
        callbacks: Callable | list[Callable],
        completer_options: dict,
    ) -> None:
        completer = SQLCompleter(**completer_options)

        # Create a new sqlexecute method to populate the completions.
        e = sqlexecute
        executor = SQLExecute(
            e.dbname,
            e.user,
            e.password,
            e.host,
            e.port,
            e.socket,
            e.charset,
            e.local_infile,
            e.ssl,
            e.ssh_user,
            e.ssh_host,
            e.ssh_port,
            e.ssh_password,
            e.ssh_key_filename,
        )

        # If callbacks is a single function then push it into a list.
        if callable(callbacks):
            callbacks = [callbacks]

        while 1:
            for refresher in self.refreshers.values():
                refresher(completer, executor)
                if self._restart_refresh.is_set():
                    self._restart_refresh.clear()
                    break
            else:
                # Break out of while loop if the for loop finishes natually
                # without hitting the break statement.
                break

            # Start over the refresh from the beginning if the for loop hit the
            # break statement.
            continue

        for callback in callbacks:
            callback(completer)


def refresher(name: str, refreshers: dict = CompletionRefresher.refreshers) -> Callable:
    """Decorator to add the decorated function to the dictionary of
    refreshers. Any function decorated with a @refresher will be executed as
    part of the completion refresh routine."""

    def wrapper(wrapped):
        refreshers[name] = wrapped
        return wrapped

    return wrapper


@refresher("databases")
def refresh_databases(completer: SQLCompleter, executor: SQLExecute) -> None:
    completer.extend_database_names(executor.databases())


@refresher("schemata")
def refresh_schemata(completer: SQLCompleter, executor: SQLExecute) -> None:
    # schemata - In MySQL Schema is the same as database. But for mycli
    # schemata will be the name of the current database.
    completer.extend_schemata(executor.dbname)
    completer.set_dbname(executor.dbname)


@refresher("tables")
def refresh_tables(completer: SQLCompleter, executor: SQLExecute) -> None:
    table_columns_dbresult = list(executor.table_columns())
    completer.extend_relations(table_columns_dbresult, kind="tables")
    completer.extend_columns(table_columns_dbresult, kind="tables")


@refresher("users")
def refresh_users(completer: SQLCompleter, executor: SQLExecute) -> None:
    completer.extend_users(executor.users())


# @refresher('views')
# def refresh_views(completer: SQLCompleter, executor: SQLExecute) -> None:
#     completer.extend_relations(executor.views(), kind='views')
#     completer.extend_columns(executor.view_columns(), kind='views')


@refresher("functions")
def refresh_functions(completer: SQLCompleter, executor: SQLExecute) -> None:
    completer.extend_functions(executor.functions())
    if executor.server_info and executor.server_info.species == ServerSpecies.TiDB:
        completer.extend_functions(completer.tidb_functions, builtin=True)


@refresher("special_commands")
def refresh_special(completer: SQLCompleter, executor: SQLExecute) -> None:
    completer.extend_special_commands(list(COMMANDS.keys()))


@refresher("show_commands")
def refresh_show_commands(completer: SQLCompleter, executor: SQLExecute) -> None:
    completer.extend_show_items(executor.show_candidates())


@refresher("keywords")
def refresh_keywords(completer: SQLCompleter, executor: SQLExecute) -> None:
    if executor.server_info and executor.server_info.species == ServerSpecies.TiDB:
        completer.extend_keywords(completer.tidb_keywords, replace=True)
