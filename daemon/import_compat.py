"""Conservative logical-dump preflight; never rewrite string data or relax SQL mode.

Cross-version SQL is not universally portable. Unsupported features stop before
account creation. Optional adaptations change only explicit SQL metadata tokens.
"""
import re
from collections import deque
from itertools import islice
from daemon import mariadb
from shared.validation import ValidationError

# Only explicit, reviewed fallbacks. They change comparison semantics, so the
# operator must opt in; uniqueness conflicts still fail under normal SQL rules.
COLLATION_FALLBACKS = {
    'utf8mb4_0900_ai_ci': 'utf8mb4_unicode_ci',
    'utf8mb4_0900_bin': 'utf8mb4_bin',
    'utf8mb4_uca1400_ai_ci': 'utf8mb4_unicode_ci',
    'uca1400_ai_ci': 'utf8mb4_unicode_ci',
}
TOKEN = re.compile(r"'(?:\\.|''|[^'\\])*'|\"(?:\\.|\"\"|[^\"\\])*\"|`(?:``|[^`])*`|/\*(?![!M])[^*]*(?:\*(?!/)[^*]*)*\*/|--[^\r\n]*|\#[^\r\n]*|[A-Za-z_][A-Za-z_0-9$]*|[^\s]", re.S)


def target_capabilities():
    connection = mariadb._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT VERSION()')
            version = cursor.fetchone()[0]
            cursor.execute('SHOW COLLATION')
            collations = {str(row[0]).lower() for row in cursor.fetchall()}
            cursor.execute('SHOW ENGINES')
            engines = {str(row[0]).lower() for row in cursor.fetchall() if str(row[1]).upper() in ('YES', 'DEFAULT')}
        return {'version': version, 'collations': collations, 'engines': engines}
    finally:
        connection.close()


def prepare_sql(sql, target, adapt=False):
    # Under alternate quote modes a generic dump lexer cannot safely distinguish
    # metadata from application data. Require an ordinary logical export.
    if adapt and re.search(r"\b(?:NO_BACKSLASH_ESCAPES|ANSI_QUOTES)\b", sql, re.I):
        raise ValidationError('Automatic adjustments require a dump without NO_BACKSLASH_ESCAPES or ANSI_QUOTES; use strict mode or regenerate the dump')
    iterator = (m for m in TOKEN.finditer(sql) if not m.group().startswith(('/*', '--', '#')))
    window = deque(islice(iterator, 5))
    changes, notes, problems = [], set(), set()
    while window:
        tokens = list(window)
        match = tokens[0]
        i = 0
        window.popleft()
        next_token = next(iterator, None)
        if next_token is not None:
            window.append(next_token)
        word = match.group().upper()
        if word in ('GTID_PURGED', 'GTID_SLAVE_POS'):
            problems.add('GTID replication metadata: regenerate the source dump with --set-gtid-purged=OFF')
        if word == 'SQL_LOG_BIN':
            problems.add('Dump changes binary logging; regenerate with replication metadata disabled')
        if word in ('COLLATE', 'COLLATION_CONNECTION', 'ENGINE'):
            j = i + 1
            if word == 'ENGINE' and (j >= len(tokens) or tokens[j].group() != '='):
                continue
            if j < len(tokens) and tokens[j].group() == '=':
                j += 1
            if j >= len(tokens):
                continue
            value = tokens[j].group().strip('`\'"').lower()
            supported = target['engines'] if word == 'ENGINE' else target['collations']
            if value in supported or value in ('default', '@'):
                continue
            # COLLATE is also a legal identifier; limit checks to metadata names.
            if word == 'ENGINE' or value.startswith(('utf8', 'latin', 'uca', 'ascii', 'ucs', 'binary')):
                fallback = COLLATION_FALLBACKS.get(value) if word != 'ENGINE' else None
                if adapt and fallback in supported:
                    changes.append((tokens[j].start(), tokens[j].end(), fallback))
                    notes.add(f'Collation {value} → {fallback}; verify sorting and unique keys')
                else:
                    problems.add(f'Unsupported {word.lower()} {value}' + (' (enable compatibility adjustments to map it)' if fallback in supported else ''))
        if word == 'DEFINER' and i + 4 < len(tokens) and tokens[i + 1].group() == '=':
            if tokens[i + 3].group() == '@':
                if adapt:
                    changes.append((tokens[i + 2].start(), tokens[i + 4].end(), 'CURRENT_USER'))
                    notes.add('Object definers changed to the destination database user; review routines, views and triggers')
                else:
                    problems.add('Source object DEFINER requires compatibility adjustments or a source dump using CURRENT_USER')
    if problems:
        raise ValidationError('Database preflight blocked: ' + '; '.join(sorted(problems)))
    for start, end, replacement in sorted(changes, reverse=True):
        sql = sql[:start] + replacement + sql[end:]
    return sql, sorted(notes)


def preflight_dumps(dumps, adapt=False):
    if not dumps:
        return []
    target = target_capabilities()
    report = [f'Destination database: {target["version"]}; logical SQL imports with database-scoped credentials']
    # Complete every check before changing any dump or creating the account.
    for dump in dumps:
        try:
            sql = dump.read_text(encoding='utf-8')
            header = re.search(r'^--\s*Server version\s+([^\r\n]+)', sql[:65536], re.M)
            if header:
                version = re.sub(r'[^a-zA-Z0-9._ +()/-]', '', header[1])[:120]
                report.append(f'{dump.name}: source server version {version}')
            _, notes = prepare_sql(sql, target, adapt)
        except UnicodeError:
            raise ValidationError(f'{dump.name}: dump is not UTF-8; export a UTF-8 logical backup before migrating') from None
        except ValidationError as exc:
            raise ValidationError(f'{dump.name}: {exc}') from None
        report.extend(f'{dump.name}: {note}' for note in notes)
    if adapt:
        for dump in dumps:
            sql, notes = prepare_sql(dump.read_text(encoding='utf-8'), target, True)
            if notes:
                dump.write_text(sql, encoding='utf-8')
    report.append('Source authentication hashes and grants are not replayed; database users are newly provisioned. Non-WordPress applications need their database credentials updated.')
    report.append('Preflight cannot prove full cross-version compatibility. Import stops on SQL errors; no --force or automatic lossy retry.')
    return report
