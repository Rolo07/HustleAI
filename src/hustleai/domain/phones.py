"""Phone normalization independent of transport or storage."""
import re

def normalize(number, country_code):
    """Normalize supported phone notation without inferring missing digits.

    Args:
        number: Phone value converted to text; permits digits, an initial +,
            spaces, parentheses, dots and hyphens. Extensions are unsupported.
        country_code: Country calling code without +, such as "27".

    Returns:
        A plus-prefixed 8–15 digit string, or None for unsupported input.

    An initial 00 becomes +; a local leading 0 becomes country_code. Bare
    numbers must already start with country_code. This is syntactic
    normalization, not validation against national numbering allocations.
    """
    value = str(number).strip()
    if not value:
        return None
    if not re.fullmatch(r'\+?[0-9\s().-]+', value):
        return None
    digits = re.sub(r'\D', '', value)
    if digits.startswith('00'):
        digits = digits[2:]
    elif not value.startswith('+') and digits.startswith('0'):
        digits = country_code + digits[1:]
    elif not value.startswith('+') and not digits.startswith(country_code):
        return None
    return '+' + digits if 8 <= len(digits) <= 15 else None
