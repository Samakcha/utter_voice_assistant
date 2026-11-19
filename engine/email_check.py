"""
Catch email mistakes before signing up, so every Utter account has a real address:
  - typos in well-known providers: "name@gmial.com" -> "Did you mean name@gmail.com?"
  - throwaway addresses (mailinator.com, 10minutemail...) are refused
The emailed 6-digit code then proves the address is real (engine/account.py).
"""

from typing import Optional

# Exact matches are always fine (also stops "mail.com" being "corrected" to "gmail.com")
KNOWN_DOMAINS = [
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.co.uk", "ymail.com",
    "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com",
    "gmx.com", "gmx.de", "mail.com", "zoho.com", "yandex.com", "rediffmail.com",
    "fastmail.com", "hey.com", "tutanota.com",
]

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "sharklasers.com", "grr.la",
    "10minutemail.com", "10minutemail.net", "tempmail.com", "temp-mail.org", "tempmail.net",
    "throwawaymail.com", "yopmail.com", "yopmail.net", "trashmail.com", "getnada.com",
    "dispostable.com", "maildrop.cc", "mailnesia.com", "fakeinbox.com", "mintemail.com",
    "emailondeck.com", "mohmal.com", "tempail.com", "moakt.com", "mailcatch.com",
    "spamgourmet.com", "burnermail.io", "inboxkitten.com", "tempr.email", "discard.email",
    "mail.tm", "emailfake.com", "fakemail.net", "trash-mail.com", "33mail.com",
}

_COMMON_TLD_TYPOS = {"con": "com", "cmo": "com", "comm": "com", "cm": "com", "om": "com",
                     "ocm": "com", "vom": "com", "xom": "com", "co": "com", "cpm": "com"}


def _distance(a: str, b: str) -> int:
    """Levenshtein distance, with a swap of two neighbouring letters counting as one."""
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            value = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                value = min(value, prev2[j - 2] + 1)
            cur.append(value)
        prev2, prev = prev, cur
    return prev[-1]


def split(email: str):
    email = (email or "").strip()
    if email.count("@") != 1:
        return None, None
    local, domain = email.split("@")
    return local, domain.lower()


def suggest_email(email: str) -> Optional[str]:
    """A corrected address when the domain looks like a typo of a well-known provider."""
    local, domain = split(email)
    if not local or not domain or domain in KNOWN_DOMAINS:
        return None
    candidates = []
    # "gmail.con", "gmail.co" -> "gmail.com"; "gmailcom" -> "gmail.com"
    if "." in domain:
        name, tld = domain.rsplit(".", 1)
        fixed = f"{name}.{_COMMON_TLD_TYPOS.get(tld, tld)}"
        if fixed in KNOWN_DOMAINS:
            candidates.append((0, fixed))
    for known in KNOWN_DOMAINS:
        if domain.replace(".", "") == known.replace(".", ""):
            candidates.append((0, known))
            continue
        d = _distance(domain, known)
        limit = 1 if len(known) < 8 else 2  # short domains: only one letter off
        if d <= limit:
            candidates.append((d, known))
    if not candidates:
        return None
    best = min(candidates)
    ties = [c for c in candidates if c[0] == best[0] and c[1] != best[1]]
    if ties:
        return None  # two providers equally close: don't guess
    return f"{local}@{best[1]}"


def is_disposable(email: str) -> bool:
    _, domain = split(email)
    if not domain:
        return False
    return domain in DISPOSABLE_DOMAINS or any(domain.endswith("." + d) for d in DISPOSABLE_DOMAINS)
