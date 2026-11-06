# Port Locator Trace & Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port Locator traces a MAC from the distribution/core switches via LLDP to the access port, falls back to a scoped parallel scan with early stop, and gains Group and Location filters. Filtered command results are trusted (no full-table downloads), the firewall is skipped, and Cancel works.

**Architecture:**
- `operations.py`:
  - query helpers fixed;
  - new `query_lldp_neighbor`;
  - `stream_locate_host` restructured into `_resolve` (ARP on the site's core/dist), `_trace` (hop-by-hop) and `_scan` (parallel, early stop).
- `Device.hostname` (from `show version`) maps LLDP neighbours to Vault switches.
- `port_locator.html` gains two dropdowns, a path display and "Show full path".

**Tech Stack:** Flask, netmiko via `engine.connect`/`engine.run`, plain JS.

**Spec:** `docs/superpowers/specs/2026-11-06-port-locator-scope-speed-design.md`

## Global Constraints

- Only read-only `show` commands are ever sent.
- Every switch queried must be in the user's site access, for the trace as well as the scan.
- Scan parallelism is `min(16, n)`. At most 4 trace hops; revisiting a switch ends the trace.
- Firewalls (role `firewall` or platform `sonicwall*`) are never queried.
- A filtered command that runs is authoritative, even when empty. The full table is fetched only if the filtered command raises.
- No Claude attribution in commits; CHANGELOG entry under `## [Unreleased]`; no version bump.

## Review Focus

1. **An LLDP neighbour that maps to a switch in a site the user can't access** must not be queried; fall back instead → Task 2 test `trace_respects_site_access`.
2. **A MAC learned on an `ae` bundle** must find the neighbour via the bundle's member ports → Task 1 test `lldp_ae_parent`.
3. **A cancelled run** must not start new switch queries → Task 2 test `cancel_stops_scan`.
4. **An IP search with a narrow group scope** must still resolve via the site's core → Task 2 test `ip_with_group_scope_resolves`.
5. **Output from a switch that doesn't support a filtered command** must still work via the full-table fallback → Task 1 test `filter_error_falls_back`.

Checks are scratchpad scripts. SSH is stubbed: `engine.connect` returns a fake connection, and `engine.run(conn, cmd)` returns scripted output per (switch, command), recording every command sent.

---

### Task 1: Query helpers, LLDP and hostnames

**Files:**
- Modify `network_vault/operations.py`:
  - **`query_arp`, `query_mac_table`:** fetch the full table only when the filtered command *raises*. Each takes an optional `conn=None` so the caller can reuse one SSH session.
  - **New `query_lldp_neighbor(device, port, conn=None) -> dict | None`** returns `{"local_port", "parent", "system_name", "mgmt_ip"}` for the neighbour on `port`, matching either the local port or its parent bundle.
    - **Juniper:** `show lldp neighbors | no-more`. The columns are Local Interface, Parent Interface, Chassis Id, Port info and System Name; the system name is the last field and may contain spaces. If the name maps to no switch, run `show lldp neighbors interface <member> | no-more` and parse `Management address` (Juniper also prints `Address`).
    - **Cisco IOS / C1300:** `show lldp neighbors <port> detail`, parsing `System Name:` and the IP under `Management Addresses:`. If that raises, use `show cdp neighbors <port> detail` (`Device ID:` / `IP address:`).
  - **New `match_neighbor(nb, candidates) -> Device | None`:** match by hostname (case-insensitive, domain stripped, also against `Device.name`), then by `mgmt_ip == Device.host`.
- Modify `network_vault/models.py`: add `Device.hostname = db.Column(db.String(128))`.
- Modify `network_vault/engine.py`:
  - parse the hostname from the `show version` output in `collect`: Juniper `^Hostname:\s*(\S+)`, Cisco `^(\S+)\s+uptime is`;
  - return it in facts so `apply_facts` stores it.
- Modify `network_vault/__init__.py`: add `run_once("backfill_hostnames", …)`, which fills `Device.hostname` from each switch's latest `info_path` file using the same regexes. Unreadable files are skipped.
- Test: `scratchpad/locator_query_test.py`

- [ ] **Step 1: Write the check.**
  - **Empty filtered MAC output:** no `show ethernet-switching table | no-more` (full) command is sent.
  - **`filter_error_falls_back`:** when the filtered command raises, the full table is fetched and the parse finds the MAC.
  - **Cisco:** the same two cases.
  - **`lldp_ae_parent`:** Juniper output with `ge-0/0/46  ae12  …  10F-RACK1-ACC-SW3-90.26`, and port `ae12`, returns system name `10F-RACK1-ACC-SW3-90.26`.
  - **Direct port:** `ge-0/0/20` matches its own row.
  - **Juniper names with spaces** are parsed whole.
  - **Cisco detail parse:** name and management IP.
  - **CDP fallback** when LLDP raises.
  - **`match_neighbor`:** by hostname, by Vault name, by IP; unknown → None.
  - **Hostname regexes:** Juniper and Cisco samples.
  - **Back-fill:** after `create_app()` on a fresh DB copy, `DIST-EX3300-VC.hostname == "dsw-agg"`.
- [ ] **Step 2: Run it.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run it.** Expect ALL OK.
- [ ] **Step 5: Commit** `feat(locator): trust filtered results, LLDP neighbour lookup, switch hostnames`.

### Task 2: Trace, scoped scan, early stop, cancel

**Files:**
- Modify `network_vault/operations.py`: `stream_locate_host(app, devices, query_str, run_id=None, full=False, user_site_ids=None)`. `user_site_ids` is `None` for all sites. The run:
  1. **Resolve.** Collect the *site's* core/distribution switches, plus any core/distribution in scope, limited to `user_site_ids`, never firewalls.
     - **IP search:** run ARP on them in parallel and stop at the first match. The MAC table on the same session is collected for the trace.
     - **MAC search:** MAC table only.
  2. **Trace** from the best core/dist hit (distribution preferred):
     - loop `query_lldp_neighbor` → `match_neighbor` (candidates are all enabled switches the user can access) → `query_mac_table` on the neighbour;
     - stop when the hit is a non-uplink (`is_uplink_port` false) → found; or after 4 hops, a revisit, or any break → fallback.
     - Each hop is logged and appended to `path: [{switch_id, switch_name, port}]`.
  3. **Fallback scan:**
     - over the scoped, non-firewall switches not already queried, `min(16, n)` workers;
     - unless `full`, stop scheduling once an access, non-uplink hit is found;
     - check the run's `cancelled` status before each switch.
- **Result adds:** `path`, `method` ("trace" | "scan"), `stopped_early` (bool), `scanned` and `in_scope`.
- Modify `network_vault/views.py`: both port-locator API routes accept `group`, `location` and `full`, filter `Device.group` / `Device.location` exactly, and pass `user_site_ids` (`None` for group-admins).
- `locate_host` (non-stream): replace its body with a call that drains `stream_locate_host` and returns the final result, so both routes share one engine.
- Test: `scratchpad/locator_trace_test.py`

- [ ] **Step 1: Write the check** (stubbed SSH, recording commands per switch):
  - **MAC trace, single hop:** dist has the MAC on `ae12` → LLDP → access switch has it on `ge-0/0/14` (access mode) → found. Exactly 2 switches are queried, `method=trace` and the path has 2 hops.
  - **Two hops (daisy chain):** the access switch has it on an uplink → LLDP → next switch → found.
  - **Neighbour unknown** → fallback scan finds it; `method=scan`.
  - **`trace_respects_site_access`:** the neighbour is in another site and the user is a site-admin → it isn't queried, and the run falls back.
  - **Loop** (neighbour = previous switch) → fallback.
  - **`ip_with_group_scope_resolves`:** scope = Level-10, the IP is resolved via the core in the same site, and the trace finds it.
  - **Early stop:** fallback with 20 switches where the 3rd has the access hit → fewer than 20 queried and `stopped_early=True`; with `full=True` all 20 are queried.
  - **`cancel_stops_scan`:** cancel after the first result → fewer than all queried.
  - **Firewall never queried.**
  - **Group and location filters** select the right switches through the API route as a site-admin, and a site-admin with a group from another site gets an empty scope.
- [ ] **Step 2: Run it.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run it and the Task 1 check.** Expect ALL OK.
- [ ] **Step 5: Commit** `feat(locator): trace from distribution via LLDP, scoped scan with early stop and working cancel`.

### Task 3: Page

**Files:**
- Modify `network_vault/views.py` `port_locator()`: pass the group and location lists (distinct, non-empty, from switches in the user's scope, sorted).
- Modify `network_vault/templates/port_locator.html`:
  - **Group and Location `<select>`s** ("Any") between Site and Target Switches.
  - **Each switch `<option>`** gets `data-site`, `data-group` and `data-location`. A `narrowLocator()` function hides options that don't match the site, group and location; Location options are narrowed by group.
  - **`handleLocate`** sends `group`, `location` and `full`.
  - **Results:** show the path as `switch port → switch port`, the method ("Traced via LLDP" / "Scanned N of M switches"), and a **Show full path** button when `stopped_early`, which re-runs with `full: true`.
- Test: `scratchpad/locator_page_browser.mjs` (CDP):
  - the page loads with no JS errors;
  - choosing Level-10 leaves only Level-10 switches and the matching locations;
  - the request body contains group and location (intercept via `fetch` wrapper).
- CHANGELOG `## [Unreleased]` entries.

- [ ] **Step 1: Write the browser check.**
- [ ] **Step 2: Run it.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run it and all earlier checks.** Expect ALL OK.
- [ ] **Step 5: Commit** `feat(locator): Group and Location filters, traced path and Show full path`, with the CHANGELOG, spec and plan.
