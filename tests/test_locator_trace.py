"""Port Locator trace/scan engine (stream_locate_host) with scripted switches and the HTTP stream API."""
import json
import threading
import time

import pytest

from conftest import LLDP_HEADER as HDR, lldp_row
from network_vault import locator

MAC = "84a93e76eab4"
COLON = "84:a9:3e:76:ea:b4"
MATCH = f"show ethernet-switching table | match {COLON} | no-more"
LLDP = "show lldp neighbors | no-more"


@pytest.fixture()
def topo(seed):
    d = seed["dev"]
    hc_ids = [v[0] for k, v in d.items() if k not in ("mal", "fw")]
    return {
        "dist": d["dist"][1], "core": d["core"][1], "a": d["a"][1], "b": d["b"][1], "mal": d["mal"][1],
        "fw_id": d["fw"][0], "hc_ids": hc_ids,
        "l10_ids": [d["a"][0], d["b"][0]],
        "names": {v[0]: v[1] for v in d.values()},
        "hc_id": seed["hc_id"],
    }


@pytest.fixture()
def locate(app, switches, monkeypatch):
    """run(ids, query, ...) -> (result, events). Keeps worker count small and restores it."""
    monkeypatch.setattr(locator, "LOCATOR_WORKERS", 4)

    def run(ids, query=MAC, full=False, user_site_ids=None, cancel_after=None):
        rid = f"t{time.time()}"
        if cancel_after:
            threading.Timer(cancel_after, lambda: locator.cancel_locator_run(rid)).start()
        evs = [json.loads(line) for line in locator.stream_locate_host(app, ids, query, run_id=rid, full=full,
                                                                  user_site_ids=user_site_ids)]
        res = next((e["result"] for e in evs if e["type"] == "complete"), None)
        return res, evs
    return run


@pytest.fixture()
def base(switches, topo):
    """Distribution learns the MAC on ae12 -> LLDP says access switch A -> A has it on ge-0/0/14."""
    def build():
        switches.reset()
        switches.script[(topo["dist"], MATCH)] = f"   v120   {COLON}   D   -   ae12.0   0   0\n"
        switches.script[(topo["dist"], LLDP)] = HDR + lldp_row("ge-0/0/46", "ae12", topo["a"]) + lldp_row("ge-1/0/46", "ae12", topo["a"])
        switches.script[(topo["a"], MATCH)] = f"   v120   {COLON}   D   -   ge-0/0/14.0   0   0\n"
    build()
    return build


def test_single_hop_trace_finds_access_port(locate, topo, switches, base):
    res, _ = locate(topo["hc_ids"])
    assert res["best_match"]["switch_name"] == topo["a"]
    assert res["best_match"]["interface"] == "ge-0/0/14"
    assert res["method"] == "trace" and res["stopped_early"] is False
    assert [h["switch_name"] for h in res["path"]] == [topo["dist"], topo["a"]]
    assert set(switches.queried()) <= {topo["dist"], topo["core"], topo["a"]}
    assert not any(c.endswith("table | no-more") for c in switches.cmds())


def test_two_hop_daisy_chain(locate, topo, switches, base):
    switches.script[(topo["a"], MATCH)] = f"   v120   {COLON}   D   -   ae3.0   0   0\n"
    switches.script[(topo["a"], LLDP)] = HDR + lldp_row("ge-0/0/47", "ae3", topo["b"])
    switches.script[(topo["b"], MATCH)] = f"   v120   {COLON}   D   -   ge-0/0/5.0   0   0\n"
    res, _ = locate(topo["hc_ids"])
    assert res["best_match"]["switch_name"] == topo["b"]
    assert [h["switch_name"] for h in res["path"]] == [topo["dist"], topo["a"], topo["b"]]


def test_unknown_neighbour_falls_back_to_scan_with_early_stop(locate, topo, switches, base):
    switches.delay = 0.05
    switches.script[(topo["dist"], LLDP)] = HDR + lldp_row("ge-0/0/46", "ae12", "mystery-box")
    res, _ = locate(topo["hc_ids"])
    assert res["method"] == "scan" and res["best_match"]["switch_name"] == topo["a"]
    assert res["stopped_early"] is True
    assert len(switches.queried()) < len(topo["hc_ids"])


def test_single_cable_uplink_is_followed_not_reported(locate, topo, switches, base):
    switches.script[(topo["a"], MATCH)] = f"   v120   {COLON}   D   -   ge-0/0/1.0   0   0\n"
    switches.script[(topo["a"], LLDP)] = HDR + lldp_row("ge-0/0/1.0", "-", topo["b"])
    switches.script[(topo["b"], MATCH)] = f"   v120   {COLON}   D   -   ge-0/0/5.0   0   0\n"
    res, _ = locate(topo["hc_ids"])
    assert res["method"] == "trace"
    assert (res["best_match"]["switch_name"], res["best_match"]["interface"]) == (topo["b"], "ge-0/0/5")


def test_trace_through_overflowing_lldp_row(locate, topo, switches, base):
    switches.script[(topo["dist"], LLDP)] = HDR + (
        f"ge-0/0/16.0        ae12.0              b0:33:a6:7c:40:00   UPLINK_TO_DISTRIBUTION {topo['a']}-S1-S2\n")
    res, _ = locate(topo["hc_ids"])
    assert res["method"] == "trace" and res["best_match"]["switch_name"] == topo["a"]


