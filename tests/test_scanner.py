"""Tests for the scanner module."""
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.homelable import scanner, tcp_scanner


@pytest.mark.asyncio
async def test_run_scan_invalid_cidr_raises() -> None:
    """An invalid CIDR range surfaces as ValueError."""
    with pytest.raises(ValueError, match="Invalid CIDR range"):
        await scanner.run_scan(["not-a-cidr"])


@pytest.mark.asyncio
async def test_run_scan_returns_enriched_devices() -> None:
    """Scan output carries services + suggested_type + discovery_source."""

    async def _fake_scan(target: str, **_kwargs) -> list[dict]:
        return [
            {
                "ip": "10.0.0.5",
                "hostname": "dev.local",
                "mac": "AA:BB:CC:DD:EE:FF",
                "os": None,
                "open_ports": [{"port": 22, "protocol": "tcp", "banner": "OpenSSH"}],
            }
        ]

    async def _no_mdns(*_args, **_kwargs) -> list[dict]:
        return []

    with (
        patch.object(scanner, "_scan_target", _fake_scan),
        patch.object(scanner, "_mdns_discover", _no_mdns),
    ):
        devices = await scanner.run_scan(["10.0.0.0/24"])

    assert len(devices) == 1
    dev = devices[0]
    assert dev["ip"] == "10.0.0.5"
    assert dev["discovery_source"] == "tcp"
    assert "services" in dev
    assert "suggested_type" in dev


@pytest.mark.asyncio
async def test_run_scan_excludes_ips() -> None:
    """IPs in exclude_ips are skipped."""

    async def _fake_scan(target: str, **_kwargs) -> list[dict]:
        return [
            {"ip": "10.0.0.5", "hostname": None, "mac": None, "os": None, "open_ports": []},
            {"ip": "10.0.0.6", "hostname": None, "mac": None, "os": None, "open_ports": []},
        ]

    async def _no_mdns(*_args, **_kwargs) -> list[dict]:
        return []

    with (
        patch.object(scanner, "_scan_target", _fake_scan),
        patch.object(scanner, "_mdns_discover", _no_mdns),
    ):
        devices = await scanner.run_scan(
            ["10.0.0.0/24"], exclude_ips={"10.0.0.5"}
        )

    assert [d["ip"] for d in devices] == ["10.0.0.6"]


@pytest.mark.asyncio
async def test_run_scan_dedups_across_sources() -> None:
    """Same IP from TCP scan + mdns surfaces once (TCP wins)."""

    async def _fake_scan(target: str, **_kwargs) -> list[dict]:
        return [
            {
                "ip": "10.0.0.7",
                "hostname": "from-tcp",
                "mac": "AA:BB:CC:00:00:01",
                "os": None,
                "open_ports": [],
            }
        ]

    async def _fake_mdns(*_args, **_kwargs) -> list[dict]:
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
        patch.object(scanner, "_scan_target", _fake_scan),
        patch.object(scanner, "_mdns_discover", _fake_mdns),
    ):
        devices = await scanner.run_scan(["10.0.0.0/24"])

    assert len(devices) == 1
    assert devices[0]["discovery_source"] == "tcp"
    assert devices[0]["hostname"] == "from-tcp"


@pytest.mark.asyncio
async def test_run_scan_cancel_short_circuits() -> None:
    """Cancelling a run_id prevents further hosts from being processed."""

    async def _fake_scan(target: str, **_kwargs) -> list[dict]:
        scanner.request_cancel("test-run")
        return [
            {"ip": "10.0.0.5", "hostname": None, "mac": None, "os": None, "open_ports": []}
        ]

    async def _no_mdns(*_args, **_kwargs) -> list[dict]:
        return []

    with (
        patch.object(scanner, "_scan_target", _fake_scan),
        patch.object(scanner, "_mdns_discover", _no_mdns),
    ):
        devices = await scanner.run_scan(["10.0.0.0/24"], run_id="test-run")

    assert devices == []


@pytest.mark.asyncio
async def test_phase2_dispatches_to_tcp_connect_scan() -> None:
    """_phase2_port_scan delegates per-host work to tcp_connect_scan."""
    alive = {
        "10.0.0.5": {"ip": "10.0.0.5", "hostname": None, "mac": None, "os": None, "open_ports": []},
    }

    async def _fake_tcp(host: dict, ports: tuple[int, ...], **_kw) -> dict:
        host["open_ports"] = [{"port": 80, "protocol": "tcp", "banner": "nginx"}]
        return host

    with patch.object(scanner, "tcp_connect_scan", _fake_tcp):
        results = await scanner._phase2_port_scan(alive)

    assert len(results) == 1
    assert results[0]["open_ports"] == [{"port": 80, "protocol": "tcp", "banner": "nginx"}]


