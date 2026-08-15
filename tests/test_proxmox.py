"""Tests for the Proxmox VE import (service + coordinator + WS)."""
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant

from custom_components.homelable import proxmox
from custom_components.homelable.const import DOMAIN
from custom_components.homelable.coordinator import HomelableCoordinator
from custom_components.homelable.websocket import async_register_websocket_commands

# ─── Service: pure helpers ────────────────────────────────────────────────────


def test_normalize_mac() -> None:
    assert proxmox.normalize_mac("BC:24:11:AA:BB:CC") == "bc:24:11:aa:bb:cc"
    assert proxmox.normalize_mac("bc-24-11-aa-bb-cc") == "bc:24:11:aa:bb:cc"
    assert proxmox.normalize_mac("  ") is None
    assert proxmox.normalize_mac(None) is None


def test_build_proxmox_properties_specs_and_source() -> None:
    node = {
        "vmid": 101,
        "model": "LXC",
        "cpu_count": 4,
        "ram_gb": 8.0,
        "disk_gb": 40.0,
    }
    props = proxmox.build_proxmox_properties(node)
    by_key = {p["key"]: p["value"] for p in props}
    assert by_key["VMID"] == "101"
    assert by_key["CPU Cores"] == "4"
    assert by_key["RAM"] == "8.0 GB"
    assert by_key["Source"] == "Proxmox VE"
    # Every row is hidden by default; the user opts in.
    assert all(p["visible"] is False for p in props)


def test_build_proxmox_cluster_links_chains_hosts() -> None:
    nodes = [
        {"type": "proxmox", "ieee_address": "pve-node-a"},
        {"type": "vm", "ieee_address": "pve-a-100"},
        {"type": "proxmox", "ieee_address": "pve-node-b"},
        {"type": "proxmox", "ieee_address": "pve-node-c"},
    ]
    assert proxmox.build_proxmox_cluster_links(nodes) == [
        ("pve-node-a", "pve-node-b"),
        ("pve-node-b", "pve-node-c"),
    ]
    # A single host has no cluster edge.
    assert proxmox.build_proxmox_cluster_links(nodes[:1]) == []


def test_guest_visibility_advisory() -> None:
    hosts_only = [{"type": "proxmox"}, {"type": "proxmox"}]
    assert "no VMs or LXC were visible" in proxmox.guest_visibility_advisory(hosts_only)
    with_guests = [{"type": "proxmox"}, {"type": "vm"}]
    assert proxmox.guest_visibility_advisory(with_guests) is None
    assert proxmox.guest_visibility_advisory([]) is None


def test_extract_net_mac_from_qemu_and_lxc_config() -> None:
    qemu = {"net0": "virtio=BC:24:11:AA:BB:CC,bridge=vmbr0"}
    lxc = {"net0": "name=eth0,bridge=vmbr0,hwaddr=BC:24:11:11:22:33,ip=10.0.0.5/24"}
    assert proxmox._extract_net_mac(qemu) == "bc:24:11:aa:bb:cc"
    assert proxmox._extract_net_mac(lxc) == "bc:24:11:11:22:33"
    assert proxmox._extract_lxc_ip(lxc) == "10.0.0.5"
    assert proxmox._extract_net_mac({"net0": "dhcp"}) is None


# ─── Service: fetch + test-connection (mocked transport) ──────────────────────


