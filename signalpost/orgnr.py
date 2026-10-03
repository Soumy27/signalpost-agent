"""Norwegian organisation number helpers (9 digits, MOD11 check digit)."""
import re

_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


def normalize(raw):
    # accepts "923 609 016", "NO 923609016 MVA", etc.
    return re.sub(r"\D", "", str(raw))


def is_valid(orgnr):
    if not re.fullmatch(r"\d{9}", orgnr or ""):
        return False
    total = sum(int(d) * w for d, w in zip(orgnr[:8], _WEIGHTS))
    check = 11 - total % 11
    if check == 11:
        check = 0
    return check != 10 and check == int(orgnr[8])


def pattern(orgnr):
    """Regex that finds the orgnr in free text, allowing common separators
    such as '923 609 016', '923.609.016' or 'NO923609016MVA'."""
    sep = r"[\s. -]?"
    return re.compile(r"(?<!\d)" + sep.join(orgnr) + r"(?!\d)")
