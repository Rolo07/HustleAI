"""Local bounded PDF extraction; owner must review the extracted table as CSV."""
import base64
import binascii
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from hustleai.workflows.bank_reconciliation import ALIASES, normalized


def extract_pdf(encoded, password=''):
    if not isinstance(encoded, str) or len(encoded) > 6 * 1024 * 1024:
        raise ValueError('PDF upload limit is 4 MB.')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Invalid PDF upload.') from None
    if not data.startswith(b'%PDF-') or len(data) > 4 * 1024 * 1024:
        raise ValueError('Upload a PDF of at most 4 MB.')
    import importlib.util
    if not importlib.util.find_spec('pdfplumber'):
        raise ValueError('PDF extraction requires the optional pdfplumber package. No data was sent externally.')
    with tempfile.TemporaryDirectory(prefix='hustleai-bank-') as folder:
        os.chmod(folder, 0o700)
        path = Path(folder) / 'statement.pdf'
        path.write_bytes(data); path.chmod(0o600)
        try:
            result = subprocess.run([sys.executable, '-m', 'hustleai.dashboard.bank_pdf', str(path)],
                                    input=json.dumps({'password': str(password)}), capture_output=True,
                                    text=True, timeout=45)
            payload = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, ValueError):
            raise ValueError('PDF extraction failed or timed out. Use a bank CSV export; nothing was reconciled.') from None
        if result.returncode or payload.get('error'):
            raise ValueError('PDF could not be read. Check the password; scanned/image statements require a bank CSV export.')
        return payload


def read_pdf(path, password):
    import pdfplumber
    header = None; rows = []; texts = []
    with pdfplumber.open(path, password=password or None) as document:
        if len(document.pages) > 20:
            raise ValueError('Maximum 20 PDF pages.')
        for page in document.pages:
            texts.append((page.extract_text() or '')[:30000])
            for table in page.extract_tables():
                for row in table:
                    cells = [str(x or '').replace('\n', ' ').strip() for x in row]
                    names = set(map(normalized, cells))
                    is_header = bool(names.intersection(ALIASES['date'])) and bool(names.intersection(ALIASES['amount'] + ALIASES['credit']))
                    if is_header:
                        if header and list(map(normalized, header)) != list(map(normalized, cells)):
                            raise ValueError('Multiple incompatible tables. Review a CSV export.')
                        header = cells
                    elif header and len(cells) == len(header) and any(cells):
                        rows.append(cells)
                        if len(rows) > 5000:
                            raise ValueError('Maximum 5,000 statement rows.')
    output = io.StringIO()
    if header and rows:
        writer = csv.writer(output); writer.writerow(header); writer.writerows(rows)
    text = '\n\n'.join(texts)[:200000]
    if not text and not rows:
        raise ValueError('No readable text. Scanned PDF is unsupported.')
    return {'csv': output.getvalue(), 'text': text, 'pages': len(texts),
            'warning': 'Review every extracted row, sign, date and the complete statement period. PDF extraction may omit or misalign values; no payments have been recorded.'}


if __name__ == '__main__':
    try:
        settings = json.loads(sys.stdin.read())
        print(json.dumps(read_pdf(sys.argv[1], settings.get('password', ''))))
    except Exception:
        print(json.dumps({'error': 'Unreadable PDF'}))
        raise SystemExit(1)
