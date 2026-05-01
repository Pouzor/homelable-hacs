"""Common fixtures for the Homelable integration tests."""
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None, None, None]:
    """Enable custom integrations defined in the test directory."""
    yield


@pytest.fixture(autouse=True)
def isolate_storage(hass_storage):  # noqa: ANN001
    """Ensure HA Store I/O is isolated per-test (prevents cross-test leakage)."""
    hass_storage.clear()
    yield hass_storage
    hass_storage.clear()
