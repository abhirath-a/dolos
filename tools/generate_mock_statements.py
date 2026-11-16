"""Generate the reproducible synthetic video upload pack.

Run from a Nix shell:
  uv run --with reportlab --extra pdf-llm python tools/generate_mock_statements.py

Every PDF has the exact same canonical rows in its dolos-transactions.csv
attachment and CSV companion. No label or model score is provided to inference.
"""
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ('timestamp', 'from_bank', 'from_account', 'to_bank', 'to_account',
          'amount_received', 'receiving_currency', 'amount_paid',
          'payment_currency', 'payment_format')
OUT = ROOT / 'output' / 'pdf'
INK, MUTED, BLUE = map(HexColor, ('#202c3a', '#5c6878', '#3658db'))


def csv_bytes(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows({k: row[k] for k in FIELDS} for row in rows)
    return stream.getvalue().encode()


def statement(path, title, rows, payload):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.setTitle(title + ' - synthetic test statement')
    pdf.setAuthor('Dolos synthetic fixture generator')
    width, height = A4
    per_page = 35
    total = (len(rows) + per_page - 1) // per_page
    columns = [(44, 'UTC timestamp'), (164, 'From account'),
               (261, 'To account'), (410, 'Amount (USD)'), (487, 'Format')]
    for page in range(total):
        pdf.setFillColor(BLUE)
        pdf.rect(44, height - 59, 29, 4, fill=1, stroke=0)
        pdf.setFillColor(INK)
        pdf.setFont('Helvetica-Bold', 26)
        pdf.drawString(44, height - 94, 'Synthetic statement')
        pdf.setFont('Helvetica', 14)
        pdf.drawString(44, height - 119, title)
        pdf.setFillColor(MUTED)
        pdf.setFont('Helvetica', 9)
        pdf.drawString(44, height - 140, 'DEMO is a fictional institution. All accounts, amounts and transfers are generated.')
        pdf.drawString(44, height - 154, 'Includes machine-readable transaction export. Not a real bank record or evidence of crime.')
        pdf.setFillColor(INK)
        pdf.setFont('Helvetica-Bold', 9)
        pdf.drawString(44, height - 181, f'{len(rows)} transfers | USD only | Bank identifier: DEMO')
        pdf.setFont('Helvetica', 9)
        pdf.drawString(44, height - 197, f'Period: {rows[0]["timestamp"][:10]} to {rows[-1]["timestamp"][:10]} (UTC)')
        header_y = height - 226
        pdf.setFillColor(HexColor('#edf0f5'))
        pdf.rect(44, header_y - 10, width - 88, 25, fill=1, stroke=0)
        pdf.setFillColor(INK)
        pdf.setFont('Helvetica-Bold', 8.5)
        for x, heading in columns:
            if heading.startswith('Amount'):
                pdf.drawRightString(x + 48, header_y, heading)
            else:
                pdf.drawString(x + 6, header_y, heading)
        for index, row in enumerate(rows[page * per_page:(page + 1) * per_page]):
            y = header_y - 30 - index * 14
            if index % 2 == 0:
                pdf.setFillColor(HexColor('#f8f9fb'))
                pdf.rect(44, y - 4, width - 88, 14, fill=1, stroke=0)
            pdf.setFillColor(INK)
            pdf.setFont('Helvetica', 8.5)
            pdf.drawString(50, y, row['timestamp'][:16].replace('T', ' '))
            pdf.drawString(170, y, 'DEMO / ' + row['from_account'])
            pdf.drawString(267, y, 'DEMO / ' + row['to_account'])
            pdf.drawRightString(458, y, f'{float(row["amount_paid"]):,.2f}')
            pdf.drawString(493, y, row['payment_format'])
        pdf.setStrokeColor(HexColor('#d8dde5'))
        pdf.line(44, 65, width - 44, 65)
        pdf.setFillColor(MUTED)
        pdf.setFont('Helvetica', 8)
        pdf.drawString(44, 49, 'SYNTHETIC TEST DATA - dolos-transactions.csv attached; same USD amount paid and received.')
        pdf.drawString(44, 36, 'Generated routing motifs are teaching labels, not established laundering or model accuracy.')
        pdf.drawRightString(width - 44, 22, f'Page {page + 1} of {total}')
        pdf.showPage()
    pdf.save()
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(buffer.getvalue())))
    writer.add_attachment('dolos-transactions.csv', payload)
    writer.add_metadata({'/Title': title + ' - synthetic test statement',
                         '/Author': 'Dolos', '/Subject': 'Synthetic generated test data; attached canonical transaction export'})
    writer.write(path)
    return total


