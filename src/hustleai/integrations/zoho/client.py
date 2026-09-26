"""Zoho HTTP transport, OAuth refresh, pagination and PDF downloads.

No agent-facing authorization rules belong in this module. Mutations must be
called through the workflows and their confirmation checks.
"""
import json
import os
import time
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from hustleai.config import ROOT
from hustleai.integrations.zoho.auth import tls_context
from hustleai.domain.validation import identifier

class Client:
    def __init__(self, organization):
        """Load local credentials and obtain an access token for this instance.

        Args:
            organization: Zoho Invoice organization ID used in subsequent headers.

        Raises:
            OSError: The local credentials cannot be read.
            ValueError: JSON decoding, token exchange, or OAuth validation fails.
            KeyError: Required credential fields are missing.

        Performs one OAuth POST with the saved refresh token. The access token is
        kept in memory, never printed or saved. It is not automatically refreshed
        again during this instance's lifetime. Initializes the GET call counter.
        """
        self.organization = organization
        self.context = tls_context()
        self.calls = 0
        credentials = json.loads((ROOT / '.zoho-credentials.json').read_text())
        data = urllib.parse.urlencode({
            key: credentials[key] for key in ('client_id', 'client_secret', 'refresh_token')
        } | {'grant_type': 'refresh_token'}).encode()
        result = self.request(urllib.request.Request(
            credentials['accounts_url'] + '/oauth/v2/token', data=data))
        if not result.get('access_token'):
            raise ValueError('Could not refresh Zoho access. Run setup with valid credentials.')
        self.token = result['access_token']
        self.domain = result.get('api_domain', credentials['api_domain'])

    def request(self, request):
        """Execute a prepared HTTPS request and decode Zoho's JSON response.

        Args:
            request: urllib Request built by trusted code with endpoint and headers.

        Returns:
            Decoded response dictionary with an absent or zero API error code.

        Raises:
            ValueError: HTTP/API errors, connection failure, timeout, or invalid JSON.

        Uses the verified TLS context and a 30-second timeout. Error messages omit
        response bodies and credentials. Does not retry requests: callers must
        resolve uncertain write outcomes before submitting another mutation.
        """
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            raise ValueError(f'Zoho returned HTTP {error.code}; check permissions, organization ID and API quota.') from None
        except (urllib.error.URLError, TimeoutError):
            raise ValueError('Could not connect securely to Zoho. Check your connection.') from None
        if result.get('code', 0) != 0:
            raise ValueError(f"Zoho API error code {result['code']}; check organization ID and permissions.")
        return result

    def get(self, path):
        """Read a relative Invoice v3 endpoint with organization authentication.

        Args:
            path: Trusted relative path and optional query string, without a slash
                prefix. Validate any IDs before inserting them into this path.

        Returns:
            Decoded Zoho response dictionary.

        Raises:
            ValueError: 800 GET attempts have been reached or request() fails.

        Counts attempted GETs, pausing 0.65 seconds before requests after the first.
        The counter is per instance, not a persistent daily API quota. OAuth, POST
        and direct PDF requests do not use this counter.
        """
        if self.calls >= 800:
            raise ValueError('Stopped at 800 API calls. Existing mapping preserved; contact list needs a larger sync strategy.')
        if self.calls:
            time.sleep(0.65)
        self.calls += 1
        return self.request(urllib.request.Request(
            self.domain + '/invoice/v3/' + path,
            headers={'Authorization': 'Zoho-oauthtoken ' + self.token,
                     'X-com-zoho-invoice-organizationid': self.organization}))

