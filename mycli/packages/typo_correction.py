"""Offline did-you-mean corrections for typo'd identifiers and keywords.

Pure decision logic: callers split the input with the execution path's
delimiter-aware splitter, collect candidate names from the completer metadata,
and handle echoing/prefilling. Every function stays silent (returns None)
rather than propose a low-confidence correction.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import re
from typing import Literal

from pymysql.constants.ER import BAD_FIELD_ERROR, NO_SUCH_TABLE, PARSE_ERROR
from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein
import sqlglot

# MySQL truncates the near-fragment of a parse error around this length; a
# fragment this long may end mid-word, so its last word is never a suspect.
# The exact server limit is unverified; erring low only skips one tail word.
TRUNCATED_FRAGMENT_LENGTH = 80
# 1064 hints show the whole corrected statement up to this length, word pairs beyond it.
DISPLAY_STATEMENT_LIMIT = 120
# Words shorter than this are never keyword-correction attempts: a one-edit
# match on a two-letter word changes half of it, which is noise, not a typo fix.
MIN_KEYWORD_TYPO_LENGTH = 3

_UNKNOWN_COLUMN_RE = re.compile(r"^Unknown column '(?P<ident>.+)' in '[^']+'$")
_NO_SUCH_TABLE_RE = re.compile(r"^Table '(?P<schema>[^'.]+)\.(?P<table>[^']+)' doesn't exist$")
_SYNTAX_NEAR_RE = re.compile(r"near '(?P<fragment>.*)' at line \d+$", re.DOTALL)

_IDENTIFIER_RE = re.compile(r'[0-9A-Za-z_$]+')
# Alternatives that consume single- and double-quoted string literals whole, so
# their contents never look like words to correct or occurrences to rewrite.
_STRING_LITERALS = r"'(?:[^'\\]|\\.|'')*'|" r'"(?:[^"\\]|\\.|"")*"'
# Word-level scan that additionally consumes backticked names whole.
_SQL_WORD_RE = re.compile(
    rf"""{_STRING_LITERALS}
       |`[^`]*`
       |(?P<word>[0-9A-Za-z_$]+)""",
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class UnknownIdentifier:
    kind: Literal['column', 'table']
    name: str
    qualifier: str | None


@dataclass(frozen=True, slots=True)
class SyntaxFragment:
    fragment: str


@dataclass(frozen=True, slots=True)
class TypoSuggestion:
    display: str
    corrected_sql: str | None


def parse_error(errno: int, message: str) -> UnknownIdentifier | SyntaxFragment | None:
    """Extract the misspelled identifier or syntax fragment from a server error message.

    Only the MySQL/MariaDB message formats are recognized; dialect variants
    (e.g. TiDB's 1064 wording) fall through to silence.
    """
    if errno == BAD_FIELD_ERROR:
        column_match = _UNKNOWN_COLUMN_RE.match(message)
        if column_match is None:
            return None
        qualifier, _, name = column_match.group('ident').rpartition('.')
        return UnknownIdentifier('column', name, qualifier or None)
    if errno == NO_SUCH_TABLE:
        table_match = _NO_SUCH_TABLE_RE.match(message)
        if table_match is None:
            return None
        return UnknownIdentifier('table', table_match.group('table'), table_match.group('schema'))
    if errno == PARSE_ERROR:
        syntax_match = _SYNTAX_NEAR_RE.search(message)
        if syntax_match is None or not syntax_match.group('fragment'):
            return None
        return SyntaxFragment(syntax_match.group('fragment'))
    return None


def qualifier_matches(qualifier: str, table: tuple[str | None, str, str | None]) -> bool:
    """Does a column qualifier refer to this (schema, table, alias) tuple?"""
    schema, relname, alias = table
    wanted = qualifier.lower()
    if alias and wanted == alias.lower():
        return True
    if wanted == relname.lower():
        return True
    return bool(schema) and wanted == f'{schema}.{relname}'.lower()


def _normalize(name: str) -> str:
    return name.strip('`').lower()


def _closest_unique(word: str, choices: Sequence[str], max_distance: int, processor: Callable[[str], str]) -> str | None:
    """The choice within max_distance edits of word, or None when there is no
    such choice or the two best choices tie (a low-confidence match)."""
    matches = process.extract(word, choices, scorer=DamerauLevenshtein.distance, processor=processor, score_cutoff=max_distance, limit=2)
    if not matches or (len(matches) > 1 and matches[0][1] == matches[1][1]):
        return None
    return matches[0][0]


def find_suggestion(misspelled: str, candidates: Sequence[str], allow_case_fix: bool = False) -> str | None:
    """The single close-enough candidate for a misspelled identifier, or None.

    Candidates are deduplicated by normalized name, so the same name arriving
    from several sources (two tables, column vs alias, backticked storage form)
    never fakes a tie. A case-only difference counts as a fix only when
    allow_case_fix is set: table names are case-sensitive on
    lower_case_table_names=0 servers, column names never are.
    """
    unique: dict[str, str] = {}
    for candidate in candidates:
        unique.setdefault(_normalize(candidate), candidate)
    if not unique:
        return None
    max_distance = 1 if len(misspelled) <= 4 else 2
    best = _closest_unique(misspelled, list(unique.values()), max_distance, _normalize)
    if best is None:
        return None
    if allow_case_fix:
        if best.strip('`') == misspelled:
            return None
    elif _normalize(best) == _normalize(misspelled):
        return None
    return best


