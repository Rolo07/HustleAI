"""HTTP webhook server for the WhatsApp Cloud API.

Runs behind an HTTPS reverse proxy (Meta requires HTTPS) on 127.0.0.1.
POST /webhook verifies Meta's signature, stores each event once and answers
200 before any processing, so a crash cannot lose a message. A worker thread
processes events in order; on startup it reprocesses stored events that were
never finished. GET /webhook answers Meta's subscription check. GET /health
reports liveness without touching Zoho or WhatsApp.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import threading
import urllib.parse

from hustleai.integrations.whatsapp.webhook import parse_events, signature_valid, subscription_challenge

MAX_BODY = 1024 * 1024


class Gateway:
    """Owns the event queue. store_factory opens a storage session;
    process(store, event) handles one event with that session."""

    def __init__(self, settings, store_factory, process):
        self.settings = settings
        self.store_factory = store_factory
        self.process = process
        self.events = queue.Queue()
        self.errors = []

    def accept(self, body, signature):
        """Verify, parse and durably store a webhook body.

        Returns the HTTP status: 403 for a bad signature, 400 for bad JSON,
        otherwise 200 after new events are stored and queued.
        """
        if not signature_valid(self.settings['app_secret'], body, signature):
            return 403
        try:
            payload = json.loads(body)
        except ValueError:
            return 400
        events = parse_events(payload, self.settings['phone_number_id'])
        if events:
            store = self.store_factory()
            try:
                for event in events:
                    if store.claim_webhook_event(event['id'], event['kind'], event):
                        self.events.put(event)
            finally:
                store.close()
        return 200

    def recover(self):
        """Queue stored events that were never processed (after a crash)."""
        store = self.store_factory()
        try:
            for event in store.unprocessed_webhook_events():
                self.events.put(event)
        finally:
            store.close()

    def work_once(self, timeout=None):
        """Process one queued event; errors are kept and never stop the worker."""
        event = self.events.get(timeout=timeout)
        try:
            store = self.store_factory()
            try:
                self.process(store, event)
            finally:
                store.close()
        except Exception as error:  # Keep serving; the event stays unprocessed for recovery.
            self.errors.append((event.get('id'), type(error).__name__))
            print(f"Event {event.get('id')} failed: {type(error).__name__}", flush=True)
        finally:
            self.events.task_done()

    def run_worker(self):
        """Process events forever on a daemon thread."""
        while True:
            self.work_once()


def handler_for(gateway):
    """Build a request handler class bound to one gateway."""

    class Handler(BaseHTTPRequestHandler):
        server_version = 'HustleAI'

        def respond(self, status, body=b'', content_type='text/plain'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path == '/health':
                return self.respond(200, b'ok')
            if url.path == '/webhook':
                params = dict(urllib.parse.parse_qsl(url.query))
                challenge = subscription_challenge(params, gateway.settings['verify_token'])
                return self.respond(200, challenge.encode()) if challenge else self.respond(403)
            self.respond(404)

        def do_POST(self):
            if urllib.parse.urlsplit(self.path).path != '/webhook':
                return self.respond(404)
            length = int(self.headers.get('Content-Length') or 0)
            if length <= 0 or length > MAX_BODY:
                return self.respond(413 if length > MAX_BODY else 400)
            body = self.rfile.read(length)
            try:
                status = gateway.accept(body, self.headers.get('X-Hub-Signature-256'))
            except Exception as error:
                # Storage failed: ask Meta to retry later rather than lose the event.
                print(f'Webhook storage failed: {type(error).__name__}', flush=True)
                status = 503
            self.respond(status)

        def log_message(self, format, *args):
            """Log method, path and status only; never bodies or headers."""
            print(f'{self.command} {urllib.parse.urlsplit(self.path).path} {args[1] if len(args) > 1 else ""}', flush=True)

    return Handler


def serve(gateway, host, port):
    """Recover unfinished events, start the worker and serve until stopped."""
    gateway.recover()
    threading.Thread(target=gateway.run_worker, daemon=True).start()
    server = ThreadingHTTPServer((host, port), handler_for(gateway))
    print(f'WhatsApp gateway listening on http://{host}:{port}/webhook', flush=True)
    server.serve_forever()
