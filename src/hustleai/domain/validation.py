"""Provider ID and monetary-input validation shared by workflows."""
from decimal import Decimal, InvalidOperation

def identifier(value):
    """Validate and return a Zoho identifier as a string.

    Args:
        value: String or integer identifier containing digits only.

    Returns:
        The string representation, preserving leading zeros in string inputs.

    Raises:
        ValueError: The value contains non-digit characters or is empty.

    This checks syntax only; existence and ownership require an API lookup.
    """
    value = str(value)
    if not value.isdigit():
        raise ValueError('Expected a numeric Zoho ID.')
    return value

def positive(value):
    """Parse a finite, strictly positive decimal without rounding it.

    Args:
        value: Decimal-compatible input; strings are preferred for money.

    Returns:
        A Decimal used for validation and local arithmetic.

    Raises:
        ValueError: The input is invalid, non-finite, zero, or negative.

    Payment-specific decimal-place limits are enforced by prepare_payment.
    """
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Expected a positive number.') from None
    if not result.is_finite() or result <= 0:
        raise ValueError('Expected a finite positive number.')
    return result
