"""Hardware & Software EOL / EOS Lifecycle intelligence catalog and analysis.

Tracks End-of-Sale (EOS), End-of-Software-Maintenance (EoSW),
End-of-Security-Support (EoSec), and Last-Date-of-Support (LDOS / EOL)
across Juniper Junos, Cisco Catalyst, Cisco Small Business, and other network hardware.
"""
import datetime
import re
from typing import Dict, List, Optional, Tuple

# Pre-populated enterprise lifecycle intelligence database
# Format:
# "model_regex": {
#     "vendor": "Juniper" | "Cisco",
#     "family": "EX2200" | "EX2300" | "EX3300" | "EX4600" | "C1300" | "Catalyst 3750",
#     "eos_date": "YYYY-MM-DD",     # End of Sale / End of Life announcement
#     "eosw_date": "YYYY-MM-DD",    # End of Software Maintenance
#     "eosec_date": "YYYY-MM-DD",   # End of Vulnerability & Security Fixes
#     "eol_date": "YYYY-MM-DD",     # Last Date of Support (LDOS / Full EOL)
#     "recommended_sw": "Version",  # TAC / Vendor Gold Standard Recommended Release
#     "notes": "Description",
# }
LIFECYCLE_CATALOG = [
    {
        "pattern": re.compile(r"^ex2200", re.I),
        "vendor": "Juniper Networks",
        "family": "Juniper EX2200 Series",
        "eos_date": "2019-06-30",
        "eosw_date": "2020-06-30",
        "eosec_date": "2022-06-30",
        "eol_date": "2024-06-30",
        "recommended_sw": "12.3R12-S21",
        "notes": "Legacy 1G access switch. Past Last Date of Support (EOL). Migration to EX2300/EX4100 recommended.",
    },
    {
        "pattern": re.compile(r"^ex3300", re.I),
        "vendor": "Juniper Networks",
        "family": "Juniper EX3300 Series",
        "eos_date": "2019-06-30",
        "eosw_date": "2020-06-30",
        "eosec_date": "2022-06-30",
        "eol_date": "2024-06-30",
        "recommended_sw": "12.3R12-S15",
        "notes": "Distribution/Stack switch with 10G uplinks. Past Last Date of Support (EOL). Replacement recommended.",
    },
    {
        "pattern": re.compile(r"^ex2300", re.I),
        "vendor": "Juniper Networks",
        "family": "Juniper EX2300 Series",
        "eos_date": "2025-07-31",
        "eosw_date": "2026-07-31",
        "eosec_date": "2028-07-31",
        "eol_date": "2030-07-31",
        "recommended_sw": "23.4R2-S3.9",
        "notes": "Current enterprise access switch. Active OEM software and hardware support.",
    },
    {
        "pattern": re.compile(r"^ex4600", re.I),
        "vendor": "Juniper Networks",
        "family": "Juniper EX4600 Series",
        "eos_date": "2024-12-31",
        "eosw_date": "2025-12-31",
        "eosec_date": "2027-12-31",
        "eol_date": "2029-12-31",
        "recommended_sw": "21.4R3-S2.4",
        "notes": "10G/40G Distribution & Campus Core switch. Active hardware & software maintenance.",
    },
    {
        "pattern": re.compile(r"^(c1300|cbs1300)", re.I),
        "vendor": "Cisco Systems",
        "family": "Cisco Catalyst 1300 Series",
        "eos_date": "2029-12-31",
        "eosw_date": "2030-12-31",
        "eosec_date": "2032-12-31",
        "eol_date": "2034-12-31",
        "recommended_sw": "4.1.3.36",
        "notes": "Current generation Catalyst smart switch. Full Cisco TAC hardware & software support.",
    },
    {
        "pattern": re.compile(r"^(cbs250|cbs350)", re.I),
        "vendor": "Cisco Systems",
        "family": "Cisco Business 250/350 Series",
        "eos_date": "2024-10-31",
        "eosw_date": "2025-10-31",
        "eosec_date": "2027-10-31",
        "eol_date": "2029-10-31",
        "recommended_sw": "3.3.0.16",
        "notes": "Cisco Business series. Replacement path: Catalyst 1200 / 1300 series.",
    },
    {
        "pattern": re.compile(r"^(ws-c3750|ws-c3560|c3750)", re.I),
        "vendor": "Cisco Systems",
        "family": "Cisco Catalyst 3750/3560 Series",
        "eos_date": "2013-01-30",
        "eosw_date": "2014-01-30",
        "eosec_date": "2016-01-30",
        "eol_date": "2018-01-31",
        "recommended_sw": "12.2(55)SE12",
        "notes": "Obsolete legacy switch. Past all Cisco support and security vulnerability patching.",
    },
    {
        "pattern": re.compile(r"^(ws-c2960|c2960)", re.I),
        "vendor": "Cisco Systems",
        "family": "Cisco Catalyst 2960 Series",
        "eos_date": "2014-10-31",
        "eosw_date": "2015-10-31",
        "eosec_date": "2017-10-31",
        "eol_date": "2019-10-31",
        "recommended_sw": "15.2(2)E9",
        "notes": "Legacy 2960 access series. Past Last Date of Support (EOL).",
    },
]


def match_lifecycle_entry(hw_model: str, model_fallback: str = "") -> Optional[dict]:
    """Find the best matching lifecycle catalog entry for a device model."""
    targets = [hw_model or "", model_fallback or ""]
    for t in targets:
        if not t:
            continue
        cleaned = t.strip()
        for entry in LIFECYCLE_CATALOG:
            if entry["pattern"].search(cleaned):
                return entry
    return None


