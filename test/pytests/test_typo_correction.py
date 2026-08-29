# type: ignore

import pytest

from mycli.packages.typo_correction import (
    SyntaxFragment,
    UnknownIdentifier,
    correct_keywords,
    extract_select_aliases,
    find_suggestion,
    locate_failing_statement,
    parse_error,
    qualifier_matches,
    rewrite_sql,
    suggest_identifier_correction,
    suggest_keyword_correction,
)

KEYWORD_WORDS = frozenset({'SELECT', 'FROM', 'WHERE', 'SHOW', 'CREATE', 'TABLE', 'GROUP', 'BY', 'ORDER', 'LIMIT', 'COUNT', 'CONCAT'})


@pytest.mark.parametrize(
    ('errno', 'message', 'expected'),
    [
        (1054, "Unknown column 'usr_nam' in 'field list'", UnknownIdentifier('column', 'usr_nam', None)),
        (1054, "Unknown column 'usr_nam' in 'where clause'", UnknownIdentifier('column', 'usr_nam', None)),
        (1054, "Unknown column 't.usr_nam' in 'order clause'", UnknownIdentifier('column', 'usr_nam', 't')),
        (1054, "Unknown column 'db.t.usr_nam' in 'field list'", UnknownIdentifier('column', 'usr_nam', 'db.t')),
        (1146, "Table 'mydb.userz' doesn't exist", UnknownIdentifier('table', 'userz', 'mydb')),
        (
            1064,
            "You have an error in your SQL syntax; check the manual that corresponds to your MySQL server "
            "version for the right syntax to use near 'selet 1' at line 1",
            SyntaxFragment('selet 1'),
        ),
        (
            1064,
            "You have an error in your SQL syntax; check the manual that corresponds to your MySQL server "
            "version for the right syntax to use near 'wher name = 'foo'' at line 1",
            SyntaxFragment("wher name = 'foo'"),
        ),
        (1064, "You have an error in your SQL syntax; ... for the right syntax to use near '' at line 1", None),
        (1064, 'You have a syntax error: line 1 column 6 near "selet 1" ', None),  # TiDB variant
        (1054, 'totally different message', None),
        (1146, "Table 'userz' doesn't exist", None),
        (9999, "Unknown column 'usr_nam' in 'field list'", None),
    ],
)
def test_parse_error(errno, message, expected):
    assert parse_error(errno, message) == expected


@pytest.mark.parametrize(
    ('misspelled', 'candidates', 'expected'),
    [
        ('userz', ['users', 'orders'], 'users'),  # substitution
        ('userss', ['users', 'orders'], 'users'),  # deletion
        ('usrs', ['users', 'orders'], 'users'),  # insertion, len 4 -> cutoff 1
        ('usres', ['users', 'orders'], 'users'),  # transposition counts as 1
        ('usr_nam', ['user_name', 'user_id'], 'user_name'),  # two edits on a long name
        ('idd', ['id', 'ids'], None),  # tie between two candidates
        ('idd', ['id'], 'id'),
        ('nm', ['name'], None),  # short name: distance 2 over cutoff 1
        ('userz', ['users', 'userx'], None),  # tie
        ('users', ['users', 'orders'], None),  # identical to a candidate
        ('USERS', ['users'], None),  # case-only difference is silent by default (columns)
        ('zzzzzz', ['users'], None),  # beyond cutoff
        ('userz', [], None),
        ('ordr', ['`order`'], '`order`'),  # backticked storage form
        ('userz', ['users', '`users`'], 'users'),  # normalized dedup: same name twice is no tie
        ('totl', ['total', 'total'], 'total'),  # column and alias with the same name merge
    ],
)
def test_find_suggestion(misspelled, candidates, expected):
    assert find_suggestion(misspelled, candidates) == expected


def test_find_suggestion_allows_case_fix_for_tables():
    assert find_suggestion('USERS', ['users'], allow_case_fix=True) == 'users'
    assert find_suggestion('users', ['users'], allow_case_fix=True) is None  # raw-identical stays silent


@pytest.mark.parametrize(
    ('statement', 'expected'),
    [
        ('select price*qty as total, name nm from t', ['total', 'nm']),
        ('select a, b from t', []),
        ('selet >>> broken', []),  # parse failure
        ('select usr_nam from `broken', []),  # unclosed backtick raises TokenError, not ParseError
    ],
)
def test_extract_select_aliases(statement, expected):
    assert extract_select_aliases(statement) == expected


def test_suggest_identifier_correction_survives_untokenizable_statement():
    parsed = UnknownIdentifier('column', 'usr_nam', None)
    suggestion = suggest_identifier_correction(parsed, ['select `usr_nam` from users', 'select usr_nam from `broken'], ';', ['user_name'])
    assert suggestion.display == 'user_name'