def extract_select_aliases(statement: str) -> list[str]:
    """Column aliases (AS and bare) defined by the statement's SELECT list."""
    try:
        parsed = sqlglot.parse_one(statement, read='mysql')
    except sqlglot.errors.SqlglotError:
        # covers TokenError too (e.g. an unclosed backtick), not just ParseError
        return []
    return [node.alias for node in parsed.find_all(sqlglot.exp.Alias) if node.alias]


def correct_keywords(
    statement: str,
    fragment: str,
    keyword_words: frozenset[str],
    known_identifiers: frozenset[str],
) -> tuple[str, dict[str, str]] | None:
    """Fix misspelled keywords in the leading run of a syntax-error fragment.

    The fragment starts at the parse failure, so only its leading words are
    suspects; scanning stops at the first word that is neither a keyword nor an
    unambiguous distance-1 keyword typo (identifiers, numbers, real names,
    words shorter than MIN_KEYWORD_TYPO_LENGTH). Returns the corrected
    statement with the ordered replacement pairs.
    """
    words = [match.group('word') for match in _SQL_WORD_RE.finditer(fragment) if match.group('word') is not None]
    if len(fragment) >= TRUNCATED_FRAGMENT_LENGTH and words:
        words = words[:-1]
    keyword_choices = sorted(keyword_words)
    replacements: dict[str, str] = {}
    for word in words:
        if word.upper() in keyword_words or word in replacements:
            continue
        if word.lower() in known_identifiers or len(word) < MIN_KEYWORD_TYPO_LENGTH:
            break
        keyword = _closest_unique(word, keyword_choices, 1, str.lower)
        if keyword is None:
            break
        replacements[word] = keyword.lower() if word.islower() else keyword
    if not replacements:
        return None
    corrected = statement
    for word, replacement in replacements.items():
        rewritten = rewrite_sql(corrected, word, replacement)
        if rewritten is None:
            return None
        corrected = rewritten
    return corrected, replacements


def locate_failing_statement(statements: Sequence[str], needle: str) -> int | None:
    """Index of the statement containing the needle, or None when it appears
    in zero or several statements and the failure cannot be pinned down."""
    if not statements:
        return None
    if len(statements) == 1:
        return 0
    if _IDENTIFIER_RE.fullmatch(needle):
        finder = re.compile(rf'(?<![0-9A-Za-z_$`]){re.escape(needle)}(?![0-9A-Za-z_$`])', re.IGNORECASE)
        hits = [index for index, statement in enumerate(statements) if finder.search(statement)]
    else:
        hits = [index for index, statement in enumerate(statements) if needle.lower() in statement.lower()]
    if len(hits) != 1:
        return None
    return hits[0]


def rewrite_sql(sql: str, misspelled: str, replacement: str) -> str | None:
    """Replace every word-boundary occurrence of misspelled outside string literals.

    Backticked occurrences keep their backticks; bare occurrences take the
    replacement's stored form (which carries backticks for reserved names).
    Returns None when nothing was replaced.
    """
    bare_replacement = replacement.strip('`')
    pattern = re.compile(
        rf"""{_STRING_LITERALS}
           |(?P<quoted>`{re.escape(misspelled)}`)
           |(?P<bare>(?<![0-9A-Za-z_$`]){re.escape(misspelled)}(?![0-9A-Za-z_$`]))""",
        re.IGNORECASE | re.VERBOSE,
    )
    count = 0

    def substitute(match: re.Match[str]) -> str:
        nonlocal count
        if match.group('quoted') is None and match.group('bare') is None:
            return match.group(0)
        count += 1
        if match.group('quoted') is not None:
            return f'`{bare_replacement}`'
        return replacement

    corrected = pattern.sub(substitute, sql)
    return corrected if count else None


def _prefill(statements: Sequence[str], failing_index: int, corrected: str, delimiter: str) -> str:
    # Statements before the failing one already executed; never replay them.
    # The delimiter only separates statements: appending one to the last would
    # corrupt trailing suffixes such as \G or an output redirect's file name.
    return f'{delimiter} '.join([corrected, *statements[failing_index + 1 :]])


def suggest_identifier_correction(
    parsed: UnknownIdentifier,
    statements: Sequence[str],
    delimiter: str,
    candidates: Sequence[str],
) -> TypoSuggestion | None:
    failing_index = locate_failing_statement(statements, parsed.name)
    all_candidates = list(candidates)
    if parsed.kind == 'column' and parsed.qualifier is None and failing_index is not None:
        all_candidates += extract_select_aliases(statements[failing_index])
    suggestion = find_suggestion(parsed.name, all_candidates, allow_case_fix=parsed.kind == 'table')
    if suggestion is None:
        return None
    if failing_index is None:
        return TypoSuggestion(suggestion, None)
    corrected = rewrite_sql(statements[failing_index], parsed.name, suggestion)
    if corrected is None:
        return None
    return TypoSuggestion(suggestion, _prefill(statements, failing_index, corrected, delimiter))


def suggest_keyword_correction(
    parsed: SyntaxFragment,
    statements: Sequence[str],
    delimiter: str,
    keyword_words: frozenset[str],
    known_identifiers: frozenset[str],
) -> TypoSuggestion | None:
    failing_index = locate_failing_statement(statements, parsed.fragment)
    if failing_index is None:
        return None
    correction = correct_keywords(statements[failing_index], parsed.fragment, keyword_words, known_identifiers)
    if correction is None:
        return None
    corrected, replacements = correction
    if len(corrected) <= DISPLAY_STATEMENT_LIMIT:
        display = corrected
    else:
        display = ', '.join(f'{word} → {replacement}' for word, replacement in replacements.items())
    return TypoSuggestion(display, _prefill(statements, failing_index, corrected, delimiter))
