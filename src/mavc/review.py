"""Readable CSV review sheets; every imported label is reserved for testing."""
import csv
from pathlib import Path

FINDINGS = {'ok', 'label_disagreement', 'quote_mismatch', 'missing_context', 'other'}
DISPOSITIONS = {'accept', 'correct', 'abstain', 'unresolved'}
NOTES = ('finding_code', 'disposition', 'review_note')


def _index(rows, id_field):
    indexed, problems = {}, []
    for number, row in enumerate(rows, 2):
        identity = row.get(id_field)
        if not isinstance(identity, str) or not identity.strip():
            problems.append(f'invalid {id_field} at row {number}')
        elif identity in indexed:
            problems.append(f'duplicate ID {identity!r} at row {number}')
        else:
            indexed[identity] = dict(row)
    if problems:
        raise ValueError('; '.join(problems))
    return indexed


def export_sheet(rows, path, *, id_field='id'):
    """Write once, retaining source columns/text verbatim (CSV quoting may change)."""
    rows = list(_index(rows, id_field).values())
    if not rows:
        raise ValueError('no review rows')
    fields = list(rows[0])
    if any(set(row) != set(fields) for row in rows):
        raise ValueError('review rows have different columns')
    fields += [field for field in NOTES if field not in fields]
    with Path(path).open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(rows)


def import_sheet(path, original_rows, *, id_field='id', answer_fields=('answer',),
                 observer_type, allowed_labels=None):
    """Allow edits only to blank answers and finding/disposition/note columns.

    Pass the current completed rows as original_rows on subsequent imports.
    Completed answers cannot be replaced, even by another observer. Partial
    sheets report missing IDs; unknown/duplicate IDs or changed evidence fail.
    Observer type describes the source, never makes it training data or gold.
    """
    if not observer_type or not isinstance(observer_type, str):
        raise ValueError('observer_type required (for example model or human)')
    original = _index(original_rows, id_field)
    if id_field in (*answer_fields, *NOTES):
        raise ValueError('identity cannot be an editable field')
    with Path(path).open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)):
            raise ValueError('duplicate sheet columns')
        imported = _index(reader, id_field)
    invalid = sorted(set(imported) - set(original))
    if invalid:
        raise ValueError(f'invalid IDs: {invalid}')
    records = []
    for identity, row in imported.items():
        before = original[identity]
        expected = set(before) | set(NOTES)
        if set(row) != expected or not set(answer_fields) <= set(before):
            raise ValueError(f'changed/missing columns for ID {identity!r}')
        for field, value in before.items():
            if row[field] != value and field not in NOTES and (field not in answer_fields or value):
                raise ValueError(f'cannot overwrite completed answer/evidence {field!r} for ID {identity!r}')
        for field, choices in [('finding_code', FINDINGS), ('disposition', DISPOSITIONS)]:
            if row[field] and row[field] not in choices:
                raise ValueError(f'invalid {field} {row[field]!r} for ID {identity!r}')
        if allowed_labels is not None:
            for field in answer_fields:
                if row[field] and row[field] not in allowed_labels:
                    raise ValueError(f'invalid label {row[field]!r} for ID {identity!r}')
        records.append({'id': identity, 'row': row, 'metadata': {
            'label_source': 'review', 'observer_type': observer_type,
            'test_only': True, 'split': 'test'}})
    return {'records': records, 'missing_ids': sorted(set(original) - set(imported)),
            'metadata': {'label_source': 'review', 'observer_type': observer_type,
                         'test_only': True, 'split': 'test'}}