def test_full_scan_queries_every_switch_in_scope(locate, topo, switches, base):
    res, _ = locate(topo["hc_ids"], full=True)
    assert set(switches.queried()) >= {topo["names"][i] for i in topo["hc_ids"]}
    assert res["best_match"]["switch_name"] == topo["a"]


def test_trace_respects_site_access(locate, topo, switches, base):
    switches.script[(topo["dist"], LLDP)] = HDR + lldp_row("ge-0/0/46", "ae12", topo["mal"])
    switches.script[(topo["mal"], MATCH)] = f"   v120   {COLON}   D   -   ge-0/0/9.0   0   0\n"
    res, _ = locate(topo["hc_ids"], user_site_ids=[topo["hc_id"]])
    assert topo["mal"] not in switches.queried()
    assert res["method"] == "scan"


def test_loop_in_trace_falls_back_to_scan(locate, topo, switches, base):
    switches.script[(topo["a"], MATCH)] = f"   v120   {COLON}   D   -   ae3.0   0   0\n"
    switches.script[(topo["a"], LLDP)] = HDR + lldp_row("ge-0/0/47", "ae3", topo["dist"])
    res, _ = locate(topo["hc_ids"])
    assert res["method"] == "scan"


def test_ip_search_resolves_through_arp_on_distribution(locate, topo, switches, base):
    switches.script[(topo["dist"], "show arp no-resolve | match 10.9.9.9 | no-more")] = \
        f"{COLON} 10.9.9.9  10.9.9.9  irb.120  none\n"
    res, _ = locate(topo["l10_ids"], query="10.9.9.9")
    assert res["resolved_mac"] == COLON
    assert res["best_match"]["switch_name"] == topo["a"]
    assert topo["dist"] in switches.queried()


def test_ip_not_in_arp_reports_not_found(locate, topo, switches, base):
    res, _ = locate(topo["hc_ids"], query="10.9.9.8")
    assert res["best_match"] is None and "not found" in res["message"]


def test_cancel_stops_the_scan(locate, topo, switches, base):
    switches.script[(topo["dist"], LLDP)] = ""
    switches.script[(topo["a"], MATCH)] = ""
    switches.delay = 0.25
    locate(topo["hc_ids"], cancel_after=0.6)
    assert len(switches.queried()) < len(topo["hc_ids"])


def test_firewall_is_never_queried(locate, topo, switches, base):
    locate(topo["hc_ids"] + [topo["fw_id"]], full=True)
    assert "T-FW" not in switches.queried()


@pytest.mark.parametrize("query", ["not-a-mac", "", "84:a9:3e"])
def test_invalid_query_is_an_error_event(locate, topo, switches, query):
    res, evs = locate(topo["hc_ids"], query=query)
    assert res is None and any(e["type"] == "error" for e in evs)
    assert switches.sent == []


def test_progress_follows_the_trace(locate, topo, switches, base):
    res, evs = locate(topo["hc_ids"])
    prog = [e for e in evs if e["type"] == "progress"]
    steps = [e["step"] for e in prog]
    pcts = [e["percent"] for e in prog]
    assert any("distribution/core" in t for t in steps)
    assert any("hop 1" in t and topo["a"] in t for t in steps)
    assert pcts == sorted(pcts) and pcts[-1] > 0 and max(pcts) < 100
    assert prog[-1]["done"] >= 3 and prog[-1]["total"] == len(topo["hc_ids"])
    state = locator.get_locator_run(evs[0]["run_id"])
    assert state["status"] == "complete" and state["step"]


def test_scan_progress_never_goes_backwards(locate, topo, switches, base):
    switches.delay = 0.02
    switches.script[(topo["dist"], LLDP)] = HDR + lldp_row("ge-0/0/46", "ae12", "mystery-box")
    _, evs = locate(topo["hc_ids"], full=True)
    prog = [e for e in evs if e["type"] == "progress"]
    pcts = [e["percent"] for e in prog]
    assert pcts == sorted(pcts)
    assert any("Scanning switch" in e["step"] for e in prog)


# ------------------------------------------------------------------ HTTP API

def _stream(client, body):
    r = client.post("/api/operations/port-locator/stream", json=body, headers=client.csrf_headers)
    return r, [json.loads(line) for line in r.get_data(as_text=True).splitlines() if line.strip()]


def test_api_requires_a_query(admin):
    r = admin.post("/api/operations/port-locator/stream", json={"query": " "}, headers=admin.csrf_headers)
    assert r.status_code == 400


def test_api_stream_group_filter_scope(admin, topo, switches, base):
    r, evs = _stream(admin, {"query": MAC, "site_id": "all", "group": "Level-10"})
    init = next(e for e in evs if e["type"] == "init")
    assert r.status_code == 200 and init["total"] == 2


def test_api_site_admin_cannot_scope_to_another_sites_group(site_admin, topo, switches):
    _, evs = _stream(site_admin, {"query": MAC, "site_id": "all", "group": "Level-NOPE"})
    assert next(e for e in evs if e["type"] == "init")["total"] == 0
    assert any(e["type"] == "error" for e in evs)


def test_api_stream_end_to_end(admin, topo, switches, base):
    _, evs = _stream(admin, {"query": MAC, "site_id": "all"})
    res = next(e["result"] for e in evs if e["type"] == "complete")
    assert res["best_match"]["switch_name"] == topo["a"]
