"""Optional forwarder for the owner's free-text messages to an AI assistant.

Posts to any OpenAI-compatible chat completions URL. On Hermes that is the
tenant profile's API server, e.g. http://127.0.0.1:8642/p/<slug>/v1/chat/completions,
with that profile's API_SERVER_KEY; X-Hermes-Session-Key keeps one ongoing
conversation per owner. Commands such as CONFIRM and APPROVE never
reach it: the gateway runs those itself. Configure under owner_agent in
.whatsapp.json with url, optional model and optional api_key.
"""
import json
import urllib.error
import urllib.request


class OwnerAgent:
    """Send one owner message and return the assistant's text reply."""

    def __init__(self, settings, session_key=None):
        self.url = settings['url']
        self.model = settings.get('model') or 'default'
        self.api_key = settings.get('api_key')
        # Hermes keeps one conversation per key across messages.
        self.session_key = settings.get('session_key') or session_key

    def ask(self, text):
        """Return the reply, or raise ValueError with a safe message."""
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = 'Bearer ' + self.api_key
        if self.session_key:
            headers['X-Hermes-Session-Key'] = self.session_key[:256]
        body = json.dumps({'model': self.model, 'user': 'owner',
                           'messages': [{'role': 'user', 'content': text}]}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url, body, headers), timeout=180) as response:
                reply = json.load(response)['choices'][0]['message']['content']
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError):
            raise ValueError('The assistant is unavailable right now. Commands still work: send HELP.') from None
        return str(reply).strip() or 'The assistant returned an empty reply.'
