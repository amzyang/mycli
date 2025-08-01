import logging

from prompt_toolkit.enums import EditingMode
from prompt_toolkit.filters import completion_is_selected, emacs_mode
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.filters.cli import ViInsertMode

from mycli.packages import shortcuts
from mycli.packages.toolkit.fzf import search_history

_logger = logging.getLogger(__name__)


def mycli_bindings(mycli) -> KeyBindings:
    """Custom key bindings for mycli."""
    kb = KeyBindings()
    insert_mode = ViInsertMode()

    @kb.add("f2")
    def _(_event: KeyPressEvent) -> None:
        """Enable/Disable SmartCompletion Mode."""
        _logger.debug("Detected F2 key.")
        mycli.completer.smart_completion = not mycli.completer.smart_completion

    @kb.add("f3")
    def _(_event: KeyPressEvent) -> None:
        """Enable/Disable Multiline Mode."""
        _logger.debug("Detected F3 key.")
        mycli.multi_line = not mycli.multi_line

    @kb.add("f4")
    def _(event: KeyPressEvent) -> None:
        """Toggle between Vi and Emacs mode."""
        _logger.debug("Detected F4 key.")
        if mycli.key_bindings == "vi":
            event.app.editing_mode = EditingMode.EMACS
            mycli.key_bindings = "emacs"
        else:
            event.app.editing_mode = EditingMode.VI
            mycli.key_bindings = "vi"

    @kb.add("tab")
    def _(event: KeyPressEvent) -> None:
        """Force autocompletion at cursor."""
        _logger.debug("Detected <Tab> key.")
        b = event.app.current_buffer
        if b.complete_state:
            b.complete_next()
        else:
            b.start_completion(select_first=True)

    @kb.add("c-space")
    def _(event: KeyPressEvent) -> None:
        """
        Initialize autocompletion at cursor.

        If the autocompletion menu is not showing, display it with the
        appropriate completions for the context.

        If the menu is showing, select the next completion.
        """
        _logger.debug("Detected <C-Space> key.")

        b = event.app.current_buffer
        if b.complete_state:
            b.complete_next()
        else:
            b.start_completion(select_first=False)

    @kb.add("c-x", "p", filter=emacs_mode)
    def _(event: KeyPressEvent) -> None:
        """
        Prettify and indent current statement, usually into multiple lines.

        Only accepts buffers containing single SQL statements.
        """
        _logger.debug("Detected <C-x p>/> key.")

        b = event.app.current_buffer
        cursorpos_relative = b.cursor_position / max(1, len(b.text))
        pretty_text = mycli.handle_prettify_binding(b.text)
        if len(pretty_text) > 0:
            b.text = pretty_text
            cursorpos_abs = int(round(cursorpos_relative * len(b.text)))
            while 0 < cursorpos_abs < len(b.text) and b.text[cursorpos_abs] in (" ", "\n"):
                cursorpos_abs -= 1
            b.cursor_position = min(cursorpos_abs, len(b.text))

    @kb.add("c-x", "u", filter=emacs_mode)
    def _(event: KeyPressEvent) -> None:
        """
        Unprettify and dedent current statement, usually into one line.

        Only accepts buffers containing single SQL statements.
        """
        _logger.debug("Detected <C-x u>/< key.")

        b = event.app.current_buffer
        cursorpos_relative = b.cursor_position / max(1, len(b.text))
        unpretty_text = mycli.handle_unprettify_binding(b.text)
        if len(unpretty_text) > 0:
            b.text = unpretty_text
            cursorpos_abs = int(round(cursorpos_relative * len(b.text)))
            while 0 < cursorpos_abs < len(b.text) and b.text[cursorpos_abs] in (" ", "\n"):
                cursorpos_abs -= 1
            b.cursor_position = min(cursorpos_abs, len(b.text))

    @kb.add("c-o", "d", filter=emacs_mode)
    def _(event: KeyPressEvent) -> None:
        """
        Insert the current date.
        """
        _logger.debug("Detected <C-o d> key.")

        event.app.current_buffer.insert_text(shortcuts.server_date(mycli.sqlexecute))

    @kb.add("c-o", "c-d", filter=emacs_mode)
    def _(event: KeyPressEvent) -> None:
        """
        Insert the quoted current date.
        """
        _logger.debug("Detected <C-o C-d> key.")

        event.app.current_buffer.insert_text(shortcuts.server_date(mycli.sqlexecute, quoted=True))

    @kb.add("c-o", "t", filter=emacs_mode)
    def _(event: KeyPressEvent) -> None:
        """
        Insert the current datetime.
        """
        _logger.debug("Detected <C-o t> key.")

        event.app.current_buffer.insert_text(shortcuts.server_datetime(mycli.sqlexecute))

    @kb.add("c-o", "c-t", filter=emacs_mode)
    def _(event: KeyPressEvent) -> None:
        """
        Insert the quoted current datetime.
        """
        _logger.debug("Detected <C-o C-t> key.")

        event.app.current_buffer.insert_text(shortcuts.server_datetime(mycli.sqlexecute, quoted=True))

    @kb.add("c-r", filter=emacs_mode)
    def _(event: KeyPressEvent) -> None:
        """Search history using fzf or reverse incremental search."""
        _logger.debug("Detected <C-r> key.")
        mode = mycli.config.get('keys', {}).get('control_r', 'auto')
        if mode == 'reverse_isearch':
            search_history(event, incremental=True)
        else:
            search_history(event)

    @kb.add("escape", "r", filter=emacs_mode)
    def _(event: KeyPressEvent) -> None:
        """Search history using fzf when available."""
        _logger.debug("Detected <alt-r> key.")
        search_history(event)

    @kb.add("enter", filter=completion_is_selected)
    def _(event: KeyPressEvent) -> None:
        """Makes the enter key work as the tab key only when showing the menu.

        In other words, don't execute query when enter is pressed in
        the completion dropdown menu, instead close the dropdown menu
        (accept current selection).

        """
        _logger.debug("Detected enter key.")

        event.current_buffer.complete_state = None
        b = event.app.current_buffer
        b.complete_state = None

    @kb.add("escape", "enter")
    def _(event: KeyPressEvent) -> None:
        """Introduces a line break in multi-line mode, or dispatches the
        command in single-line mode."""
        _logger.debug("Detected alt-enter key.")
        if mycli.multi_line:
            event.app.current_buffer.validate_and_handle()
        else:
            event.app.current_buffer.insert_text("\n")

    @kb.add('c-p', filter=insert_mode)
    def _(event):
        b = event.current_buffer

        if b.complete_state:
            b.complete_previous()
        else:
            event.current_buffer.auto_up(count=event.arg)

    @kb.add('c-n', filter=insert_mode)
    def _(event):
        b = event.current_buffer

        if b.complete_state:
            b.complete_next()
        else:
            event.current_buffer.auto_down(count=event.arg)

    return kb