@pytest.mark.asyncio
async def test_phase2_invokes_on_host_done_callback() -> None:
    """Per-host callback fires once tcp_connect_scan returns."""
    alive = {
        "10.0.0.5": {"ip": "10.0.0.5", "hostname": None, "mac": None, "os": None, "open_ports": []},
    }
    seen: list[dict] = []

    async def _fake_tcp(host: dict, ports: tuple[int, ...], **_kw) -> dict:
        host["open_ports"] = [{"port": 22, "protocol": "tcp", "banner": ""}]
        return host

    async def _on_done(host: dict) -> None:
        seen.append(host)

    with patch.object(scanner, "tcp_connect_scan", _fake_tcp):
        await scanner._phase2_port_scan(alive, on_host_done=_on_done)

    assert len(seen) == 1
    assert seen[0]["ip"] == "10.0.0.5"
    assert seen[0]["open_ports"][0]["port"] == 22


# ── Deep scan (extra port ranges + HTTP probe) ────────────────────────────────

def test_valid_port_range_accepts_single_and_range() -> None:
    assert scanner._valid_port_range("8080")
    assert scanner._valid_port_range("8000-8100")
    assert not scanner._valid_port_range("0")
    assert not scanner._valid_port_range("70000")
    assert not scanner._valid_port_range("8100-8000")  # inverted
    assert not scanner._valid_port_range("abc")


def test_build_port_list_appends_extra_ranges_without_dupes() -> None:
    """Extra ranges expand and append; ports already in the base list are skipped."""
    result = scanner._build_port_list(["8000-8002", "80"])
    # 80 is already in the base list → not duplicated; 8000 too. 8001/8002 are new.
    assert result[: len(scanner._PORT_LIST)] == scanner._PORT_LIST
    extra = result[len(scanner._PORT_LIST):]
    assert 80 not in extra
    assert set(extra) == {8001, 8002}


def test_build_port_list_empty_is_base() -> None:
    assert scanner._build_port_list(None) == scanner._PORT_LIST
    assert scanner._build_port_list([]) == scanner._PORT_LIST


@pytest.mark.asyncio
async def test_run_scan_deep_scan_passes_extended_port_list() -> None:
    """Deep-scan extra ranges reach _scan_target as an extended port_list."""
    seen_ports: dict[str, tuple[int, ...]] = {}

    async def _fake_scan(target: str, *, port_list=scanner._PORT_LIST, **_kwargs) -> list[dict]:
        seen_ports["ports"] = port_list
        return []

    async def _no_mdns(*_args, **_kwargs) -> list[dict]:
        return []

    with (
        patch.object(scanner, "_scan_target", _fake_scan),
        patch.object(scanner, "_mdns_discover", _no_mdns),
    ):
        await scanner.run_scan(
            ["10.0.0.0/24"],
            deep_scan=scanner.DeepScanOptions(http_ranges=["9000-9001"]),
        )

    assert 9000 in seen_ports["ports"]
    assert 9001 in seen_ports["ports"]


@pytest.mark.asyncio
async def test_run_scan_http_probe_identifies_custom_port_service() -> None:
    """With the probe on, a port:null http_regex signature matches a custom port."""

    async def _fake_scan(target: str, **_kwargs) -> list[dict]:
        return [
            {
                "ip": "10.0.0.5",
                "hostname": None,
                "mac": None,
                "os": None,
                "open_ports": [{"port": 39000, "protocol": "tcp", "banner": ""}],
            }
        ]

    async def _no_mdns(*_args, **_kwargs) -> list[dict]:
        return []

    async def _fake_probe(ip, open_ports, verify_tls=False):  # noqa: ANN001, ANN201
        return [{**p, "http_signals": {"title": "Jellyfin", "headers": {}}} for p in open_ports]

    with (
        patch.object(scanner, "_scan_target", _fake_scan),
        patch.object(scanner, "_mdns_discover", _no_mdns),
        patch.object(scanner, "probe_open_ports", _fake_probe),
    ):
        devices = await scanner.run_scan(
            ["10.0.0.0/24"],
            deep_scan=scanner.DeepScanOptions(http_probe_enabled=True),
        )

    names = [s["service_name"] for s in devices[0]["services"]]
    assert "Jellyfin" in names