@pytest.mark.parametrize(
    ('statement', 'fragment', 'known', 'expected'),
    [
        ('selet 1', 'selet 1', frozenset(), ('select 1', {'selet': 'select'})),
        ('show createe tabel abc', 'createe tabel abc', frozenset(), ('show create table abc', {'createe': 'create', 'tabel': 'table'})),
        # a word that names a cached identifier is never treated as a keyword typo
        ('select * from tabel', 'tabel', frozenset({'tabel'}), None),
        # already-valid syntax words offer nothing to fix
        ('select 1', 'select 1', frozenset(), None),
        ('slct 1', 'slct 1', frozenset(), None),  # distance 2 from SELECT, over cutoff
        ('SELET 1', 'SELET 1', frozenset(), ('SELECT 1', {'SELET': 'SELECT'})),  # non-lowercase word takes stored casing
        # scanning stops at the first uncorrectable word: 'name' stays untouched
        (
            "select * from t wher name = 'x'",
            "wher name = 'x'",
            frozenset(),
            ("select * from t where name = 'x'", {'wher': 'where'}),
        ),
        # words inside string literals are not candidates
        (
            "select * from t wher c = 'selet'",
            "wher c = 'selet'",
            frozenset(),
            ("select * from t where c = 'selet'", {'wher': 'where'}),
        ),
        # function-name typos are correctable once functions join the word set
        ('select cout(1) from t', 'cout(1) from t', frozenset(), ('select count(1) from t', {'cout': 'count'})),
        # words shorter than 3 chars are never correction attempts
        ('group bt x', 'bt x', frozenset(), None),
        # ... but legal short words pass through
        ('group by selet', 'by selet', frozenset(), ('group by select', {'selet': 'select'})),
    ],
)
def test_correct_keywords(statement, fragment, known, expected):
    assert correct_keywords(statement, fragment, KEYWORD_WORDS, known) == expected


def test_correct_keywords_skips_last_word_of_truncated_fragment():
    filler = 'x' * 72
    statement = f'createe {filler}'
    fragment = statement  # len == 80: server truncation limit reached
    assert len(fragment) == 80
    assert correct_keywords(statement, fragment, KEYWORD_WORDS, frozenset()) == (f'create {filler}', {'createe': 'create'})
    # when the possibly-truncated last word is the only suspect, stay silent
    lone = 'y' * 80
    assert correct_keywords(lone, lone, KEYWORD_WORDS, frozenset()) is None


@pytest.mark.parametrize(
    ('statements', 'needle', 'expected'),
    [
        (['select usr_nam from users'], 'usr_nam', 0),
        (['select 1', 'select usr_nam from t'], 'usr_nam', 1),
        (['select usr_nam from a', 'select usr_nam from b'], 'usr_nam', None),  # ambiguous
        (['select usr_nam from a', 'select usr_nam2 from b'], 'usr_nam', 0),  # word boundary
        (['selet 1', 'show createe tabel abc'], 'selet 1', 0),
        (['select 1', 'select 2'], 'usr_nam', None),  # not found anywhere
        ([], 'usr_nam', None),
    ],
)
def test_locate_failing_statement(statements, needle, expected):
    assert locate_failing_statement(statements, needle) == expected


@pytest.mark.parametrize(
    ('sql', 'misspelled', 'replacement', 'expected'),
    [
        ('select usr_nam from users', 'usr_nam', 'user_name', 'select user_name from users'),
        ('select usr_nam from t where usr_nam = 1', 'usr_nam', 'user_name', 'select user_name from t where user_name = 1'),
        ('select `usr_nam` from t', 'usr_nam', 'user_name', 'select `user_name` from t'),
        ('select ordr from t', 'ordr', '`order`', 'select `order` from t'),
        ("select usr_nam from t where c = 'usr_nam'", 'usr_nam', 'user_name', "select user_name from t where c = 'usr_nam'"),
        ('select USR_NAM from t', 'usr_nam', 'user_name', 'select user_name from t'),
        ('select usr_nam2 from t', 'usr_nam', 'user_name', None),  # word boundary
        ('select 1', 'usr_nam', 'user_name', None),  # no occurrence
    ],
)
def test_rewrite_sql(sql, misspelled, replacement, expected):
    assert rewrite_sql(sql, misspelled, replacement) == expected


@pytest.mark.parametrize(
    ('qualifier', 'table', 'expected'),
    [
        ('t', ('mydb', 'users', 't'), True),  # alias
        ('users', ('mydb', 'users', None), True),  # table name
        ('mydb.users', ('mydb', 'users', None), True),  # schema-qualified
        ('other', ('mydb', 'users', 't'), False),
    ],
)
def test_qualifier_matches(qualifier, table, expected):
    assert qualifier_matches(qualifier, table) == expected


