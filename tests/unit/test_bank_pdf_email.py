import base64
import json
import sys
from unittest.mock import MagicMock, patch
import unittest
from hustleai.dashboard.bank_pdf import extract_pdf, read_pdf
from hustleai.integrations.report_email import send_report


class PDFTests(unittest.TestCase):
    def test_invalid_binary_never_starts_parser(self):
        with patch('subprocess.run') as run:
            with self.assertRaises(ValueError): extract_pdf(base64.b64encode(b'not pdf').decode())
            run.assert_not_called()

    def test_extract_timeout_does_not_echo_password(self):
        import subprocess
        with patch('importlib.util.find_spec', return_value=True), patch('subprocess.run', side_effect=subprocess.TimeoutExpired('parser', 45)):
            with self.assertRaises(ValueError) as error:
                extract_pdf(base64.b64encode(b'%PDF-test').decode(), 'private password')
            self.assertNotIn('private password', str(error.exception))

    def test_password_passed_through_stdin_not_command_arguments(self):
        result = MagicMock(returncode=0, stdout=json.dumps({'csv': 'Date,Amount', 'text': '', 'pages': 1}))
        with patch('importlib.util.find_spec', return_value=True), patch('subprocess.run', return_value=result) as run:
            extract_pdf(base64.b64encode(b'%PDF-test').decode(), 'private password')
            self.assertNotIn('private password', ' '.join(run.call_args.args[0]))
            self.assertEqual(json.loads(run.call_args.kwargs['input'])['password'], 'private password')

    def test_repeated_pdf_table_headers_not_transactions(self):
        plumber = MagicMock(); doc = plumber.open.return_value.__enter__.return_value
        page = MagicMock(); page.extract_text.return_value = 'Fictional statement'
        page.extract_tables.return_value = [[['Date', 'Name', 'Credit'], ['2026-10-07', 'Maya', '440']]]
        doc.pages = [page, page]
        with patch.dict(sys.modules, {'pdfplumber': plumber}):
            result = read_pdf('fictional.pdf', '')
        self.assertEqual(result['csv'].count('Date,Name,Credit'), 1)
        self.assertEqual(result['csv'].count('2026-10-07,Maya,440'), 2)
        self.assertIn('Review every extracted row', result['warning'])


class EmailTests(unittest.TestCase):
    def test_single_owner_email_uses_verified_tls(self):
        from hustleai.workflows.bank_reconciliation import reconcile
        report = reconcile([], [], [], '2026-10-01', '2026-10-11')
        env = {'HUSTLEAI_REPORT_EMAIL': 'owner@example.invalid', 'HUSTLEAI_SMTP_FROM': 'reports@example.invalid', 'HUSTLEAI_SMTP_HOST': 'smtp.example.invalid', 'HUSTLEAI_SMTP_PORT': '465', 'HUSTLEAI_SMTP_USER': 'user', 'HUSTLEAI_SMTP_PASSWORD': 'fictional-password'}
        with patch.dict('os.environ', env), patch('smtplib.SMTP_SSL') as smtp:
            smtp.return_value.send_message.return_value = {}
            send_report(report)
            self.assertIsNotNone(smtp.call_args.kwargs['context'])
            message = smtp.return_value.send_message.call_args.args[0]
            self.assertEqual(str(message['To']), 'owner@example.invalid')
            self.assertEqual(len(list(message.iter_attachments())), 1)

    def test_multiple_recipient_configuration_rejected(self):
        with patch.dict('os.environ', {'HUSTLEAI_REPORT_EMAIL': 'a@example.invalid,b@example.invalid', 'HUSTLEAI_SMTP_FROM': 'sender@example.invalid'}), patch('smtplib.SMTP_SSL') as smtp:
            with self.assertRaises(ValueError): send_report({})
            smtp.assert_not_called()


class RealPDFExtractionTests(unittest.TestCase):
    """An in-memory fictional fixture exercises the installed parser end to end."""
    @staticmethod
    def statement():
        lines = ['0.5 w']
        for y in (700, 675, 650):
            lines.append(f'50 {y} m 562 {y} l S')
        for x in (50, 140, 290, 400, 480, 562):
            lines.append(f'{x} 650 m {x} 700 l S')
        for y, values in ((683, ('Date', 'Name', 'Reference', 'Credit', 'Debit')),
                          (658, ('2026-10-07', 'Fictional Client', 'RG-1001', '440.00', '0.00'))):
            for x, text in zip((55, 145, 295, 405, 485), values):
                lines.append(f'BT /F1 9 Tf {x} {y} Td ({text}) Tj ET')
        stream = '\n'.join(lines).encode()
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                   b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                   b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
        pdf = b'%PDF-1.4\n'; offsets = [0]
        for number, obj in enumerate(objects, 1):
            offsets.append(len(pdf)); pdf += f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n'
        xref = len(pdf)
        pdf += b'xref\n0 6\n0000000000 65535 f \n' + b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
        return pdf + b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n'

    @unittest.skipUnless(__import__('importlib.util', fromlist=['find_spec']).find_spec('pdfplumber'), 'optional PDF extra not installed')
    def test_installed_pdf_parser_and_subprocess_extract_actual_table(self):
        from hustleai.workflows.bank_reconciliation import parse_statement
        result = extract_pdf(base64.b64encode(self.statement()).decode())
        self.assertEqual(result['pages'], 1)
        rows, errors = parse_statement(result['csv'], {'date_format': '%Y-%m-%d'})
        self.assertFalse(errors)
        self.assertEqual(rows[0]['date'], '2026-10-07')
        self.assertEqual(rows[0]['amount'], '440.00')
        self.assertEqual(rows[0]['reference'], 'RG-1001')
