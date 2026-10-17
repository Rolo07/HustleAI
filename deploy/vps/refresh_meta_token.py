"""Refresh only the existing Meta test token using a hidden terminal prompt."""
from getpass import getpass
import os
from pathlib import Path
import re

from hustleai.integrations.whatsapp.client import WhatsAppClient, WhatsAppError
from hustleai.integrations.whatsapp.test_assistant import load_env, settings_from_env
from hustleai.storage.files import private_write


def main():
    path = Path('/var/lib/hustleai-dashboard/.env')
    load_env(path)
    os.environ['WHATSAPP_ACCESS_TOKEN'] = getpass('Fresh Meta test access token (hidden; never paste in chat): ').strip()
    client = WhatsAppClient(settings_from_env())
    try:
        client.number_info()
    except WhatsAppError as error:
        print('Token validation failed; Meta code:', error.code, 'No settings changed.')
        return 1
    content = re.sub(r'^WHATSAPP_ACCESS_TOKEN=.*(?:\n|$)', '', path.read_text(), flags=re.M)
    private_write(path, content.rstrip('\n') + '\nWHATSAPP_ACCESS_TOKEN=' + os.environ['WHATSAPP_ACCESS_TOKEN'] + '\n')
    print('Test token validated and saved privately.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
