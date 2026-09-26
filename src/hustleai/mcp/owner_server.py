"""Local stdio MCP server for Hermes. No public HTTP port required."""
from mcp.server.fastmcp import FastMCP
from hustleai.workflows.service import Service

mcp = FastMCP('zoho_invoice', instructions='Show every write preview to Roland. Call confirm_operation only after Roland explicitly replies CONFIRM followed by that operation ID. Never invent confirmation. PDFs are for Roland only; never send to customers.')


@mcp.tool()
def lookup_client(cellphone: str) -> list:
    """Find current client records using the stored cellphone index.

    Args:
        cellphone: International number or local number starting with 0.

    Returns:
        List of current Zoho contact dictionaries; may be empty or ambiguous.

    Raises:
        ValueError: Invalid number, missing/wrong mapping, or API failure.

    Read-only in Zoho. Ask Roland to select a contact ID if multiple clients
    match. A missing match may require syncing the phone index.
    """
    with Service() as s:
        return s.clients(cellphone)


@mcp.tool()
def list_products() -> list:
    """Read all configured Zoho catalog items across every page.

    Returns:
        List of item dictionaries including IDs, status, rates and tax fields.

    Raises:
        ValueError: OAuth/API requests fail or the GET budget is exhausted.

    Includes inactive items; choose an active item for invoices. Confirm that
    the proposed rate is VAT-inclusive rather than assuming catalog pricing.
    This tool performs no remote writes.
    """
    with Service() as s:
        return list(s.api.pages('items', 'items'))


@mcp.tool()
def list_taxes() -> dict:
    """Read the configured Zoho tax list without choosing a rate.

    Returns:
        Zoho response dictionary with taxes and pagination metadata. This
        tool reads the endpoint's default page, not every possible page.

    Raises:
        ValueError: OAuth or the API read fails.

    An empty taxes list is not permission to invent a VAT rate or omit tax.
    Ask Roland for treatment when tax settings are missing.
    """
    with Service() as s:
        return s.api.get('settings/taxes')


@mcp.tool()
def create_client(name: str, email: str, cellphone: str) -> dict:
    """Prepare client creation; show the preview and ask Roland to confirm.

    Args:
        name: Client display name.
        email: Client email address with basic valid syntax.
        cellphone: Supported local or international cellphone.

    Returns:
        Existing client match with created=False, or a locally stored proposal
        containing operation_id, preview and confirmation instructions.

    Raises:
        ValueError: Input/ZAR settings are invalid or API reads fail.

    Scans live contacts for a matching phone or email. Does not create a Zoho
    record until confirm_operation is called after Roland's explicit reply.
    New clients use ZAR and seven-day payment terms.
    """
    with Service() as s:
        return s.prepare_client(name, email, cellphone)


@mcp.tool()
def create_invoice(cellphone: str, lines: list[dict], invoice_date: str, contact_id: str = '') -> dict:
    """Prepare an unsent ZAR invoice due seven days after its invoice date.

    Args:
        cellphone: Customer's cellphone.
        lines: 1–100 dictionaries with explicit VAT-inclusive rate, positive
            quantity (default 1), item_id or description, and tax_id (may
            inherit the product's tax). Use no_tax=True only after Roland
            explicitly approves it; never infer approval from missing setup.
        invoice_date: Invoice date in YYYY-MM-DD format.
        contact_id: Optional client ID to resolve a shared cellphone.

    Returns:
        Proposal with customer, dates, line details, taxes and estimated total.

    Raises:
        ValueError: Customer selection, dates, prices, tax or API reads fail.
        KeyError: A required line field such as rate is missing.

    Show the preview and request confirmation; this call does not create or
    send an invoice. After confirmation, use invoice_pdf to obtain a PDF for
    Roland. No client email or WhatsApp delivery is performed by these tools.
    """
    with Service() as s:
        return s.prepare_invoice(cellphone, lines, invoice_date, contact_id)