class API(Client):
    def __init__(self, organization, pdf_dir=None):
        """Authenticate and choose the one private folder for downloaded PDFs.

        Args:
            organization: Zoho Invoice organization ID.
            pdf_dir: Folder for invoice PDFs. Service passes its own folder so
                downloads and review copies always share one location. Defaults
                to invoice-pdfs under the configured data directory.
        """
        super().__init__(organization)
        self.pdf_dir = Path(pdf_dir) if pdf_dir else ROOT / 'invoice-pdfs'

    def post(self, path, payload):
        """Submit one JSON mutation to the configured Zoho organization.

        Args:
            path: Trusted relative Invoice v3 endpoint, such as "invoices".
            payload: JSON-serializable request dictionary.

        Returns:
            The decoded Zoho response dictionary.

        Raises:
            ValueError: Inherited request handling detects an HTTP, API, or
                connection error. JSON encoding/decoding errors propagate.

        Side Effects:
            May create a remote record. This low-level method does not enforce
            confirmation or retry protection; callers must use Service.confirm.
            It never retries: a connection failure may occur after a remote write.
        """
        return self.request(urllib.request.Request(
            self.domain + '/invoice/v3/' + path,
            data=json.dumps(payload).encode(), method='POST',
            headers={'Authorization': 'Zoho-oauthtoken ' + self.token,
                     'X-com-zoho-invoice-organizationid': self.organization,
                     'Content-Type': 'application/json'}))

    def put(self, path, payload):
        """Submit one JSON update; confirmation must be enforced by Service.

        Args:
            path: Trusted relative endpoint, with validated numeric ID.
            payload: JSON-ready update body; complete lines replace old lines.
        Returns: Decoded Zoho response.
        Raises: ValueError on API/connection error; never retries uncertain PUTs.
        Side effects: Updates a remote invoice using invoices.UPDATE permission.
        """
        return self.request(urllib.request.Request(
            self.domain + '/invoice/v3/' + path,
            data=json.dumps(payload).encode(), method='PUT',
            headers={'Authorization': 'Zoho-oauthtoken ' + self.token,
                     'X-com-zoho-invoice-organizationid': self.organization,
                     'Content-Type': 'application/json'}))

    def pages(self, path, key):
        """Iterate records across every page of a Zoho list endpoint.

        Args:
            path: Trusted relative endpoint, optionally including a query string.
            key: Response collection key, such as "items" or "invoices".

        Yields:
            Individual record dictionaries in Zoho's response order.

        Raises:
            ValueError: The underlying GET fails or reaches its per-client budget.
            KeyError: The response does not contain the expected collection.

        Requests 200 records per page and stops when has_more_page is false or
        absent. Iteration performs network reads lazily and does not cache them.
        """
        page = 1
        while True:
            separator = '&' if '?' in path else '?'
            result = self.get(f'{path}{separator}page={page}&per_page=200')
            yield from result[key]
            if not result.get('page_context', {}).get('has_more_page'):
                break
            page += 1

    def pdf(self, invoice_id):
        """Download an invoice PDF to the local private PDF directory.

        Args:
            invoice_id: Numeric Zoho invoice identifier.

        Returns:
            Absolute path to <invoice_id>.pdf inside pdf_dir.

        Raises:
            ValueError: The ID is invalid or the response lacks the PDF signature.
            OSError: Network/TLS, timeout, or filesystem operations fail.

        Side Effects:
            Reads Zoho and creates or overwrites the PDF. Newly created files use
            mode 0600 and the directory uses 0700. Existing permissions are not
            reset. This does not send messages or verify customer ownership;
            callers must first use Service.invoice for that check.
        """
        request = urllib.request.Request(
            self.domain + '/invoice/v3/invoices/' + identifier(invoice_id) + '?accept=pdf',
            headers={'Authorization': 'Zoho-oauthtoken ' + self.token,
                     'X-com-zoho-invoice-organizationid': self.organization})
        with urllib.request.urlopen(request, timeout=30, context=self.context) as response:
            data = response.read()
        if not data.startswith(b'%PDF-'):
            raise ValueError('Zoho did not return a PDF.')
        self.pdf_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.pdf_dir / (identifier(invoice_id) + '.pdf')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
        return str(path)
