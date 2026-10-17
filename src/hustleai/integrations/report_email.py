"""Private owner report email; TLS verified and single configured recipient."""
from email.message import EmailMessage
import os
import smtplib
from hustleai.workflows.bank_reconciliation import markdown_report
from hustleai.integrations.zoho.auth import tls_context


def send_report(report):
    recipient = os.environ['HUSTLEAI_REPORT_EMAIL']
    sender = os.environ['HUSTLEAI_SMTP_FROM']
    if any(x in recipient + sender for x in ('\r', '\n', ',', ';')) or '@' not in recipient:
        raise ValueError('Configure one owner email address.')
    message = EmailMessage()
    message['From'] = sender
    message['To'] = recipient
    message['Subject'] = 'RG Midrand: bank payment reconciliation report'
    message.set_content('Your owner-only payment review report is attached. These are potential missing payments, not confirmed nonpayment. No customer reminders were sent.')
    message.add_attachment(markdown_report(report).encode(), maintype='text', subtype='markdown', filename='RG-Midrand-payment-report.md')
    host = os.environ['HUSTLEAI_SMTP_HOST']; port = int(os.getenv('HUSTLEAI_SMTP_PORT', '465'))
    if port == 465:
        client = smtplib.SMTP_SSL(host, port, timeout=30, context=tls_context())
    elif port == 587:
        client = smtplib.SMTP(host, port, timeout=30)
    else:
        raise ValueError('Use verified TLS SMTP on port 465 or STARTTLS on port 587.')
    with client:
        if port == 587:
            client.ehlo(); client.starttls(context=tls_context()); client.ehlo()
        client.login(os.environ['HUSTLEAI_SMTP_USER'], os.environ['HUSTLEAI_SMTP_PASSWORD'])
        if client.send_message(message):
            raise ValueError('Recipient not accepted.')