@mcp.tool()
def read_invoice(cellphone: str, invoice_id: str = '', contact_id: str = '') -> dict:
    """List a client's invoices or read a selected invoice after ownership checks.

    Args:
        cellphone: Client cellphone used for selection.
        invoice_id: Optional numeric Zoho invoice ID, not invoice number.
            Omit it to list invoices first.
        contact_id: Optional client ID to disambiguate shared phone numbers.

    Returns:
        {invoices: [...]} for a list, or {invoice: {...}} for full details.

    Raises:
        ValueError: Ambiguous/missing client, invalid ID, ownership/currency
            mismatch or API failure.

    Read-only. Ask which invoice Roland means when more than one exists;
    never choose an arbitrary invoice for a subsequent payment.
    """
    with Service() as s:
        return {'invoice': s.invoice(cellphone, invoice_id, contact_id)} if invoice_id else {'invoices': s.invoices(cellphone, contact_id)}


@mcp.tool()
def invoice_pdf(cellphone: str, invoice_id: str, contact_id: str = '') -> dict:
    """Save an owned invoice's PDF for delivery to Roland only.

    Args:
        cellphone: Invoice customer's cellphone.
        invoice_id: Numeric Zoho invoice ID.
        contact_id: Optional client ID for a shared number.

    Returns:
        Dictionary with absolute pdf_path and delivery instructions.

    Raises:
        ValueError: Client/invoice checks fail or response is not a PDF.
        OSError: Network/TLS or local file operations fail.

    Verifies ownership before downloading; creates or overwrites a private
    local PDF. Hermes must attach it to Roland's authenticated WhatsApp
    conversation. This tool sends no messages; never send to the customer.
    """
    with Service() as s:
        s.invoice(cellphone, invoice_id, contact_id)
        return {'pdf_path': s.api.pdf(invoice_id), 'delivery': 'Attach to Roland only; this tool does not send WhatsApp messages.'}


@mcp.tool()
def create_payment(cellphone: str, invoice_id: str, amount: str, payment_date: str,
                   mode: str, reference: str, contact_id: str = '') -> dict:
    """Prepare recording an already received payment, never charging a customer.

    Args:
        cellphone: Invoice customer's cellphone.
        invoice_id: Numeric invoice ID to allocate the payment against.
        amount: Positive ZAR decimal string with at most two decimal places.
        payment_date: Receipt date in YYYY-MM-DD format.
        mode: banktransfer, cash, creditcard, check, bankremittance, or others.
        reference: Nonblank receipt/bank reference for duplicate checking.
        contact_id: Optional client ID to disambiguate a shared cellphone.

    Returns:
        Local confirmation proposal with exact payment and allocation details.

    Raises:
        ValueError: Invalid input, ownership mismatch, overpayment or API failure.

    Ask Roland to confirm the preview before confirm_operation. This tool
    records no payment by itself and does not establish that funds arrived.
    Bank statement ingestion and automatic matching are not implemented.
    """
    with Service() as s:
        return s.prepare_payment(cellphone, invoice_id, amount, payment_date, mode, reference, contact_id)


@mcp.tool()
def confirm_operation(operation_id: str, user_confirmation: str) -> dict:
    """Execute the saved preview only after Roland's explicit confirmation.

    Args:
        operation_id: ID from the exact proposal shown to Roland.
        user_confirmation: His verbatim "CONFIRM <operation_id>" reply.
            Never manufacture this text or infer it from unrelated messages.

    Returns:
        Zoho's creation response; repeated completed IDs return the saved result.

    Raises:
        ValueError: Invalid confirmation, expired/attempted/unknown proposal,
            failed payment checks or API error.
        OSError: A local file operation fails, possibly after remote success.

    Performs a real write and updates the workflow journal and new-client phone mapping. Pending proposals expire after 30 minutes.
    After an uncertain failure, inspect Zoho rather than blindly retrying with
    another proposal. The gateway must authenticate Roland: possession of a
    confirmation string is not independent proof of consent.
    """
    with Service() as s:
        return s.confirm(operation_id, user_confirmation)


