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


DEFAULT = ''  # Route key for single-tenant installs served at /webhook.


class Route:
    """One tenant's webhook route: its settings, storage and event handler."""

    def __init__(self, settings, store_factory, process):
        self.settings = settings
        self.store_factory = store_factory
        self.process = process


class Gateway:
    """Owns the event queue for one or many tenants.

    Single tenant: Gateway(settings, store_factory, process), served at
    /webhook. Many tenants: Gateway(routes={slug: Route(...)}), each served
    at /webhook/<slug>, verified with that tenant's own app secret and
    phone number ID. Queue items are (slug, event).
    """

    def __init__(self, settings=None, store_factory=None, process=None, routes=None):
        self.routes = dict(routes or {})
        if settings is not None:
            self.routes[DEFAULT] = Route(settings, store_factory, process)
        self.events = queue.Queue()
        self.errors = []

    @property
    def settings(self):
        """Settings of the single-tenant route (compatibility)."""
        return self.routes[DEFAULT].settings

    def route(self, slug):
        """Return the Route for a slug, or None if unknown."""
        return self.routes.get(slug if slug is not None else DEFAULT)

    def challenge(self, slug, params):
        """Answer Meta's subscription check for one tenant, or None."""
        route = self.route(slug)
        return subscription_challenge(params, route.settings['verify_token']) if route else None

    def accept(self, body, signature, slug=None):
        """Verify, parse and durably store a webhook body for one tenant.

        Returns the HTTP status: 404 for an unknown tenant, 403 for a bad
        signature, 400 for bad JSON, otherwise 200 after new events are
        stored and queued. Events for another phone number ID are ignored.
        """
        route = self.route(slug)
        if route is None:
            return 404
        if not signature_valid(route.settings['app_secret'], body, signature):
            return 403
        try:
            payload = json.loads(body)
        except ValueError:
            return 400
        events = parse_events(payload, route.settings['phone_number_id'])
        if events:
            store = route.store_factory()
            try:
                for event in events:
                    if store.claim_webhook_event(event['id'], event['kind'], event):
                        self.events.put((slug if slug is not None else DEFAULT, event))
            finally:
                store.close()
        return 200

    def recover(self):
        """Queue every tenant's stored events that were never processed."""
        for slug, route in self.routes.items():
            store = route.store_factory()
            try:
                for event in store.unprocessed_webhook_events():
                    self.events.put((slug, event))
            finally:
                store.close()

    def work_once(self, timeout=None):
        """Process one queued event; errors are kept and never stop the worker."""
        slug, event = self.events.get(timeout=timeout)
        try:
            route = self.routes[slug]
            store = route.store_factory()
            try:
                route.process(store, event)
            finally:
                store.close()
        except Exception as error:  # Keep serving; the event stays unprocessed for recovery.
            self.errors.append((slug, event.get('id'), type(error).__name__))
            print(f"Tenant {slug or 'default'} event {event.get('id')} failed: {type(error).__name__}", flush=True)
        finally:
            self.events.task_done()

    def run_worker(self):
        """Process events forever on a daemon thread."""
        while True:
            self.work_once()


def webhook_slug(path):
    """Return the tenant slug for /webhook/<slug>, DEFAULT for /webhook, else None."""
    if path == '/webhook':
        return DEFAULT
    if path.startswith('/webhook/'):
        slug = path[len('/webhook/'):]
        return slug if slug and '/' not in slug else None
    return None


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
            slug = webhook_slug(url.path)
            if slug is None:
                return self.respond(404)
            challenge = gateway.challenge(slug, dict(urllib.parse.parse_qsl(url.query)))
            return self.respond(200, challenge.encode()) if challenge else self.respond(403)

        def do_POST(self):
            slug = webhook_slug(urllib.parse.urlsplit(self.path).path)
            if slug is None:
                return self.respond(404)
            length = int(self.headers.get('Content-Length') or 0)
            if length <= 0 or length > MAX_BODY:
                return self.respond(413 if length > MAX_BODY else 400)
            body = self.rfile.read(length)
            try:
                status = gateway.accept(body, self.headers.get('X-Hub-Signature-256'), slug)
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
