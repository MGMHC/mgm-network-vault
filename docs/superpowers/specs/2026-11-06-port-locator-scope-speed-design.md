# Port Locator: trace from distribution, group/location scope, faster scans — design

**Date:** 2026-11-06 · **Size:** small change · **Decided with:** Enawba

## Why
- Admins usually know roughly where a device is (a floor/block or a rack) but not the exact switch; today the only choices are one switch or the whole site.
- Scans feel slow.

## What the code does today (findings)
1. **Full-table downloads.** `query_mac_table` asks each switch for the one MAC. An empty answer — the normal case on every switch that doesn't have it — is treated as a failure, and the whole MAC table is downloaded and discarded (`operations.py:472`). `query_arp` does the same with the full ARP table (`:395`).
2. **The switch scan waits for the IP lookup.** Step 1 (ARP) waits for every core switch before step 2 starts, even for a MAC search, where the IP is only extra information.
3. **The SonicWall firewall is scanned like a switch**, with Cisco commands that fail one after another.
4. **Parallelism** is the backup setting `max_workers` (8): 68 switches take about 9 rounds.
5. **Bug: narrowing the scope breaks IP searches.** IP→MAC resolution only uses core/distribution switches *inside the selected scope*. With a floor selected, none are left, access switches are asked, and the result is "IP not found".
6. **Bug: Cancel doesn't stop the scan.** It sets a flag the workers never check.

## Goals / non-goals / anti-goals
- **Goals:**
  - Group and Location filters.
  - Answer in seconds when the device is on an access port.
  - A full-path scan on request.
  - IP search works with any scope.
  - Cancel actually stops the scan.
- **Non-goals:**
  - Multi-select scopes.
  - Caching MAC tables between searches.
  - LLDP/CDP topology tracing.
- **Anti-goals:**
  - A wrong "not found" because a filtered command's empty output was misread.
  - Losing the uplink path when someone needs it.

## Approaches (chosen by Enawba)
1. **Two extra dropdowns, Group and Location — chosen.** The Location and Switches lists narrow to match the chosen group.
2. One combined "Scope" dropdown. Rejected: a 90-item list, and group and location can't be combined.
3. Multi-select chips. Rejected: busiest, more work.

**Early stop (chosen):** stop at the first access-port hit, with a "Show full path" button that runs the full scan.

## Design
- **UI** (`port_locator.html`):
  - **Group** and **Location** dropdowns ("Any" by default) between Site and Switches.
  - Options come from the enabled switches in the user's scope.
  - Choosing a site narrows Group, Location and Switches; choosing a group narrows Location and Switches; choosing a location narrows Switches. This is done client-side from data attributes on each switch option.
- **API** (`/api/operations/port-locator/stream` and the non-stream route):
  - Accepts `group`, `location`, `full` (bool).
  - The filters are combined with AND on top of the existing site and access filters, server-side.
- **Scan scope:**
  - The MAC scan covers the filtered switches with role ≠ `firewall` and a non-SonicWall platform.
  - ARP resolution for IP searches always uses the **site's** core/distribution switches, plus any in scope, regardless of group/location/switch filters, and still within the user's site access. If there are none, it falls back to the scanned switches.
- **Speed:**
  - **Filtered commands are trusted.** If the filtered command runs, its output (even empty) is the answer. The full table is only fetched if the filtered command itself errors (unsupported syntax).
  - **MAC searches** start the MAC scan and the ARP lookup at the same time.
  - **IP searches** need the MAC first. ARP on core switches finishes as soon as one core switch answers with a match; the rest are skipped.
  - **Parallelism:** `min(16, switches)` SSH sessions for the locator, independent of the backup `max_workers`.
- **Early stop (default):**
  - When a match is found on an **access** switch on a **non-uplink** port (`is_uplink_port` false), the run stops scheduling more switches.
  - In-flight queries finish, and their matches are still reported.
  - The result says "Stopped early after N of M switches".