def main():
    source = ROOT / 'var/model-demo/synthetic-transfers.csv'
    if not source.exists():
        from dolos.model_demo import generate_csv
        source.parent.mkdir(parents=True, exist_ok=True)
        generate_csv(source)
    with source.open(newline='') as stream:
        reader = csv.reader(stream)
        next(reader)
        raw = list(reader)[-1200:]
    rows = []
    for values in raw:
        row = dict(zip(FIELDS, values[:10]))
        row['timestamp'] = datetime.strptime(row['timestamp'], '%Y/%m/%d %H:%M').replace(tzinfo=timezone.utc).isoformat()
        rows.append((row, values[10]))
    inflows = [row for row, motif in rows if motif == '1' and row['from_account'] in {'0000', '0001'}]
    outflows = [row for row, motif in rows if motif == '1' and row['from_account'] in {'0003', '0005'}]
    ordinary = [row for row, motif in rows if motif == '0']
    groups = [('01-routing-inflows', 'Routing sample - incoming transfers', inflows),
              ('02-routing-outflows', 'Routing sample - onward transfers', outflows),
              ('03-ordinary-payments-a', 'Ordinary payment sample - part A', ordinary[::2]),
              ('04-ordinary-payments-b', 'Ordinary payment sample - part B', ordinary[1::2])]
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {'kind': 'synthetic_teaching_fixture', 'source': 'dolos/model_demo.py generated source',
                'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'selection': 'Last 1200 chronological rows (100 complete hours), partitioned without overlap.',
                'rows': sum(len(group[2]) for group in groups),
                'use': 'Upload all four PDFs together OR all four CSVs together; do not upload both formats together.',
                'model_policy': 'Labels are omitted from exports. Score with existing frozen model artifacts, retaining all four files as history. Predictions must be measured, not assumed.',
                'files': []}
    for stem, title, group in groups:
        payload = csv_bytes(group)
        csv_path, pdf_path = OUT / (stem + '.csv'), OUT / (stem + '.pdf')
        csv_path.write_bytes(payload)
        pages = statement(pdf_path, title, group, payload)
        manifest['files'].append({'pdf': pdf_path.name, 'csv': csv_path.name, 'rows': len(group),
                                  'pages': pages, 'csv_sha256': hashlib.sha256(payload).hexdigest(),
                                  'pdf_sha256': hashlib.sha256(pdf_path.read_bytes()).hexdigest()})
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (OUT / 'README.md').write_text('''# Synthetic video statement pack

All records are project-generated teaching data. DEMO is fictional; these are not real financial records.

Upload all four PDFs together to exercise document processing, or upload all four CSVs for a plain canonical-data comparison. Do not upload both formats into the same investigation: that would represent each transfer twice. Each PDF includes an exact `dolos-transactions.csv` attachment, identified as a machine-readable export on every page. The parser can ingest this export without an LLM call; ordinary PDFs still use configured extraction.

The pack contains the final 1,200 rows (100 complete hours) of the reproducible demo source, split without overlap into two routing-motif exports and two ordinary-payment exports. Upload all four to retain historical context. The routing/ordinary names describe generator motifs, not verified predictions or criminal findings. No ground-truth labels or model scores are included in the exported transactions.

Use the frozen demo artifacts via `DOLOS_MODEL_DIR` when testing these files. Some early history predates the model-selection cutoff and supplies context rather than target scores. Do not retrain on these uploads before evaluating them. Actual model outputs must be checked for the chosen subset; this teaching fixture is not a real-world performance benchmark.

The manifest records row counts and checksums. Recreate with:

```sh
nix develop
uv run --with reportlab --extra pdf-llm python tools/generate_mock_statements.py
```
''')
    with ZipFile(OUT / 'dolos-video-statements.zip', 'w', ZIP_DEFLATED) as archive:
        for filename in [*(stem + suffix for stem, _, _ in groups for suffix in ('.pdf', '.csv')), 'manifest.json', 'README.md']:
            archive.write(OUT / filename, filename)
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
