"""Canonical CSV/JSON adapters and flexible PDF relation extraction."""
import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

IBM_HEADER = ('Timestamp', 'From Bank', 'Account', 'To Bank', 'Account',
              'Amount Received', 'Receiving Currency', 'Amount Paid',
              'Payment Currency', 'Payment Format', 'Is Laundering')
FIELDS = ('timestamp', 'from_bank', 'from_account', 'to_bank', 'to_account',
          'amount_received', 'receiving_currency', 'amount_paid', 'payment_currency', 'payment_format')


class DocumentError(ValueError):
    pass


def timestamp(value):
    value = str(value).strip()
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        dt = datetime.strptime(value, '%Y/%m/%d %H:%M')
    # Naive source timestamps are explicitly treated as UTC, as documented.
    return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat(timespec='microseconds')


def normalize(row):
    result = {}
    for key in FIELDS:
        value = row.get(key)
        if key in ('from_bank', 'from_account', 'to_bank', 'to_account') and not isinstance(value, str):
            raise ValueError(f'{key} must be a string to preserve account identity')
        if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
            raise ValueError(f'{key} is required')
        value = str(value).strip()
        if not value or len(value) > 256:
            raise ValueError(f'{key} must contain 1–256 characters')
        result[key] = value
    result['timestamp'] = timestamp(result['timestamp'])
    for key in ('amount_paid', 'amount_received'):
        try:
            number = Decimal(result[key])
        except InvalidOperation:
            raise ValueError(f'{key} must be a decimal')
        if not number.is_finite() or number < 0 or number > Decimal('1e18'):
            raise ValueError(f'{key} must be finite and between 0 and 1e18')
        if not -18 <= number.as_tuple().exponent <= 18 or len(number.as_tuple().digits) > 38:
            raise ValueError(f'{key} supports at most 18 fractional digits and 38 significant digits')
        result[key] = format(number, 'f')
    for side in ('from', 'to'):
        # JSON tuples avoid collisions when bank/account identifiers contain colons.
        result['sender_id' if side == 'from' else 'receiver_id'] = json.dumps(
            [result[f'{side}_bank'], result[f'{side}_account']], separators=(',', ':'))
    reference = row.get('reference')
    result['reference'] = str(reference).strip() if reference is not None else None
    return result


def csv_rows(text):
    reader = csv.reader(io.StringIO(text))
    header = next(reader, [])
    if tuple(header) == IBM_HEADER:
        header = list(FIELDS) + ['is_laundering']
    if len(set(header)) != len(header) or not set(FIELDS).issubset(header):
        raise DocumentError('Expected canonical CSV columns or the original IBM header; see /statement-schema.')
    for row in reader:
        if not row or all(not x.strip() for x in row):
            continue
        if len(row) != len(header):
            yield {'_error': 'Column count differs from header'}
        else:
            yield dict(zip(header, row))


def parse_document(path, suffix):
    if suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise DocumentError('Encrypted PDF requires an unencrypted export.')
        if len(reader.pages) > 200:
            raise DocumentError('PDF exceeds 200 pages; split the statement.')
        # Preserve deterministic ingestion for legacy canonical-table PDFs.
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        first_line = text.splitlines()[0] if text.splitlines() else ''
        header = next(csv.reader([first_line]), [])
        if set(FIELDS).issubset(header) or tuple(header) == IBM_HEADER:
            rows = csv_rows(text)
        else:
            from .pdf_extraction import extract_relations
            yield from extract_relations(path, reader)
            return
    elif suffix == '.csv':
        rows = csv_rows(path.read_text(encoding='utf-8-sig'))
    elif suffix == '.json':
        rows = json.loads(path.read_text(encoding='utf-8-sig'), parse_float=Decimal)
        if isinstance(rows, dict):
            rows = rows.get('transactions')
        if not isinstance(rows, list):
            raise DocumentError('JSON must be an array or an object with a transactions array.')
    else:
        raise DocumentError('Supported files: CSV, JSON, and PDF.')
    for index, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict):
                raise ValueError('Expected a transaction object')
            if '_error' in row:
                raise ValueError(row['_error'])
            yield index, normalize(row), None
        except (ValueError, TypeError, OverflowError) as exc:
            yield index, None, str(exc)