@pytest.mark.asyncio
async def test_run_scan_probe_not_called_when_disabled() -> None:
    """No probe runs on a standard scan — behaviour is unchanged."""
    called = {"n": 0}

    async def _fake_scan(target: str, **_kwargs) -> list[dict]:
        return [
            {
                "ip": "10.0.0.5",
                "hostname": None,
                "mac": None,
                "os": None,
                "open_ports": [{"port": 39000, "protocol": "tcp", "banner": ""}],
            }
        ]

    async def _no_mdns(*_args, **_kwargs) -> list[dict]:
        return []

    async def _spy_probe(ip, open_ports, verify_tls=False):  # noqa: ANN001, ANN201
        called["n"] += 1
        return open_ports

    with (
        patch.object(scanner, "_scan_target", _fake_scan),
        patch.object(scanner, "_mdns_discover", _no_mdns),
        patch.object(scanner, "probe_open_ports", _spy_probe),
    ):
        await scanner.run_scan(["10.0.0.0/24"])

    assert called["n"] == 0


def test_request_cancel_records_run_id() -> None:
    """request_cancel marks the run_id; _is_cancelled reflects it."""
    scanner.request_cancel("my-run")
    assert scanner._is_cancelled("my-run") is True
    # cleanup
    with scanner._cancelled_lock:
        scanner._cancelled_runs.discard("my-run")


