"""Payments business operations; provider HTTP stays in integrations."""
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from hustleai.domain.phones import normalize
from hustleai.domain.validation import identifier, positive

class PaymentWorkflows:
    """Mixin using the service API, mapping and immutable proposal journal."""

    def prepare_payment(self, phone, invoice_id, amount, payment_date, mode, reference, contact_id=''):
        """Prepare recording an already received payment against one invoice.

        Args:
            phone: Invoice customer's cellphone.
            invoice_id: Numeric invoice ID belonging to that client.
            amount: Positive ZAR amount, at most two decimal places and no more
                than the current invoice balance. Prefer a decimal string.
            payment_date: Receipt date in YYYY-MM-DD format.
            mode: cash, banktransfer, creditcard, check, bankremittance, or others.
            reference: Nonblank receipt/bank reference used for duplicate checks.
            contact_id: Optional client ID to disambiguate the cellphone.

        Returns:
            Confirmation proposal with record_only_no_charge=True and the request
            body allocating the entire payment to the selected invoice.

        Raises:
            ValueError: Ownership, amount, mode, reference, date or API reads fail.

        No money is collected and no payment is submitted here. Confirmation
        rechecks balance and references because they can change after preview.
        The caller must establish that funds were received (except explicit tests).
        """
        invoice = self.invoice(phone, invoice_id, contact_id)
        amount = positive(amount)
        if amount != amount.quantize(Decimal('.01')):
            raise ValueError('Payment must have at most two decimal places.')
        if amount > Decimal(str(invoice['balance'])):
            raise ValueError('Payment exceeds invoice balance.')
        if mode not in ('cash', 'banktransfer', 'creditcard', 'check', 'bankremittance', 'others'):
            raise ValueError('Unsupported payment mode.')
        if not reference.strip():
            raise ValueError('A payment reference is required.')
        payload = {'customer_id': str(invoice['customer_id']), 'payment_mode': mode,
                   'amount': float(amount), 'date': date.fromisoformat(payment_date).isoformat(),
                   'reference_number': reference, 'invoices': [{'invoice_id': identifier(invoice_id),
                                                              'amount_applied': float(amount)}]}
        return self.proposal('payment', payload, {'record_only_no_charge': True, 'currency': self.tenant.currency, 'payment': payload})
