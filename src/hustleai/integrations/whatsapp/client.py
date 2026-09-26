"""WhatsApp Cloud API transport: text, template and document messages.

No routing or permission rules live here. Errors carry Meta's numeric code
but never the access token or message content.
"""
import json
import mimetypes
from pathlib import Path
import urllib.error
import urllib.request
import uuid

from hustleai.integrations.zoho.auth import tls_context

TEXT_LIMIT = 4096
WINDOW_CLOSED_CODES = {131047}  # Re-engagement required: >24h since the user wrote.


class WhatsAppError(ValueError):
    """A Cloud API request failed; code is Meta's error code when known."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code

    @property
    def window_closed(self):
        """True when free-form messages need the recipient to write first."""
        return self.code in WINDOW_CLOSED_CODES


def split_text(text, limit=TEXT_LIMIT):
    """Split long text on line breaks into WhatsApp-sized messages."""
    chunks, current = [], ''
    for line in text.splitlines():
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ''
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f'{current}\n{line}' if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks or ['']


class WhatsAppClient:
    """Send messages from the configured business number."""

    def __init__(self, settings):
        self.token = settings['access_token']
        self.base = f"https://graph.facebook.com/{settings['graph_version']}/{settings['phone_number_id']}"
        self.context = tls_context()

    def request(self, url, data=None, content_type='application/json', method=None):
        """Send one Graph API request and decode its JSON response."""
        headers = {'Authorization': 'Bearer ' + self.token}
        if data is not None:
            headers['Content-Type'] = content_type
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            code = None
            try:
                detail = json.load(error).get('error') or {}
                code = detail.get('code')
                message = detail.get('message') or f'HTTP {error.code}'
            except (ValueError, AttributeError):
                message = f'HTTP {error.code}'
            raise WhatsAppError(f'WhatsApp API error {code or error.code}: {message}', code) from None
        except (urllib.error.URLError, TimeoutError):
            raise WhatsAppError('Could not reach the WhatsApp API.') from None

    def send(self, to, body):
        """Send a message body (without messaging_product/to); return its ID."""
        payload = {'messaging_product': 'whatsapp', 'recipient_type': 'individual',
                   'to': to.lstrip('+'), **body}
        result = self.request(self.base + '/messages', json.dumps(payload).encode())
        return result['messages'][0]['id']

    def send_text(self, to, text):
        """Send one text message of at most 4096 characters."""
        return self.send(to, {'type': 'text', 'text': {'body': text[:TEXT_LIMIT], 'preview_url': False}})

    def send_template(self, to, name, language, parameters=()):
        """Send an approved template; used when the 24-hour window is closed."""
        template = {'name': name, 'language': {'code': language}}
        if parameters:
            template['components'] = [{'type': 'body', 'parameters': [
                {'type': 'text', 'text': str(p)} for p in parameters]}]
        return self.send(to, {'type': 'template', 'template': template})

    def upload_media(self, path):
        """Upload a local file and return Meta's media ID."""
        path = Path(path)
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'.encode(),
                 f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\n{mime}\r\n'.encode(),
                 (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
                  f'Content-Type: {mime}\r\n\r\n').encode() + path.read_bytes() + b'\r\n',
                 f'--{boundary}--\r\n'.encode()]
        result = self.request(self.base + '/media', b''.join(parts), f'multipart/form-data; boundary={boundary}')
        return result['id']

    def send_document(self, to, path, filename=None, caption=None):
        """Upload and send a document, such as an invoice PDF."""
        document = {'id': self.upload_media(path), 'filename': filename or Path(path).name}
        if caption:
            document['caption'] = caption[:1024]
        return self.send(to, {'type': 'document', 'document': document})

    def number_info(self):
        """Return Meta's display number and verified name for the configured ID."""
        return self.request(self.base + '?fields=display_phone_number,verified_name,quality_rating')