def calculate_lifecycle_status(entry: Optional[dict], today: datetime.date = None) -> Tuple[str, str, int]:
    """
    Evaluate lifecycle status based on dates.
    Returns (status_key, label, days_remaining):
    - 'eol': Past Last Date of Support (Critical Red)
    - 'eosec': Past Security Vulnerability Support (Red)
    - 'eosw': Past Software Maintenance (Amber)
    - 'eos_soon': EOL approaching within 365 days (Amber)
    - 'supported': Fully Supported (Green)
    - 'unknown': No Catalog Match (Gray)
    """
    if not entry:
        return ("unknown", "Unknown Lifecycle", 99999)

    if today is None:
        today = datetime.date.today()

    def parse_d(val: str) -> Optional[datetime.date]:
        try:
            return datetime.date.fromisoformat(val) if val else None
        except Exception:
            return None

    d_eol = parse_d(entry.get("eol_date"))
    d_eosec = parse_d(entry.get("eosec_date"))
    d_eosw = parse_d(entry.get("eosw_date"))
    d_eos = parse_d(entry.get("eos_date"))

    # 1. Past Last Date of Support
    if d_eol and today >= d_eol:
        days_past = (today - d_eol).days
        return ("eol", f"End-of-Life ({days_past}d ago)", -days_past)

    # 2. Past Security Fixes
    if d_eosec and today >= d_eosec:
        days_past = (today - d_eosec).days
        return ("eosec", f"No Security Patches ({days_past}d ago)", -days_past)

    # 3. Past Software Maintenance
    if d_eosw and today >= d_eosw:
        days_past = (today - d_eosw).days
        return ("eosw", f"Maintenance Ended ({days_past}d ago)", -days_past)

    # 4. EOL approaching within 365 days
    if d_eol:
        days_left = (d_eol - today).days
        if days_left <= 365:
            return ("eos_soon", f"EOL in {days_left} days", days_left)

    # 5. Fully supported
    days_left = (d_eol - today).days if d_eol else 1000
    return ("supported", "Fully Supported", days_left)


def check_firmware_compliance(running_sw: str, recommended_sw: str) -> Tuple[str, str]:
    """
    Compares running software version with recommended version.
    Returns (compliance_status, badge_label):
    - 'match': 'Target Release'
    - 'diverged': 'Upgrade Recommended'
    - 'unknown': 'Baseline Pending'
    """
    if not running_sw:
        return ("unknown", "Not Detected")
    if not recommended_sw:
        return ("unknown", "No Baseline")

    clean_running = running_sw.strip().lower()
    clean_target = recommended_sw.strip().lower()

    if clean_running == clean_target:
        return ("match", "Optimal / Target")
    return ("diverged", f"Upgrade to {recommended_sw}")


def analyze_device_lifecycle(devices) -> dict:
    """Analyze a collection of Device ORM objects and produce rich dashboard KPIs and device rows."""
    today = datetime.date.today()
    items = []

    kpi = {
        "total": len(devices),
        "supported": 0,
        "eos_soon": 0,
        "eosec": 0,
        "eol": 0,
        "unknown": 0,
        "firmware_matches": 0,
        "firmware_diverged": 0,
    }

    vendor_breakdown = {}
    family_breakdown = {}
    sw_versions = {}

    for d in devices:
        hw = d.hw_model or d.model or ""
        sw = d.sw_version or ""
        entry = match_lifecycle_entry(hw, d.model)
        status_key, status_label, days_remaining = calculate_lifecycle_status(entry, today)

        # Firmware compliance
        rec_sw = entry.get("recommended_sw", "") if entry else ""
        fw_status, fw_label = check_firmware_compliance(sw, rec_sw)

        # Tally KPIs
        if status_key in ("eol", "eosec"):
            kpi["eol"] += 1
        elif status_key in ("eosw", "eos_soon"):
            kpi["eos_soon"] += 1
        elif status_key == "supported":
            kpi["supported"] += 1
        else:
            kpi["unknown"] += 1

        if fw_status == "match":
            kpi["firmware_matches"] += 1
        elif fw_status == "diverged":
            kpi["firmware_diverged"] += 1

        # Vendor and family tallies
        vendor_name = entry.get("vendor", "Other") if entry else ("Juniper" if d.is_juniper else "Cisco")
        family_name = entry.get("family", hw or "Unknown") if entry else (hw or "Generic")
        vendor_breakdown[vendor_name] = vendor_breakdown.get(vendor_name, 0) + 1
        family_breakdown[family_name] = family_breakdown.get(family_name, 0) + 1

        if sw:
            sw_key = f"{family_name} ({sw})"
            sw_versions[sw_key] = sw_versions.get(sw_key, 0) + 1

        items.append({
            "device": d,
            "hw_model": hw,
            "sw_version": sw,
            "serial": d.serial or "N/A",
            "entry": entry,
            "status_key": status_key,
            "status_label": status_label,
            "days_remaining": days_remaining,
            "vendor": vendor_name,
            "family": family_name,
            "recommended_sw": rec_sw,
            "fw_status": fw_status,
            "fw_label": fw_label,
            "eol_date": entry.get("eol_date", "N/A") if entry else "N/A",
            "eos_date": entry.get("eos_date", "N/A") if entry else "N/A",
            "eosec_date": entry.get("eosec_date", "N/A") if entry else "N/A",
            "notes": entry.get("notes", "") if entry else "",
        })

    # Sort: Critical EOL first, then upcoming EOL, then supported
    severity = {"eol": 0, "eosec": 1, "eosw": 2, "eos_soon": 3, "unknown": 4, "supported": 5}
    items.sort(key=lambda x: (severity.get(x["status_key"], 9), x["device"].name))

    return {
        "kpi": kpi,
        "items": items,
        "vendor_breakdown": vendor_breakdown,
        "family_breakdown": family_breakdown,
        "sw_versions": sw_versions,
        "distinct_sw_count": len(sw_versions),
    }