def _proxmox_json(url: str):
    """Canned Proxmox REST responses keyed on the request path."""
    if url.endswith("/nodes"):
        return [{"node": "pve1", "status": "online", "maxcpu": 8, "maxmem": 2 * 1024**3, "maxdisk": 100 * 1024**3}]
    if url.endswith("/cluster/status"):
        return [
            {"type": "cluster", "name": "homelab", "quorate": 1},
            {"type": "node", "name": "pve1", "ip": "10.0.0.2", "online": 1},
        ]
    if url.endswith("/nodes/pve1/qemu"):
        return [{"vmid": 100, "name": "web", "status": "running", "maxcpu": 2, "maxmem": 1024**3}]
    if url.endswith("/nodes/pve1/lxc"):
        return [{"vmid": 200, "name": "db", "status": "stopped"}]
    if url.endswith("/qemu/100/config"):
        return {"net0": "virtio=BC:24:11:00:00:01,bridge=vmbr0"}
    if url.endswith("/qemu/100/agent/network-get-interfaces"):
        return {"result": [{"ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "10.0.0.100"}]}]}
    if url.endswith("/lxc/200/config"):
        return {"net0": "name=eth0,hwaddr=BC:24:11:00:00:02,ip=10.0.0.200/24"}
    return None


async def test_fetch_proxmox_inventory_maps_hosts_and_guests(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.homelable.proxmox._get_json",
        AsyncMock(side_effect=lambda _s, url, _h, _v: _proxmox_json(url)),
    ), patch("custom_components.homelable.proxmox.async_get_clientsession", MagicMock()):
        nodes, edges = await proxmox.fetch_proxmox_inventory(
            hass, "pve.lan", 8006, "u@pam!t", "secret", verify_tls=False
        )

    by_type = {n["type"] for n in nodes}
    assert by_type == {"proxmox", "vm", "lxc"}
    host = next(n for n in nodes if n["type"] == "proxmox")
    # /nodes carries no address — the IP comes from /cluster/status, and it is
    # the only key that can merge this host with its ARP-scanned twin.
    assert host["ip"] == "10.0.0.2"
    vm = next(n for n in nodes if n["type"] == "vm")
    assert vm["ip"] == "10.0.0.100"
    assert vm["mac"] == "bc:24:11:00:00:01"
    lxc = next(n for n in nodes if n["type"] == "lxc")
    # Stopped LXC still yields ip + mac agent-free from net0.
    assert lxc["ip"] == "10.0.0.200"
    assert lxc["mac"] == "bc:24:11:00:00:02"
    # One host→guest edge per guest.
    assert {(e["source"], e["target"]) for e in edges} == {
        ("pve-node-pve1", "pve-pve1-100"),
        ("pve-node-pve1", "pve-pve1-200"),
    }


async def _fetch_with(hass: HomeAssistant, json_fn, host: str = "pve.lan"):  # noqa: ANN001
    """Run fetch_proxmox_inventory against canned JSON, return the host node."""
    with patch(
        "custom_components.homelable.proxmox._get_json",
        AsyncMock(side_effect=lambda _s, url, _h, _v: json_fn(url)),
    ), patch("custom_components.homelable.proxmox.async_get_clientsession", MagicMock()):
        nodes, _ = await proxmox.fetch_proxmox_inventory(
            hass, host, 8006, "u@pam!t", "secret", verify_tls=False
        )
    return next(n for n in nodes if n["type"] == "proxmox")


def _no_cluster_status(url: str):
    """Canned responses where /cluster/status is unreachable (no Sys.Audit)."""
    if url.endswith("/cluster/status"):
        raise aiohttp.ClientError("403 Forbidden")
    return _proxmox_json(url)


async def test_fetch_falls_back_to_configured_ip_when_cluster_status_fails(
    hass: HomeAssistant,
) -> None:
    # A token that can list /nodes but not /cluster/status: the configured host
    # is an IP literal and only one node is unresolved, so it is that node's.
    host = await _fetch_with(hass, _no_cluster_status, host="10.0.0.2")
    assert host["ip"] == "10.0.0.2"


async def test_fetch_leaves_host_ip_none_when_configured_host_is_a_hostname(
    hass: HomeAssistant,
) -> None:
    # No /cluster/status and a non-literal host: nothing to infer, but the
    # import still succeeds with the host node present.
    host = await _fetch_with(hass, _no_cluster_status, host="pve.lan")
    assert host["ip"] is None


async def test_fetch_does_not_guess_ip_with_several_unresolved_hosts(
    hass: HomeAssistant,
) -> None:
    def _json(url: str):
        if url.endswith("/nodes"):
            return [
                {"node": "pve1", "status": "offline"},
                {"node": "pve2", "status": "offline"},
            ]
        if url.endswith("/cluster/status"):
            raise aiohttp.ClientError("403 Forbidden")
        return _proxmox_json(url)

    with patch(
        "custom_components.homelable.proxmox._get_json",
        AsyncMock(side_effect=lambda _s, url, _h, _v: _json(url)),
    ), patch("custom_components.homelable.proxmox.async_get_clientsession", MagicMock()):
        nodes, _ = await proxmox.fetch_proxmox_inventory(
            hass, "10.0.0.2", 8006, "u@pam!t", "secret", verify_tls=False
        )
    # Ambiguous — the configured IP belongs to exactly one of them, unknown which.
    assert [n["ip"] for n in nodes] == [None, None]


async def test_fetch_ignores_cluster_status_entries_without_an_ip(
    hass: HomeAssistant,
) -> None:
    def _json(url: str):
        if url.endswith("/cluster/status"):
            return [{"type": "node", "name": "pve1", "online": 0}]
        return _proxmox_json(url)

    host = await _fetch_with(hass, _json, host="pve.lan")
    assert host["ip"] is None


async def test_test_connection_reports_no_permission(hass: HomeAssistant) -> None:
    def _json(url: str):
        if url.endswith("/version"):
            return {"version": "8.1"}
        if url.endswith("/access/permissions"):
            return {}  # empty ACL → token has no permissions
        return None

    with patch(
        "custom_components.homelable.proxmox._get_json",
        AsyncMock(side_effect=lambda _s, url, _h, _v: _json(url)),
    ), patch("custom_components.homelable.proxmox.async_get_clientsession", MagicMock()):
        connected, message = await proxmox.test_proxmox_connection(
            hass, "pve.lan", 8006, "u@pam!t", "secret"
        )
    assert connected is True
    assert "8.1" in message
    assert "no permissions" in message


# ─── Coordinator: import + merge + approve ────────────────────────────────────


def _mock_entry(options: dict | None = None) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "px_entry"
    entry.data = {"scan_ranges": "192.168.1.0/24", "status_interval": 60}
    entry.options = options or {}
    return entry


@pytest.fixture
def coord(hass):  # noqa: ANN001
    return HomelableCoordinator(hass, _mock_entry())


def _host(name: str, ip: str | None = None) -> dict:
    return {
        "ieee_address": f"pve-node-{name}",
        "label": name,
        "type": "proxmox",
        "hostname": name,
        "ip": ip,
        "mac": None,
        "vendor": "Proxmox VE",
        "model": None,
    }


def _guest(host: str, vmid: int, ip=None, mac=None, kind="vm") -> dict:
    return {
        "ieee_address": f"pve-{host}-{vmid}",
        "label": f"{kind}-{vmid}",
        "type": kind,
        "hostname": f"{kind}-{vmid}",
        "ip": ip,
        "mac": mac,
        "vmid": vmid,
        "vendor": "Proxmox VE",
        "model": kind.upper(),
    }


async def test_import_proxmox_pending_creates_rows(coord) -> None:  # noqa: ANN001
    nodes = [_host("a"), _guest("a", 100, ip="10.0.0.100", mac="bc:24:11:00:00:01")]
    edges = [{"source": "pve-node-a", "target": "pve-a-100"}]
    res = await coord.import_proxmox_pending(nodes, edges, [])
    assert res == {"created": 2, "updated": 0, "device_count": 2}

    pending = await coord.list_pending()
    guest = next(p for p in pending if p["ieee_address"] == "pve-a-100")
    assert guest["source"] == "proxmox"
    assert "proxmox" in guest["discovery_sources"]
    assert guest["data_extras"]["proxmox_parent"] == "pve-node-a"
    # Specs carried as hidden property rows.
    assert any(p["key"] == "VMID" for p in guest["properties"])


async def test_import_merges_onto_scanned_row_by_mac(coord) -> None:  # noqa: ANN001
    # A prior IP scan discovered the device by ARP (IP + MAC, no ieee).
    store = await coord._get_pending()
    store["devices"].append(
        {
            "id": "pd-scan",
            "ip": "10.0.0.100",
            "mac": "bc:24:11:00:00:01",
            "hostname": "web.lan",
            "services": [],
            "status": "pending",
            "discovery_source": "arp",
            "discovery_sources": ["arp"],
        }
    )
    # Proxmox imports the same guest — no IP (stopped), same NIC MAC.
    guest = _guest("a", 100, ip=None, mac="bc:24:11:00:00:01")
    res = await coord.import_proxmox_pending([guest], [], [])
    assert res["created"] == 0
    assert res["updated"] == 1

    pending = await coord.list_pending()
    assert len(pending) == 1  # merged, not doubled
    row = pending[0]
    # Both sources retained → shows under both inventory filters.
    assert set(row["discovery_sources"]) >= {"arp", "proxmox"}
    # pve identity + type adopted; scanned IP preserved.
    assert row["data_extras"]["ieee_address"] == "pve-a-100"
    assert row["ip"] == "10.0.0.100"
    assert row["suggested_type"] == "vm"


async def test_import_merges_host_onto_scanned_row_by_ip(coord) -> None:  # noqa: ANN001
    # The PVE host itself was found by the IP scan (ARP: IP + MAC).
    store = await coord._get_pending()
    store["devices"].append(
        {
            "id": "pd-scan-host",
            "ip": "10.0.0.2",
            "mac": "aa:bb:cc:dd:ee:ff",
            "hostname": "pve1.lan",
            "services": [],
            "status": "pending",
            "discovery_source": "arp",
            "discovery_sources": ["arp"],
        }
    )
    # Proxmox imports the same machine. No API exposes a host MAC, so the IP
    # resolved from /cluster/status is the only join key.
    res = await coord.import_proxmox_pending([_host("pve1", ip="10.0.0.2")], [], [])
    assert res["created"] == 0
    assert res["updated"] == 1

    pending = await coord.list_pending()
    assert len(pending) == 1  # merged, not doubled
    row = pending[0]
    assert set(row["discovery_sources"]) >= {"arp", "proxmox"}
    assert row["data_extras"]["ieee_address"] == "pve-node-pve1"
    assert row["mac"] == "aa:bb:cc:dd:ee:ff"  # scanned MAC kept


async def test_approve_proxmox_guest_creates_virtual_edge(coord) -> None:  # noqa: ANN001
    nodes = [_host("a"), _guest("a", 100, ip="10.0.0.100", mac="bc:24:11:00:00:01")]
    edges = [{"source": "pve-node-a", "target": "pve-a-100"}]
    await coord.import_proxmox_pending(nodes, edges, [])
    pending = await coord.list_pending()
    ids = {p["ieee_address"]: p["id"] for p in pending}

    res = await coord.approve_batch([ids["pve-node-a"], ids["pve-a-100"]])
    assert res["approved"] == 2
    canvas = await coord.get_canvas()
    virtual = [e for e in canvas["edges"] if e.get("type") == "virtual"]
    assert len(virtual) == 1
    assert virtual[0]["source"] == "pve-node-a"
    assert virtual[0]["target"] == "pve-a-100"
    # Approved guest node carries the Proxmox spec rows.
    guest_node = next(n for n in canvas["nodes"] if n["id"] == "pve-a-100")
    assert any(p["key"] == "VMID" for p in guest_node["properties"])
    assert guest_node["check_method"] == "ping"  # has IP


async def test_approve_cluster_hosts_creates_cluster_edge(coord) -> None:  # noqa: ANN001
    nodes = [_host("a"), _host("b")]
    pairs = proxmox.build_proxmox_cluster_links(nodes)
    await coord.import_proxmox_pending(nodes, [], pairs)
    pending = await coord.list_pending()
    ids = {p["ieee_address"]: p["id"] for p in pending}

    res = await coord.approve_batch([ids["pve-node-a"], ids["pve-node-b"]])
    assert res["approved"] == 2
    canvas = await coord.get_canvas()
    cluster = [e for e in canvas["edges"] if e.get("type") == "cluster"]
    assert len(cluster) == 1
    assert cluster[0]["sourceHandle"] == "right"
    assert cluster[0]["targetHandle"] == "left"
    # Both hosts got left + right connection points for the cluster edge.
    # An approved node's id is its pve ieee_address.
    for ieee in ("pve-node-a", "pve-node-b"):
        node = next(n for n in canvas["nodes"] if n["id"] == ieee)
        assert node["left_handles"] >= 1
        assert node["right_handles"] >= 1


async def test_approve_three_cluster_hosts_chains_distinct_handles(coord) -> None:  # noqa: ANN001
    # Regression: a 3-host cluster must chain a-b-c, each consecutive pair on a
    # distinct handle. Approving the MIDDLE host LAST previously fired both its
    # edges from the same 'right' handle instead of chaining.
    nodes = [_host("a"), _host("b"), _host("c")]
    pairs = proxmox.build_proxmox_cluster_links(nodes)
    await coord.import_proxmox_pending(nodes, [], pairs)
    pending = await coord.list_pending()
    ids = {p["ieee_address"]: p["id"] for p in pending}

    # Approve with the middle host (b) LAST.
    await coord.approve_batch([ids["pve-node-a"], ids["pve-node-c"], ids["pve-node-b"]])
    canvas = await coord.get_canvas()
    cluster = [e for e in canvas["edges"] if e.get("type") == "cluster"]
    # Two chain links (a->b, b->c), not a full mesh.
    assert len(cluster) == 2
    assert {(e["source"], e["target"]) for e in cluster} == {
        ("pve-node-a", "pve-node-b"),
        ("pve-node-b", "pve-node-c"),
    }
    # No two cluster edges share the same node+handle endpoint — the middle host
    # is a source on its right and a target on its left, never both on 'right'.
    endpoints = [(e["source"], e["sourceHandle"]) for e in cluster] + [
        (e["target"], e["targetHandle"]) for e in cluster
    ]
    assert len(endpoints) == len(set(endpoints))


async def test_approve_same_hosts_onto_second_design_still_draws_edges(coord) -> None:  # noqa: ANN001
    # Regression (port of homelable #254): approving the same mesh/cluster
    # devices onto a SECOND canvas must also draw their edges. In the standalone
    # repo a shared pending_device_link row was consumed by the first approval,
    # so later approvals found no topology. HACS carries topology on each node's
    # own data and resolves against the target design's canvas, so a re-approve
    # onto another design must resolve independently. This guards that property.
    second = await coord.create_design("Second")
    default = (await coord.list_designs())[0]["id"]
    nodes = [_host("a"), _host("b")]
    pairs = proxmox.build_proxmox_cluster_links(nodes)
    await coord.import_proxmox_pending(nodes, [], pairs)
    pending = await coord.list_pending()
    ids = {p["ieee_address"]: p["id"] for p in pending}

    first = await coord.approve_batch(
        [ids["pve-node-a"], ids["pve-node-b"]], {"design_id": default}
    )
    # Same two devices approved onto the second canvas.
    dupe = await coord.approve_batch(
        [ids["pve-node-a"], ids["pve-node-b"]], {"design_id": second["id"]}
    )

    # Both approvals place the nodes AND draw the cluster edge — the second is
    # not starved of topology by the first.
    assert first["approved"] == 2
    assert dupe["approved"] == 2
    for did in (default, second["id"]):
        canvas = await coord.get_canvas(did)
        cluster = [e for e in canvas["edges"] if e.get("type") == "cluster"]
        assert len(cluster) == 1
        # The edge joins THAT canvas's own nodes, never another design's.
        node_ids = {n["id"] for n in canvas["nodes"]}
        assert cluster[0]["source"] in node_ids
        assert cluster[0]["target"] in node_ids


# ─── Coordinator: config resolution ───────────────────────────────────────────


async def test_resolve_proxmox_request_falls_back_to_config(hass) -> None:  # noqa: ANN001
    entry = _mock_entry(
        {
            "proxmox_host": "pve.lan",
            "proxmox_port": 8006,
            "proxmox_token_id": "u@pam!t",
            "proxmox_token_secret": "secret",
            "proxmox_verify_tls": False,
        }
    )
    coord = HomelableCoordinator(hass, entry)
    # Blank request → configured values (incl. the stored token).
    req = coord.resolve_proxmox_request()
    assert req["host"] == "pve.lan"
    assert req["token_id"] == "u@pam!t"
    assert req["token_secret"] == "secret"
    assert req["verify_tls"] is False
    # get_proxmox_config never leaks the token.
    cfg = coord.get_proxmox_config()
    assert cfg["token_configured"] is True
    assert "token_secret" not in cfg


async def test_resolve_proxmox_request_raises_without_token(hass) -> None:  # noqa: ANN001
    coord = HomelableCoordinator(hass, _mock_entry())
    with pytest.raises(ValueError, match="token"):
        coord.resolve_proxmox_request(host="pve.lan")


# ─── WebSocket ────────────────────────────────────────────────────────────────


@pytest.fixture
async def setup_ws(hass: HomeAssistant, hass_storage):  # noqa: ANN001
    coord = HomelableCoordinator(hass, _mock_entry())
    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord
    async_register_websocket_commands(hass)
    return coord


async def test_ws_get_config(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/proxmox/get_config"})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"]["token_configured"] is False
    assert "token_secret" not in msg["result"]


async def test_ws_test_connection_not_configured(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/proxmox/test_connection", "host": "pve.lan"}
    )
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "not_configured"


async def test_ws_import_returns_inventory(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    nodes = [_host("a"), _guest("a", 100, ip="10.0.0.100")]
    edges = [{"source": "pve-node-a", "target": "pve-a-100"}]
    client = await hass_ws_client(hass)
    with patch(
        "custom_components.homelable.proxmox.fetch_proxmox_inventory",
        AsyncMock(return_value=(nodes, edges)),
    ):
        await client.send_json(
            {
                "id": 1,
                "type": "homelable/proxmox/import",
                "host": "pve.lan",
                "token_id": "u@pam!t",
                "token_secret": "secret",
            }
        )
        msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"]["device_count"] == 2
    assert len(msg["result"]["nodes"]) == 2
