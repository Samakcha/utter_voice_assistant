"""
Keep Utter's login (the session tokens, never the password) in the macOS Keychain, the
place Mac apps keep secrets. Uses the system's Security framework directly (no package).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE = "com.utter.mac.account"
ACCOUNT = "session"
_FRAMEWORK = "/System/Library/Frameworks/Security.framework"
_NOT_FOUND = -25300  # errSecItemNotFound
_DUPLICATE = -25299  # errSecDuplicateItem

_sec = None


def _security():
    global _sec
    if _sec is None:
        import Foundation
        import objc
        bundle = Foundation.NSBundle.bundleWithPath_(_FRAMEWORK)
        g = {}
        objc.loadBundleFunctions(bundle, g, [
            ("SecItemAdd", b"i@o^@"), ("SecItemCopyMatching", b"i@o^@"),
            ("SecItemDelete", b"i@"), ("SecItemUpdate", b"i@@"),
        ])
        objc.loadBundleVariables(bundle, g, [(name, b"@") for name in (
            "kSecClass", "kSecClassGenericPassword", "kSecAttrService", "kSecAttrAccount",
            "kSecAttrLabel", "kSecValueData", "kSecReturnData", "kSecMatchLimit", "kSecMatchLimitOne",
        )])
        _sec = g
    return _sec


class Keychain:
    """One secret (text) stored under a service name. Errors are logged, never raised."""

    def __init__(self, service: str = SERVICE, account: str = ACCOUNT):
        self.service = service
        self.account = account

    def _query(self, sec) -> dict:
        return {sec["kSecClass"]: sec["kSecClassGenericPassword"],
                sec["kSecAttrService"]: self.service, sec["kSecAttrAccount"]: self.account}

    def read(self) -> Optional[str]:
        try:
            sec = _security()
            query = self._query(sec)
            query[sec["kSecReturnData"]] = True
            query[sec["kSecMatchLimit"]] = sec["kSecMatchLimitOne"]
            status, data = sec["SecItemCopyMatching"](query, None)
            if status == _NOT_FOUND or data is None:
                return None
            if status != 0:
                logger.warning("Could not read the saved login from the Keychain (error %d).", status)
                return None
            return bytes(data).decode("utf-8")
        except Exception as e:
            logger.warning("Keychain not available: %s", e)
            return None

    def write(self, text: str) -> bool:
        try:
            import Foundation
            sec = _security()
            value = Foundation.NSData.dataWithBytes_length_(text.encode("utf-8"), len(text.encode("utf-8")))
            query = self._query(sec)
            status = sec["SecItemUpdate"](query, {sec["kSecValueData"]: value})
            if status == _NOT_FOUND:
                item = dict(query)
                item[sec["kSecValueData"]] = value
                item[sec["kSecAttrLabel"]] = "Utter login"
                status, _ = sec["SecItemAdd"](item, None)
            if status != 0:
                logger.warning("Could not save the login in the Keychain (error %d).", status)
            return status == 0
        except Exception as e:
            logger.warning("Keychain not available: %s", e)
            return False

    def delete(self) -> None:
        try:
            sec = _security()
            status = sec["SecItemDelete"](self._query(sec))
            if status not in (0, _NOT_FOUND):
                logger.warning("Could not remove the saved login from the Keychain (error %d).", status)
        except Exception as e:
            logger.warning("Keychain not available: %s", e)


class MemoryKeychain:
    """Same interface, kept in memory (tests)."""

    def __init__(self, text: Optional[str] = None):
        self.text = text

    def read(self) -> Optional[str]:
        return self.text

    def write(self, text: str) -> bool:
        self.text = text
        return True

    def delete(self) -> None:
        self.text = None
