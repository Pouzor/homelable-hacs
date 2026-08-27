"""Tests for the fingerprint module."""
from custom_components.homelable import fingerprint
from custom_components.homelable.fingerprint import (
    fingerprint_ports,
    match_port,
    match_service,
    preload,
    suggest_node_type,
    suggest_type_from_mac,
)


def test_match_port_known_signature_returns_match() -> None:
    sig = match_port(22, "tcp")
    assert sig is not None
    assert "service_name" in sig


def test_match_port_unknown_returns_none() -> None:
    assert match_port(64999, "tcp") is None


def test_fingerprint_ports_includes_unknown_ports_as_unknown_service() -> None:
    result = fingerprint_ports([{"port": 64999, "protocol": "tcp"}])
    assert len(result) == 1
    assert result[0]["service_name"] == "TCP/64999"


def test_suggest_type_from_mac_proxmox_oui() -> None:
    assert suggest_type_from_mac("BC:24:11:01:02:03") == "vm"


def test_suggest_type_from_mac_unknown_oui_returns_none() -> None:
    assert suggest_type_from_mac("00:11:22:33:44:55") is None


def test_suggest_type_from_mac_none_returns_none() -> None:
    assert suggest_type_from_mac(None) is None


def test_suggest_node_type_proxmox_port() -> None:
    """Port 8006 → proxmox host."""
    ports = [{"port": 8006, "protocol": "tcp"}]
    assert suggest_node_type(ports) == "proxmox"


def test_suggest_node_type_iot_mac_wins_over_http() -> None:
    """Shelly MAC OUI overrides generic HTTP signal."""
    ports = [{"port": 80, "protocol": "tcp"}]
    assert suggest_node_type(ports, mac="34:94:54:11:22:33") == "iot"


def test_suggest_node_type_no_signal_falls_back_to_generic() -> None:
    assert suggest_node_type([]) == "generic"


