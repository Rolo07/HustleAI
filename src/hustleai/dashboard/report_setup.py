"""Configure an existing email service locally; credentials never enter chat."""
from getpass import getpass
from pathlib import Path
from hustleai.cli.tenant import set_env


def main():
    recipient = input('Owner report email address: ').strip()
    host = input('Existing SMTP host: ').strip()
    port = input('TLS port [465; or 587 for STARTTLS]: ').strip() or '465'
    sender = input('Sender email address: ').strip()
    user = input('SMTP username: ').strip()
    password = getpass('SMTP/app password (hidden): ')
    if port not in ('465', '587') or any(c in recipient + sender + host + user for c in '\r\n'):
        raise ValueError('Use verified TLS port 465/587 and single-line settings.')
    if '@' not in recipient or any(c in recipient for c in ',;') or '@' not in sender or not host or not password:
        raise ValueError('One owner recipient, sender, SMTP host and private password required.')
    set_env(Path('.env'), {'HUSTLEAI_REPORT_EMAIL': recipient, 'HUSTLEAI_SMTP_HOST': host, 'HUSTLEAI_SMTP_PORT': port,
                          'HUSTLEAI_SMTP_FROM': sender, 'HUSTLEAI_SMTP_USER': user, 'HUSTLEAI_SMTP_PASSWORD': password})
    print('Saved private email settings. Restart the dashboard to use them; no test email was sent.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
