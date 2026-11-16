from pathlib import Path
import py_compile

path = Path('association/associate_documents.py')
if not path.exists():
    raise SystemExit(f'Could not find {path.resolve()}')

text = path.read_text(encoding='utf-8')
backup = path.with_suffix('.py.before_continuation_merge.bak')
backup.write_text(text, encoding='utf-8')

marker = '\ndef associate_batch(page_records):\n'
if marker not in text:
    raise SystemExit('Could not find associate_batch()')

helpers = r'''
def _same_record_file(first_record, second_record):
    first_file = normalize_name(get_record_file(first_record))
    second_file = normalize_name(get_record_file(second_record))
    return bool(first_file and second_file and first_file == second_file)


def _merge_unique_values(first_values, second_values):
    merged = []
    for value in list(first_values or []) + list(second_values or []):
        if value not in merged:
            merged.append(value)
    return merged


def merge_endorsement_continuations(page_records):
    """Merge classified continuation pages into the preceding endorsement.

    Only explicit ENDORSEMENT_CONTINUATION records are merged. UNKNOWN pages
    remain separate so unrelated material is never silently credited as policy
    evidence. Original continuation records and page numbers are retained on
    the parent schema for auditability.
    """
    ordered = sorted(
        page_records,
        key=lambda record: (
            normalize_name(get_record_file(record)) or '',
            get_record_page(record)
            if get_record_page(record) is not None
            else 10**9,
        ),
    )

    current_parent_by_file = {}
    merged_record_ids = set()

    for record in ordered:
        schema = record.get('schema') or {}
        document_type = schema.get('document_type')
        file_key = normalize_name(get_record_file(record))

        if document_type == 'ACORD_25':
            current_parent_by_file.pop(file_key, None)
            continue

        if document_type == 'ENDORSEMENT':
            current_parent_by_file[file_key] = record
            continue

        if document_type != 'ENDORSEMENT_CONTINUATION':
            continue

        parent = current_parent_by_file.get(file_key)
        if not parent or not _same_record_file(parent, record):
            continue

        parent_page = get_record_page(parent)
        continuation_page = get_record_page(record)
        if (
            parent_page is None
            or continuation_page is None
            or continuation_page <= parent_page
        ):
            continue

        parent_schema = parent.get('schema') or {}
        parent_schema['evidence_tags'] = _merge_unique_values(
            parent_schema.get('evidence_tags'),
            schema.get('evidence_tags'),
        )

        continuation_entry = {
            'page_number': continuation_page,
            'source_file': get_record_file(record),
            'document_type': document_type,
            'evidence_tags': list(schema.get('evidence_tags') or []),
            'raw_text': schema.get('raw_text'),
            'text_preview': schema.get('text_preview'),
        }
        parent_schema.setdefault('continuation_pages', []).append(
            continuation_entry
        )
        parent_schema['continuation_page_numbers'] = sorted({
            item.get('page_number')
            for item in parent_schema['continuation_pages']
            if item.get('page_number') is not None
        })
        parent_schema['continuation_evidence_merged'] = True

        text_parts = [
            parent_schema.get('combined_raw_text'),
            parent_schema.get('raw_text'),
            schema.get('raw_text'),
        ]
        combined = '\n'.join(
            part for part in text_parts if part
        )
        if combined:
            parent_schema['combined_raw_text'] = combined

        parent['schema'] = parent_schema
        merged_record_ids.add(id(record))

    remaining_records = [
        record for record in page_records
        if id(record) not in merged_record_ids
    ]
    merged_records = [
        record for record in page_records
        if id(record) in merged_record_ids
    ]
    return remaining_records, merged_records

'''

if 'def merge_endorsement_continuations(' not in text:
    text = text.replace(marker, '\n' + helpers + marker)

old_start = 'def associate_batch(page_records):\n    acord_records = ['
new_start = '''def associate_batch(page_records):
    page_records, merged_continuation_records = (
        merge_endorsement_continuations(page_records)
    )

    acord_records = ['''
if old_start not in text:
    raise SystemExit('Could not patch associate_batch() start')
text = text.replace(old_start, new_start, 1)

old_return = '''        "endorsement_count": len(endorsement_records),
        "acord_submission_count": sum('''
new_return = '''        "endorsement_count": len(endorsement_records),
        "merged_continuation_count": len(merged_continuation_records),
        "merged_continuation_records": merged_continuation_records,
        "acord_submission_count": sum('''
if old_return not in text:
    raise SystemExit('Could not patch associate_batch() return data')
text = text.replace(old_return, new_return, 1)

path.write_text(text, encoding='utf-8')
py_compile.compile(str(path), doraise=True)
print(f'Updated: {path}')
print(f'Backup:  {backup}')
print('Syntax check: PASS')