def test_preload_populates_cache_so_match_port_does_no_io() -> None:
    """preload() warms the cache; subsequent matches must not touch the file.

    The scanner calls preload() in a thread before enrichment so the blocking
    file read never runs on the event loop (HA blocking-call detector).
    """
    fingerprint._SIGNATURES = None
    preload()
    assert fingerprint._SIGNATURES is not None

    # With the cache warm, a match must not re-open the file.
    import builtins

    def _boom(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("match_port should not open files after preload()")

    original_open = builtins.open
    builtins.open = _boom
    try:
        assert match_port(22, "tcp") is not None
    finally:
        builtins.open = original_open


# ── OUI vendor detection ──────────────────────────────────────────────────────

def test_suggest_type_from_mac_mikrotik_returns_router() -> None:
    # The motivating case: MikroTik MAC should be recognized as a router
    assert suggest_type_from_mac("4c:5e:0c:11:22:33") == "router"
    assert suggest_type_from_mac("b8:69:f4:aa:bb:cc") == "router"


def test_suggest_type_from_mac_ubiquiti_returns_ap() -> None:
    # Ubiquiti makes routers, switches, APs, cameras — most homelab gear is UniFi APs,
    # so OUI defaults to "ap". Port hints can still upgrade to "router" if BGP/VPN open.
    assert suggest_type_from_mac("24:a4:3c:11:22:33") == "ap"
    assert suggest_type_from_mac("fc:ec:da:aa:bb:cc") == "ap"


def test_suggest_type_from_mac_synology_returns_nas() -> None:
    assert suggest_type_from_mac("00:11:32:11:22:33") == "nas"


def test_suggest_type_from_mac_qnap_returns_nas() -> None:
    assert suggest_type_from_mac("24:5e:be:aa:bb:cc") == "nas"


def test_suggest_type_from_mac_hikvision_returns_camera() -> None:
    assert suggest_type_from_mac("28:57:be:11:22:33") == "camera"


def test_suggest_type_from_mac_dahua_returns_camera() -> None:
    assert suggest_type_from_mac("3c:ef:8c:aa:bb:cc") == "camera"


def test_suggest_type_from_mac_cisco_returns_switch() -> None:
    assert suggest_type_from_mac("b8:38:61:11:22:33") == "switch"


def test_suggest_type_from_mac_raspberry_pi_returns_server() -> None:
    assert suggest_type_from_mac("b8:27:eb:11:22:33") == "server"


def test_suggest_type_from_mac_handles_uppercase() -> None:
    # MACs may arrive in any case; lookup must be case-insensitive
    assert suggest_type_from_mac("4C:5E:0C:11:22:33") == "router"


def test_suggest_type_from_mac_unknown_oui_returns_none_extended() -> None:
    assert suggest_type_from_mac("00:00:01:11:22:33") is None


def test_suggest_node_type_mikrotik_mac_returns_router_no_ports() -> None:
    # MikroTik device with no scanned ports should still be classified as router via MAC
    assert suggest_node_type([], mac="4c:5e:0c:11:22:33") == "router"


def test_suggest_node_type_synology_mac_with_http_returns_nas() -> None:
    # NAS priority beats server, so a Synology MAC + open HTTP → nas
    result = suggest_node_type(
        [{"port": 80, "protocol": "tcp"}],
        mac="00:11:32:11:22:33",
    )
    assert result == "nas"


def test_suggest_node_type_ubiquiti_mac_with_bgp_upgrades_to_router() -> None:
    # Ubiquiti OUI suggests "ap", but BGP port hint upgrades to "router" (higher priority)
    result = suggest_node_type(
        [{"port": 179, "protocol": "tcp"}],
        mac="24:a4:3c:11:22:33",
    )
    assert result == "router"


# ── Tiered service matching (deep-scan HTTP probe) ────────────────────────────

def test_match_service_no_probe_matches_pre_probe_behaviour() -> None:
    """Without http_signals, match_service behaves like the old port-only match."""
    assert match_service(22, "tcp") == match_port(22, "tcp")


def test_match_service_port_agnostic_matches_on_http_title() -> None:
    """A port:null signature matches on the probe title regardless of port."""
    signals = {"title": "Jellyfin", "headers": {}}
    sig = match_service(38096, "tcp", http_signals=signals)
    assert sig is not None
    assert sig["service_name"] == "Jellyfin"


def test_match_service_port_agnostic_ignored_without_probe() -> None:
    """port:null signatures never match when no probe ran (no regression)."""
    assert match_service(38096, "tcp") is None


def test_match_service_http_regex_beats_port_only() -> None:
    """Tier 1 (port + http_regex) wins over a generic port-only guess on 80."""
    signals = {"title": "Home Assistant", "headers": {}}
    sig = match_service(80, "tcp", http_signals=signals)
    assert sig is not None
    assert sig["service_name"] == "Home Assistant"


def test_match_service_http_signal_from_server_header() -> None:
    """The Server header feeds the http_regex haystack too."""
    signals = {"title": None, "headers": {"Server": "Portainer"}}
    sig = match_service(59000, "tcp", http_signals=signals)
    assert sig is not None
    assert sig["service_name"] == "Portainer"


def test_fingerprint_ports_uses_http_signals() -> None:
    """fingerprint_ports threads http_signals into the matcher."""
    result = fingerprint_ports(
        [{"port": 40000, "protocol": "tcp", "http_signals": {"title": "Grafana", "headers": {}}}]
    )
    assert result[0]["service_name"] == "Grafana"


def test_port_9100_is_labelled_ambiguously() -> None:
    """9100 is raw print far more often than node_exporter, and the two can't be
    told apart without writing to the socket — which prints a page (#87)."""
    sig = match_port(9100, "tcp")
    assert sig is not None
    assert sig["service_name"] == "Raw Print / Node Exporter"
    assert sig["icon"] == "printer"


def test_port_9100_suggests_no_node_type() -> None:
    """A bare 9100 host stays generic rather than being guessed a server."""
    assert suggest_node_type([{"port": 9100, "protocol": "tcp"}]) == "generic"
