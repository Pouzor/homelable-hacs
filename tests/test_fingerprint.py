"""Tests for the fingerprint module."""
from custom_components.homelable.fingerprint import (
    fingerprint_ports,
    match_port,
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
