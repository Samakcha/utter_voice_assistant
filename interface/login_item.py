"""
Start Utter at login, using macOS's own Login Items (System Settings > General > Login Items).

macOS's ServiceManagement framework (SMAppService) adds the app itself to "Open at Login".
It is loaded straight from the system (no extra Python package needed), and only works for
Utter.app: when Utter runs from the project folder there is no app to add, so the status is
"unavailable".
"""

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ON = "on"
OFF = "off"
NEEDS_APPROVAL = "needs_approval"  # added, but switched off in System Settings > Login Items
UNAVAILABLE = "unavailable"  # not running as Utter.app (or macOS too old)

# SMAppServiceStatus
_NOT_REGISTERED, _ENABLED, _REQUIRES_APPROVAL, _NOT_FOUND = 0, 1, 2, 3
_FRAMEWORK = "/System/Library/Frameworks/ServiceManagement.framework"


def _sm_app_service_class():
    import Foundation
    import objc
    bundle = Foundation.NSBundle.bundleWithPath_(_FRAMEWORK)
    if bundle is None or not bundle.load():
        return None
    try:
        return objc.lookUpClass("SMAppService")
    except objc.error:
        return None  # before macOS 13


def _default_service():
    from engine.config import running_as_app
    if not running_as_app():
        return None
    cls = _sm_app_service_class()
    return cls.mainAppService() if cls is not None else None


class LoginItem:
    """Utter's entry in Login Items. `service` is replaceable for tests."""

    def __init__(self, service_factory: Optional[Callable[[], object]] = None):
        self._factory = service_factory or _default_service
        self._service = None
        self._loaded = False

    def _get(self):
        if not self._loaded:
            try:
                self._service = self._factory()
            except Exception as e:
                logger.debug("Login Items not available: %s", e)
                self._service = None
            self._loaded = True
        return self._service

    @property
    def available(self) -> bool:
        return self._get() is not None

    def status(self) -> str:
        service = self._get()
        if service is None:
            return UNAVAILABLE
        try:
            code = int(service.status())
        except Exception as e:
            logger.debug("Could not read the login item status: %s", e)
            return UNAVAILABLE
        if code == _ENABLED:
            return ON
        if code == _REQUIRES_APPROVAL:
            return NEEDS_APPROVAL
        return OFF

    def set_enabled(self, on: bool) -> str:
        """Add Utter to (or remove it from) Login Items; returns the new status."""
        service = self._get()
        if service is None:
            return UNAVAILABLE
        current = self.status()
        try:
            if on and current not in (ON, NEEDS_APPROVAL):
                result = service.registerAndReturnError_(None)
            elif not on and current in (ON, NEEDS_APPROVAL):
                result = service.unregisterAndReturnError_(None)
            else:
                return current
            ok = result[0] if isinstance(result, tuple) else result
            if not ok:
                logger.warning("macOS did not %s the login item.", "add" if on else "remove")
        except Exception as e:
            logger.warning("Could not change Start at Login: %s", e)
        new = self.status()
        logger.info("Start at login: %s", new)
        return new

    def open_settings(self) -> None:
        """System Settings > General > Login Items (to approve or switch it on there)."""
        cls = None
        try:
            cls = _sm_app_service_class()
        except Exception:
            pass
        if cls is not None:
            try:
                cls.openSystemSettingsLoginItems()
                return
            except Exception as e:
                logger.debug("openSystemSettingsLoginItems failed: %s", e)
        import subprocess
        subprocess.run(["open", "x-apple.systempreferences:com.apple.LoginItems-Settings.extension"], check=False)
