"""Tests for the scanner module."""
from unittest.mock import patch

import pytest

from custom_components.homelable import scanner


@pytest.mark.asyncio
async def test_run_scan_invalid_cidr_raises() -> None:
    """An invalid CIDR range surfaces as ValueError."""
    with pytest.raises(ValueError, match="Invalid CIDR range"):
        await scanner.run_scan(["not-a-cidr"])


@pytest.mark.asyncio
async def test_run_scan_returns_enriched_devices() -> None:
    """Scan output carries services + suggested_type + discovery_source."""

    async def _fake_nmap(target: str) -> list[dict]:
        return [
            {
                "ip": "10.0.0.5",
                "hostname": "dev.local",
                "mac": "AA:BB:CC:DD:EE:FF",
                "os": None,
                "open_ports": [{"port": 22, "protocol": "tcp", "banner": "OpenSSH"}],
            }
        ]

    async def _no_mdns(timeout: float = 4.0) -> list[dict]:
        return []

    with (
        patch.object(scanner, "_nmap_scan", _fake_nmap),
        patch.object(scanner, "_mdns_discover", _no_mdns),
    ):
        devices = await scanner.run_scan(["10.0.0.0/24"])

    assert len(devices) == 1
    dev = devices[0]
    assert dev["ip"] == "10.0.0.5"
    assert dev["discovery_source"] == "nmap"
    assert "services" in dev
    assert "suggested_type" in dev


@pytest.mark.asyncio
async def test_run_scan_excludes_ips() -> None:
    """IPs in exclude_ips are skipped."""

    async def _fake_nmap(target: str) -> list[dict]:
        return [
            {"ip": "10.0.0.5", "hostname": None, "mac": None, "os": None, "open_ports": []},
            {"ip": "10.0.0.6", "hostname": None, "mac": None, "os": None, "open_ports": []},
        ]

    async def _no_mdns(timeout: float = 4.0) -> list[dict]:
        return []

    with (
        patch.object(scanner, "_nmap_scan", _fake_nmap),
        patch.object(scanner, "_mdns_discover", _no_mdns),
    ):
        devices = await scanner.run_scan(
            ["10.0.0.0/24"], exclude_ips={"10.0.0.5"}
        )

    assert [d["ip"] for d in devices] == ["10.0.0.6"]


@pytest.mark.asyncio
async def test_run_scan_dedups_across_sources() -> None:
    """Same IP from nmap + mdns surfaces once (nmap wins)."""

    async def _fake_nmap(target: str) -> list[dict]:
        return [
            {
                "ip": "10.0.0.7",
                "hostname": "from-nmap",
                "mac": "AA:BB:CC:00:00:01",
                "os": None,
                "open_ports": [],
            }
        ]

    async def _fake_mdns(timeout: float = 4.0) -> list[dict]:
        return [
            {
                "ip": "10.0.0.7",
                "hostname": "from-mdns",
                "mac": None,
                "os": None,
                "open_ports": [],
            }
        ]

    with (
        patch.object(scanner, "_nmap_scan", _fake_nmap),
        patch.object(scanner, "_mdns_discover", _fake_mdns),
    ):
        devices = await scanner.run_scan(["10.0.0.0/24"])

    assert len(devices) == 1
    assert devices[0]["discovery_source"] == "nmap"
    assert devices[0]["hostname"] == "from-nmap"


@pytest.mark.asyncio
async def test_run_scan_cancel_short_circuits() -> None:
    """Cancelling a run_id prevents further hosts from being processed."""

    async def _fake_nmap(target: str) -> list[dict]:
        scanner.request_cancel("test-run")
        return [
            {"ip": "10.0.0.5", "hostname": None, "mac": None, "os": None, "open_ports": []}
        ]

    async def _no_mdns(timeout: float = 4.0) -> list[dict]:
        return []

    with (
        patch.object(scanner, "_nmap_scan", _fake_nmap),
        patch.object(scanner, "_mdns_discover", _no_mdns),
    ):
        devices = await scanner.run_scan(["10.0.0.0/24"], run_id="test-run")

    assert devices == []


def test_request_cancel_records_run_id() -> None:
    """request_cancel marks the run_id; _is_cancelled reflects it."""
    scanner.request_cancel("my-run")
    assert scanner._is_cancelled("my-run") is True
    # cleanup
    with scanner._cancelled_lock:
        scanner._cancelled_runs.discard("my-run")