- **Show full path:**
  - A button on an early-stopped result runs the same search with `full: true`.
  - That scans every switch in scope and lists all matches (uplink path included), as today.
- **Cancel:** workers check the run's status before each switch and stop scheduling when it is `cancelled`.

## Update (approved by Enawba): trace from the distribution switch first
Enawba's manual method is to look the MAC up on the distribution switch and follow it. The network suits this:
- LLDP is enabled in all 68 backed-up configs.
- `DIST-EX3300-VC` has 34 `ae` bundles, about one per access switch or stack.
- The cores carry the rest.

**But 53 of 68 switches' real hostnames differ from their Vault names** (e.g. `DIST-EX3300-VC` is `dsw-agg`, and `GF-RACK1-ACC-VC1` is `GF-RACK1-ACC-VC1-S1-S3-90.5`), and 10 have no hostname in their backup output.

**Trace (the default search path):**
1. **Ask the site's distribution and core switches**, in parallel, within the user's site access:
   - their MAC table for the MAC;
   - for an IP search, ARP first, on the same SSH session.

   Result: the switch and port where the MAC is learned. On several hits, prefer distribution, then core.
2. **Identify the neighbour on that port.**
   - Run `show lldp neighbors` on that switch and find the row whose local port, or whose parent `ae` for bundle members, matches the learned port.
   - Map the neighbour to a Vault switch:
     - by **real hostname** (new `Device.hostname`, case-insensitive, domain stripped);
     - else by **management IP** from `show lldp neighbors interface <port>` detail (Juniper) or `show lldp neighbors <port> detail` (Cisco) = `Device.host`.
3. **Query that switch's MAC table.**
   - On a non-uplink port → **found**.
   - On an uplink → repeat from step 2 on that switch.
   - At most 4 hops; a switch already visited ends the trace.
4. **Fall back to the parallel scan** (early stop, group/location scope) when the trace breaks:
   - the MAC is on no distribution/core switch;
   - no LLDP row for the port;
   - the neighbour isn't in the Vault, or is out of the user's site access;
   - an SSH error;
   - the hop limit is reached.

   The log says why the trace stopped.
- **The result shows the path**: each hop as switch + port, e.g. `dsw-agg ae12 → 10F-RACK1-ACC-SW3 ge-0/0/14`.
- **Group/Location** filters limit the fallback scan and the Switches list. The trace follows the cabling, and the result names the found switch's group and location.
- **"Show full path"** remains for early-stopped fallback scans, where it scans every switch in scope.
- **New `Device.hostname`:**
  - set from `show version` on every backup: Juniper `Hostname:`, Cisco `<name> uptime is`;
  - back-filled once at startup from each switch's latest info file.
- **Verification adds:**
  - trace tests with stubbed LLDP and MAC output, in Juniper and Cisco formats: single hop, `ae` member → parent, two hops (daisy chain), name mismatch resolved by IP, neighbour unknown → fallback, loop → fallback;
  - hostname parsing and back-fill;
  - before merge, with Enawba's permission, one **read-only live trace** (`show` commands only) against the real distribution switch to confirm the parsers on real output.

## Builder's calls
- Exact layout and wording; whether "Show full path" appears in the results panel or the progress modal (both is fine).

## Verification
- **Scripted checks**, with stubbed SSH (`engine.connect` returns a fake connection with scripted output per command):
  - an empty filtered result → no full-table command sent;
  - a filtered command error → full table fetched;
  - the firewall is never queried;
  - an IP search with a group scope still resolves via the site's core switch;
  - early stop after the access hit leaves later switches unqueried;
  - `full: true` queries all switches;
  - cancel stops further queries;
  - the group and location filters choose the right switches;
  - a site-admin can't scan another site through the group or location filters.
- **Browser check:** the dropdowns narrow each other, no JS errors, and "Show full path" appears after an early stop.
- **Timing:** with the stub at 1 s per SSH command, scripted runs show the before/after difference.