@mcp.tool()
def retrieve_reorder(cellphone: str, contact_id: str = '', source_invoice_id: str = '') -> dict:
    """Read a previous order and current catalog details without creating anything.

    Args:
        cellphone: Customer number selected in an owner-authenticated session.
        contact_id: Optional ID to disambiguate shared phone numbers.
        source_invoice_id: Explicit historical invoice choice if dates tie.
    Returns: Eligible historical invoice, version, current products and blockers.
    Raises: ValueError for missing/ambiguous order, ownership or API errors.

    Excludes drafts, voids and known tests; latest invoice date wins. Historical
    prices/discounts are not automatically copied. Resolve current prices and tax
    and obtain confirmation before using create_invoice. This is not a public
    customer-authorized endpoint; the gateway isolation is still required.
    """
    s = Service()
    try:
        return s.reorder_invoice(cellphone, contact_id, source_invoice_id)
    finally:
        s.close()


@mcp.tool()
def update_draft_invoice(cellphone: str, invoice_id: str, lines: list[dict],
                         expected_version: str, contact_id: str = '') -> dict:
    """Prepare a full replacement of an unsent draft's lines, pending confirmation.

    Args:
        cellphone: Invoice customer's phone.
        invoice_id: Existing draft to update, not a new invoice.
        lines: COMPLETE desired line list, using create_invoice's schema.
            Omitted old lines are removed. Positive inclusive rate is explicit.
        expected_version: Exact fingerprint from review_invoice.
        contact_id: Optional shared-phone client selection.
    Returns: Before/after proposal; use confirm_operation after Roland confirms.
    Raises: ValueError for stale/non-draft invoices, discounts or invalid inputs.

    Preserves existing dates and header charges. After confirmation, prior
    approvals are revoked and review_invoice must generate a fresh PDF.
    Nothing is emailed, sent through WhatsApp or marked sent by this method.
    """
    s = Service()
    try:
        return s.prepare_draft_update(cellphone, invoice_id, lines, expected_version, contact_id)
    finally:
        s.close()


@mcp.tool()
def review_invoice(cellphone: str, invoice_id: str, contact_id: str = '') -> dict:
    """Generate a versioned PDF for Roland to review; leave the invoice unsent.

    Args: cellphone, invoice_id, contact_id: Owned draft selection.
    Returns: Review ID, invoice/version, private PDF and approval phrase.
    Raises: ValueError for state/content changes; OSError for PDF failures.

    Show this exact PDF and version to Roland. Ask him for the returned
    APPROVE phrase. Creating a new review supersedes previous approvals.
    """
    s = Service()
    try:
        return s.review_invoice(cellphone, invoice_id, contact_id)
    finally:
        s.close()


@mcp.tool()
def approve_invoice_version(review_id: str, owner_confirmation: str) -> dict:
    """Record Roland's approval of one reviewed invoice version and recipient.

    Args:
        review_id: ID of the exact PDF preview presented to Roland.
        owner_confirmation: His exact 'APPROVE <review_id>' reply; never invent it.
    Returns: Local approval ID bound to invoice version, recipient and PDF hash.
    Raises: ValueError if expired, stale, superseded or not correctly confirmed.

    Owner-only tool. This does not send the invoice or mark it sent. Customer
    reorder confirmation is not an owner delivery approval. The gateway must
    authenticate Roland separately from this text check.
    """
    s = Service()
    try:
        return s.approve_invoice(review_id, owner_confirmation)
    finally:
        s.close()


@mcp.tool()
def validate_invoice_approval(approval_id: str) -> dict:
    """Recheck an approval against live invoice content, phone and saved PDF.

    Args: approval_id: Previously issued version-specific approval identifier.
    Returns: Current binding including PDF path/hash and recipient if valid.
    Raises: ValueError for changed, expired, invalid or unknown approval.

    This is a point-in-time check, not an atomic delivery claim. A future
    WhatsApp gateway must implement send deduplication and receipt handling.
    No message is sent and no Zoho status is changed.
    """
    s = Service()
    try:
        return s.check_approval(approval_id)
    finally:
        s.close()


def main():
    """Start the owner-only stdio MCP process for Hermes."""
    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
