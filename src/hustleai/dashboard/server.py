"""Same-origin authenticated dashboard HTTP server; secrets stay server-side."""
import argparse
from getpass import getpass
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
import json
import logging
import os
import re
from pathlib import Path
import secrets
import threading
import time
import urllib.parse

from hustleai.config import ROOT
from hustleai.dashboard.adapters import DemoBusiness, LiveBusiness
from hustleai.dashboard.engine import Engine
from hustleai.dashboard.storage import Journal
from hustleai.integrations.whatsapp.client import WhatsAppClient
from hustleai.integrations.whatsapp.server import handler_for
from hustleai.integrations.whatsapp.test_assistant import load_env, settings_from_env
from hustleai.tenant import load_tenant

STATIC = Path(__file__).parent / 'static'
LOG = logging.getLogger(__name__)


def handler(engine, password=None, public_origin=None):
    public_origin = os.getenv('HUSTLEAI_PUBLIC_ORIGIN', '') if public_origin is None else public_origin
    if public_origin:
        if not re.fullmatch(r'https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?', public_origin):
            raise ValueError('HUSTLEAI_PUBLIC_ORIGIN requires an HTTPS origin without a path.')
        parsed = urllib.parse.urlsplit(public_origin)
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError('Invalid public origin port.')
    public_host = urllib.parse.urlsplit(public_origin).netloc if public_origin else None
    secure_cookie = '; Secure' if public_origin else ''
    from hustleai.dashboard.bank import BankReconciliation
    bank = BankReconciliation(engine)
    base = handler_for(engine)
    session_key = secrets.token_bytes(32)
    attempts = {}

    class Handler(base):
        def allowed_host(self):
            if public_host:
                return self.headers.get('Host') == public_host
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def auth(self):
            if engine.demo:
                return True
            try:
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                value = cookies['hustleai_owner'].value
                expiry, nonce, digest = value.split('.')
                expected = hmac.new(session_key, (expiry + '.' + nonce).encode(), hashlib.sha256).hexdigest()
                return int(expiry) > time.time() and hmac.compare_digest(digest, expected)
            except (KeyError, ValueError):
                return False

        def output(self, status, value, content_type='application/json'):
            body = json.dumps(value).encode() if content_type == 'application/json' else value
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == '/webhook' or path == '/health':
                return super().do_GET()
            if not self.allowed_host():
                return self.output(403, {'error': 'Invalid dashboard host.'})
            if path in ('/', '/app.js', '/bank.js', '/style.css'):
                file = {'/': 'index.html', '/app.js': 'app.js', '/bank.js': 'bank.js', '/style.css': 'style.css'}[path]
                mime = {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript', '/bank.js': 'text/javascript', '/style.css': 'text/css'}[path]
                return self.output(200, (STATIC / file).read_bytes(), mime)
            if not self.auth():
                return self.output(401, {'error': 'Owner sign-in required.'})
            if path == '/api/state':
                try:
                    return self.output(200, engine.snapshot())
                except Exception as error:
                    LOG.error('Dashboard storage read failed: %s', type(error).__name__)
                    return self.output(503, {'error': 'Persistent storage unavailable; no sample fallback used.'})
            if path == '/api/bank/reports':
                try:
                    return self.output(200, bank.reports())
                except Exception:
                    return self.output(503, {'error': 'Persistent bank reports unavailable; no sample fallback used.'})
            if path == '/api/bank/report':
                from hustleai.workflows.bank_reconciliation import markdown_report
                rid = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('id', [''])[0]
                try:
                    return self.output(200, markdown_report(bank.report(rid)).encode(), 'text/markdown; charset=utf-8')
                except ValueError as error:
                    return self.output(404, {'error': str(error)})
            if path == '/api/catalog':
                try:
                    return self.output(200, {'products': engine.business.products() if engine.demo else engine.business.catalog()})
                except Exception as error:
                    return self.output(503, {'error': 'Current catalog unavailable (' + type(error).__name__ + '). No prices guessed.'})
            if path == '/api/pdf':
                cid = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('conversation', [''])[0]
                try:
                    with engine.journal.locked():
                        order = engine.journal.read()['conversations'][cid]['order']
                        if engine.demo:
                            raise ValueError('Demo previews show fictional order details, not a Zoho PDF.')
                        review = engine.business.validate(order)
                        return self.output(200, Path(review['pdf_path']).read_bytes(), 'application/pdf')
                except Exception:
                    return self.output(409, {'error': 'PDF missing, expired or changed. Request a fresh review.'})
            return self.output(404, {'error': 'Not found.'})

        def body(self, limit=65536):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= limit:
                    raise ValueError('Invalid body size.')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('Expected an object.')
                return data
            except (TypeError, json.JSONDecodeError):
                raise ValueError('Invalid JSON.') from None

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == '/webhook':
                if engine.demo:
                    return self.output(403, {'error': 'Demo mode does not accept Meta events.'})
                return super().do_POST()
            expected_origin = public_origin or 'http://' + self.headers.get('Host', '')
            if not self.allowed_host() or self.headers.get('Origin') != expected_origin:
                return self.output(403, {'error': 'Same-origin request required.'})
            try:
                if path.startswith('/api/bank/') and not self.auth():
                    return self.output(401, {'error': 'Owner sign-in required.'})
                data = self.body(8 * 1024 * 1024 if path.startswith('/api/bank/') else 65536)
                if path == '/api/login':
                    ip = self.client_address[0]
                    recent = [stamp for stamp in attempts.get(ip, []) if time.time() - stamp < 60]
                    attempts[ip] = recent
                    if len(recent) >= 5:
                        return self.output(429, {'error': 'Wait a minute before trying again.'})
                    if not engine.demo and not hmac.compare_digest(str(data.get('password', '')).encode(), password.encode()):
                        recent.append(time.time())
                        return self.output(401, {'error': 'Incorrect owner password.'})
                    expiry = str(int(time.time() + 3600 * 8))
                    nonce = secrets.token_hex(16)
                    digest = hmac.new(session_key, (expiry + '.' + nonce).encode(), hashlib.sha256).hexdigest()
                    self.send_response(200)
                    self.send_header('Set-Cookie', f'hustleai_owner={expiry}.{nonce}.{digest}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{secure_cookie}')
                    self.send_header('Content-Length', '2')
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(b'{}')
                    return
                if not self.auth():
                    return self.output(401, {'error': 'Owner sign-in required.'})
                if path == '/api/logout':
                    self.send_response(200)
                    self.send_header('Set-Cookie', 'hustleai_owner=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0' + secure_cookie)
                    self.send_header('Content-Length', '2')
                    self.end_headers()
                    self.wfile.write(b'{}')
                    return
                if path.startswith('/api/bank/'):
                    if path == '/api/bank/extract':
                        from hustleai.dashboard.bank_pdf import extract_pdf
                        return self.output(200, extract_pdf(data.get('pdf'), data.get('password', '')))
                    if path == '/api/bank/import':
                        report = bank.import_csv(data)
                        notice = bank.notify(report['id'])
                        return self.output(200, {'report': report, 'notifications': notice})
                    if path == '/api/bank/refresh':
                        return self.output(200, bank.refresh(str(data.get('report_id'))))
                    if path == '/api/bank/prepare':
                        return self.output(200, bank.prepare(str(data.get('report_id')), int(data.get('row'))))
                    if path == '/api/bank/confirm':
                        return self.output(200, bank.confirm(str(data.get('key')), str(data.get('confirmation', ''))))
                    if path == '/api/bank/assign':
                        return self.output(200, bank.assign(str(data.get('report_id')), int(data.get('row')), str(data.get('invoice_id'))))
                    if path == '/api/bank/notify':
                        return self.output(200, bank.notify(str(data.get('report_id'))))
                    return self.output(404, {'error': 'Not found.'})
                cid = str(data.get('conversation', ''))
                if path == '/api/action':
                    engine.action(cid, data.get('action'), data.get('data') or {}, data.get('request_id'))
                elif path == '/api/demo/inbound':
                    engine.demo_inbound(cid, Engine.text(data), data.get('event_id') or secrets.token_hex(16))
                else:
                    return self.output(404, {'error': 'Not found.'})
                return self.output(200, {'ok': True})
            except ValueError as error:
                return self.output(409, {'error': str(error)[:300]})
            except Exception as error:
                LOG.error('Dashboard action failed: %s', type(error).__name__)
                return self.output(503, {'error': 'Action unavailable (' + type(error).__name__ + '); refresh and reconcile before retrying.'})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--demo', action='store_true')
    mode.add_argument('--live', action='store_true')
    parser.add_argument('--port', type=int, default=8090)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--configure-owner', action='store_true')
    args = parser.parse_args()
    load_env(Path('.env'))
    if args.configure_owner:
        secret = getpass('Create local dashboard owner password (16+ characters, hidden): ')
        if len(secret) < 16 or any(c in secret for c in '\r\n'):
            raise ValueError('Password requires at least 16 characters and no newlines.')
        if os.getenv('HUSTLEAI_OWNER_PASSWORD'):
            raise ValueError('Owner password already configured; update it locally.')
        fd = os.open('.env', os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as output:
            output.write('\nHUSTLEAI_OWNER_PASSWORD=' + secret + '\n')
        print('Saved owner credential privately.')
        return
    settings, password = {}, None
    if args.demo:
        journal = Journal(args.root, demo=True)
        engine = Engine(journal, DemoBusiness(), demo=True)
        engine.seed()
    else:
        password = os.getenv('HUSTLEAI_OWNER_PASSWORD', '')
        if len(password) < 16:
            raise ValueError('Configure HUSTLEAI_OWNER_PASSWORD locally before live startup.')
        settings = settings_from_env()  # test-account-only guard
        tenant = load_tenant(args.root)
        journal = Journal(args.root, tenant.organization_id)
        engine = Engine(journal, LiveBusiness(args.root), WhatsAppClient(settings), settings)
        engine.connected['hermes'] = 'Configured · replies require approval' if os.getenv('HUSTLEAI_HERMES_URL') and os.getenv('HUSTLEAI_HERMES_CUSTOMER_PROFILE') in ('restricted', 'existing-restricted') else 'Restricted customer profile not configured'
        try:
            engine.client.number_info()
            engine.connected['whatsapp'] = 'Test account reachable · inbound publication blocked'
        except Exception as error:
            engine.connected['whatsapp'] = 'Unavailable (' + type(error).__name__ + ')'
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    engine.recover()
    threading.Thread(target=engine.run_worker, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(engine, password))
    print(f"RG Midrand dashboard ({'FICTIONAL DEMO' if args.demo else 'authenticated LIVE TEST ACCOUNT'}) http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        journal.close()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