# ── Fan-out caps (issue #73) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_phase2_caps_total_open_sockets() -> None:
    """Every host shares one socket budget, so hosts x ports can't multiply.

    Per-host budgets let 10 parallel hosts hold 10 x 50 sockets at once, which
    is the fan-out that starves small hosts and gets HA watchdog-restarted.
    """
    import asyncio

    alive = {
        f"10.0.0.{i}": {
            "ip": f"10.0.0.{i}", "hostname": None, "mac": None,
            "os": None, "open_ports": [],
        }
        for i in range(1, 41)
    }
    ports = tuple(range(1, 61))

    in_flight = 0
    peak = 0

    async def _fake_connect(ip, port, **_kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return None

    with patch.object(tcp_scanner, "_scan_port", _fake_connect):
        results = await scanner._phase2_port_scan(alive, port_list=ports)

    assert len(results) == len(alive)
    assert peak <= scanner._SOCKET_CONCURRENCY
    assert peak > 1  # still concurrent, not serialised


@pytest.mark.asyncio
async def test_phase2_host_concurrency_is_capped() -> None:
    """No more than _HOST_CONCURRENCY hosts are scanned at once."""
    import asyncio

    alive = {
        f"10.0.0.{i}": {
            "ip": f"10.0.0.{i}", "hostname": None, "mac": None,
            "os": None, "open_ports": [],
        }
        for i in range(1, 26)
    }

    active = 0
    peak = 0

    async def _fake_tcp(host: dict, ports, **_kw) -> dict:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return host

    with patch.object(scanner, "tcp_connect_scan", _fake_tcp):
        await scanner._phase2_port_scan(alive)

    assert peak <= scanner._HOST_CONCURRENCY


@pytest.mark.asyncio
async def test_ping_sweep_concurrency_is_capped() -> None:
    """The ping sweep forks a process per address; cap how many run at once."""
    import asyncio

    active = 0
    peak = 0

    class _FakeProc:
        returncode = 0

        async def wait(self) -> None:
            await asyncio.sleep(0)

    async def _fake_exec(*_args, **_kw):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return _FakeProc()

    with (
        patch.object(asyncio, "create_subprocess_exec", _fake_exec),
        patch.object(scanner, "_arp_table_hosts", lambda _t: {}),
        patch.object(scanner, "_resolve_hostname", lambda _ip: None),
    ):
        alive = await scanner._ping_sweep("10.0.0.0/24")

    assert len(alive) == 254
    assert peak <= scanner._PING_CONCURRENCY


@pytest.mark.asyncio
async def test_tcp_sweep_fallback_probes_before_full_port_list() -> None:
    """With ping/ARP blind, the sweep probes a small port set across the range
    and only spends the full port list on addresses that answered."""
    calls: list[tuple[str, tuple[int, ...]]] = []

    async def _fake_tcp(host: dict, ports, **_kw) -> dict:
        calls.append((host["ip"], tuple(ports)))
        # Exactly one address in the range is up.
        host["open_ports"] = (
            [{"port": 80, "protocol": "tcp", "banner": ""}]
            if host["ip"] == "10.0.0.7"
            else []
        )
        return host

    with (
        patch.object(scanner, "_ping_sweep", AsyncMock(return_value={})),
        patch.object(scanner, "tcp_connect_scan", _fake_tcp),
        patch.object(scanner, "_resolve_hostname", lambda _ip: "host.lan"),
    ):
        results = await scanner._scan_target("10.0.0.0/28")

    probe_calls = [c for c in calls if c[1] == scanner._FALLBACK_PROBE_PORTS]
    full_calls = [c for c in calls if c[1] == scanner._PORT_LIST]
    # Every address gets the cheap probe...
    assert len(probe_calls) == 14
    # ...only the responder gets the full port list.
    assert [c[0] for c in full_calls] == ["10.0.0.7"]
    assert [r["ip"] for r in results] == ["10.0.0.7"]


@pytest.mark.asyncio
async def test_tcp_sweep_fallback_returns_early_when_range_is_empty() -> None:
    """Nothing answers the probe → no second pass at all."""
    calls: list[tuple[int, ...]] = []

    async def _fake_tcp(host: dict, ports, **_kw) -> dict:
        calls.append(tuple(ports))
        host["open_ports"] = []
        return host

    with (
        patch.object(scanner, "_ping_sweep", AsyncMock(return_value={})),
        patch.object(scanner, "tcp_connect_scan", _fake_tcp),
    ):
        results = await scanner._scan_target("10.0.0.0/29")

    assert results == []
    assert all(c == scanner._FALLBACK_PROBE_PORTS for c in calls)


@pytest.mark.asyncio
async def test_tcp_sweep_fallback_keeps_probe_only_ports() -> None:
    """The probe set is not a subset of the full port list (139 is probe-only).

    A host whose only open port is probe-only was found in stage A and then
    dropped, because stage B rescans with the full list and the sweep filters
    on the result. Stage A's ports are merged back instead.
    """
    async def _fake_tcp(host: dict, ports, **_kw) -> dict:
        if tuple(ports) == scanner._FALLBACK_PROBE_PORTS:
            host["open_ports"] = (
                [{"port": 139, "protocol": "tcp", "banner": ""}]
                if host["ip"] == "10.0.0.3"
                else []
            )
        else:
            # Full port list has no 139, so this host looks closed on stage B.
            host["open_ports"] = []
        return host

    with (
        patch.object(scanner, "_ping_sweep", AsyncMock(return_value={})),
        patch.object(scanner, "tcp_connect_scan", _fake_tcp),
        patch.object(scanner, "_resolve_hostname", lambda _ip: "smb.lan"),
    ):
        results = await scanner._scan_target("10.0.0.0/28")

    assert [r["ip"] for r in results] == ["10.0.0.3"]
    assert [p["port"] for p in results[0]["open_ports"]] == [139]


@pytest.mark.asyncio
async def test_tcp_sweep_fallback_prefers_full_scan_port_entry() -> None:
    """A port found by both passes keeps the full scan's richer banner."""
    async def _fake_tcp(host: dict, ports, **_kw) -> dict:
        if tuple(ports) == scanner._FALLBACK_PROBE_PORTS:
            host["open_ports"] = [{"port": 80, "protocol": "tcp", "banner": ""}]
        else:
            host["open_ports"] = [
                {"port": 80, "protocol": "tcp", "banner": "nginx"},
                {"port": 8080, "protocol": "tcp", "banner": ""},
            ]
        return host

    with (
        patch.object(scanner, "_ping_sweep", AsyncMock(return_value={})),
        patch.object(scanner, "tcp_connect_scan", _fake_tcp),
        patch.object(scanner, "_resolve_hostname", lambda _ip: "web.lan"),
    ):
        results = await scanner._scan_target("10.0.0.0/30")

    ports = results[0]["open_ports"]
    assert [p["port"] for p in ports] == [80, 8080]
    assert ports[0]["banner"] == "nginx"


def test_merge_ports_is_a_noop_without_extras() -> None:
    host = {"ip": "10.0.0.1", "open_ports": [{"port": 22, "protocol": "tcp"}]}
    scanner._merge_ports(host, [])
    assert host["open_ports"] == [{"port": 22, "protocol": "tcp"}]