def test_suggest_identifier_correction_single_statement():
    parsed = UnknownIdentifier('column', 'usr_nam', None)
    suggestion = suggest_identifier_correction(parsed, ['select usr_nam from users'], ';', ['user_name', 'user_id'])
    assert suggestion.display == 'user_name'
    assert suggestion.corrected_sql == 'select user_name from users'


def test_suggest_identifier_correction_prefills_from_failing_statement_onward():
    parsed = UnknownIdentifier('column', 'usr_nam', None)
    suggestion = suggest_identifier_correction(parsed, ['select 1', 'select usr_nam from users', 'select 2'], ';', ['user_name'])
    assert suggestion.display == 'user_name'
    assert suggestion.corrected_sql == 'select user_name from users; select 2'


def test_suggest_identifier_correction_joins_with_custom_delimiter():
    parsed = UnknownIdentifier('column', 'usr_nam', None)
    suggestion = suggest_identifier_correction(parsed, ['select usr_nam from users', 'select 2'], '$$', ['user_name'])
    assert suggestion.corrected_sql == 'select user_name from users$$ select 2'


def test_suggest_identifier_correction_ambiguous_statement_suggests_without_prefill():
    parsed = UnknownIdentifier('column', 'usr_nam', None)
    suggestion = suggest_identifier_correction(parsed, ['select usr_nam from a', 'select usr_nam from b'], ';', ['user_name'])
    assert suggestion.display == 'user_name'
    assert suggestion.corrected_sql is None


def test_suggest_identifier_correction_identifier_absent_from_sql():
    parsed = UnknownIdentifier('column', 'usr_nam', None)
    assert suggest_identifier_correction(parsed, ['select * from a_view'], ';', ['user_name']) is None


def test_suggest_identifier_correction_without_candidates():
    parsed = UnknownIdentifier('column', 'usr_nam', None)
    assert suggest_identifier_correction(parsed, ['select usr_nam from users'], ';', []) is None


def test_suggest_identifier_correction_uses_select_aliases():
    parsed = UnknownIdentifier('column', 'totl', None)
    suggestion = suggest_identifier_correction(parsed, ['select price*qty as total from t order by totl'], ';', [])
    assert suggestion.display == 'total'
    assert suggestion.corrected_sql == 'select price*qty as total from t order by total'


def test_suggest_identifier_correction_qualified_column_ignores_aliases():
    parsed = UnknownIdentifier('column', 'totl', 't')
    assert suggest_identifier_correction(parsed, ['select price as total from t order by t.totl'], ';', []) is None


def test_suggest_identifier_correction_table_case_only_is_corrected():
    parsed = UnknownIdentifier('table', 'USERS', 'db')
    suggestion = suggest_identifier_correction(parsed, ['select * from USERS'], ';', ['users'])
    assert suggestion.display == 'users'
    assert suggestion.corrected_sql == 'select * from users'


def test_suggest_identifier_correction_column_case_only_stays_silent():
    parsed = UnknownIdentifier('column', 'USER_NAME', None)
    assert suggest_identifier_correction(parsed, ['select USER_NAME from users'], ';', ['user_name']) is None


def test_suggest_keyword_correction_first_statement_prefills_everything():
    suggestion = suggest_keyword_correction(
        SyntaxFragment('selet 1'), ['selet 1', 'show createe tabel abc'], ';', KEYWORD_WORDS, frozenset()
    )
    assert suggestion.display == 'select 1'
    assert suggestion.corrected_sql == 'select 1; show createe tabel abc'


def test_suggest_keyword_correction_later_statement_drops_executed_ones():
    suggestion = suggest_keyword_correction(
        SyntaxFragment('createe tabel abc'), ['select 1', 'show createe tabel abc'], ';', KEYWORD_WORDS, frozenset()
    )
    assert suggestion.display == 'show create table abc'
    assert suggestion.corrected_sql == 'show create table abc'


def test_suggest_keyword_correction_custom_delimiter():
    suggestion = suggest_keyword_correction(SyntaxFragment('selet 1'), ['selet 1', 'select 2'], '$$', KEYWORD_WORDS, frozenset())
    assert suggestion.corrected_sql == 'select 1$$ select 2'


def test_suggest_keyword_correction_long_statement_displays_word_pairs():
    filler = 'z' * 130
    statement = f'createe tabel {filler}'
    suggestion = suggest_keyword_correction(SyntaxFragment(statement), [statement], ';', KEYWORD_WORDS, frozenset())
    assert suggestion.display == 'createe → create, tabel → table'
    assert suggestion.corrected_sql == f'create table {filler}'


def test_suggest_keyword_correction_structural_error_stays_silent():
    suggestion = suggest_keyword_correction(
        SyntaxFragment('frobnicate the widget'), ['frobnicate the widget'], ';', KEYWORD_WORDS, frozenset()
    )
    assert suggestion is None
