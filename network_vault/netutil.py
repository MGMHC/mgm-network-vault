"""MAC and IP address helpers, switch port name normalisation, and the OUI vendor catalog."""
import re


OUI_CATALOG = {
    "00:04:96": "Extreme Networks",
    "00:08:e3": "Cisco Systems",
    "00:10:db": "Juniper Networks",
    "00:1b:0d": "Cisco Systems",
    "00:1f:12": "Cisco Systems",
    "00:21:59": "Juniper Networks",
    "00:26:88": "Juniper Networks",
    "00:30:b6": "Cisco Systems",
    "00:40:96": "Cisco Systems",
    "00:60:2f": "Cisco Systems",
    "00:e0:14": "Cisco Systems",
    "00:e0:52": "Juniper Networks",
    "08:5b:0e": "Juniper Networks",
    "24:8a:07": "Juniper Networks",
    "2c:6b:f5": "Juniper Networks",
    "30:7b:ac": "Cisco Systems",
    "40:b4:f0": "Juniper Networks",
    "44:f4:77": "Juniper Networks",
    "50:c5:8d": "Juniper Networks",
    "54:4b:8c": "Juniper Networks",
    "5c:5e:ab": "Juniper Networks",
    "60:9c:9f": "Juniper Networks",
    "70:70:8b": "Juniper Networks",
    "7c:25:86": "Juniper Networks",
    "80:71:1f": "Juniper Networks",
    "84:18:88": "Juniper Networks",
    "84:c1:c1": "Juniper Networks",
    "88:e0:f3": "Juniper Networks",
    "9c:cc:83": "Juniper Networks",
    "a8:d0:e5": "Juniper Networks",
    "ac:4b:c8": "Juniper Networks",
    "b0:a8:6e": "Juniper Networks",
    "b8:c2:53": "Juniper Networks",
    "cc:e1:7f": "Juniper Networks",
    "dc:38:e1": "Juniper Networks",
    "ec:13:db": "Juniper Networks",
    "f0:1c:2d": "Juniper Networks",
    "f4:b5:2f": "Juniper Networks",
    "00:0b:86": "Aruba Networks / HPE",
    "00:1a:1e": "Aruba Networks / HPE",
    "00:24:6c": "Aruba Networks / HPE",
    "20:4c:03": "Aruba Networks / HPE",
    "94:b4:0f": "Aruba Networks / HPE",
    "d8:c7:c8": "Aruba Networks / HPE",
    "00:09:0f": "Fortinet",
    "00:1f:f8": "Palo Alto Networks",
    "08:66:98": "Palo Alto Networks",
    "70:30:18": "Fortinet",

    "00:05:69": "VMware",
    "00:0c:29": "VMware",
    "00:1c:14": "VMware",
    "00:50:56": "VMware",
    "00:15:5d": "Microsoft Hyper-V",
    "52:54:00": "QEMU / KVM",
    "00:1e:67": "Intel",
    "00:15:17": "Intel",
    "00:21:70": "Dell EMC",
    "00:24:e8": "Dell EMC",
    "18:03:73": "Dell EMC",
    "78:2b:cb": "Dell EMC",
    "00:02:b3": "Intel",
    "00:1b:21": "Intel",
    "00:26:b9": "Dell EMC",
    "ec:f4:bb": "Dell EMC",
    "00:11:85": "Hewlett Packard Enterprise",
    "3c:d9:2b": "Hewlett Packard Enterprise",

    "00:01:4a": "Philips Medical Systems",
    "00:09:fb": "Philips Healthcare",
    "00:17:61": "Philips Medical Systems",
    "00:24:e4": "Philips Healthcare",
    "28:51:32": "Philips Medical Systems",
    "00:07:f6": "GE Healthcare",
    "00:13:02": "GE Healthcare",
    "00:21:05": "GE Medical Systems",
    "00:50:c2": "GE Healthcare",
    "30:34:2d": "GE Healthcare",
    "00:0c:15": "Siemens Healthineers",
    "00:1a:64": "Siemens Medical Solutions",
    "00:26:4d": "Siemens Healthcare",
    "00:a0:8e": "Mindray Medical",
    "70:b3:d5": "Mindray Bio-Medical",
    "00:08:7c": "Draeger Medical",
    "00:18:23": "Draeger Medical",
    "00:0d:f0": "Baxter Healthcare",
    "00:23:a7": "Hill-Rom Medical",
    "00:1d:92": "Masimo Patient Monitoring",
    "00:16:44": "Nihon Kohden",

    "00:17:88": "Philips Lighting / IoT",
    "00:03:93": "Apple",
    "00:05:02": "Apple",
    "00:0a:95": "Apple",
    "00:10:fa": "Apple",
    "00:1e:c2": "Apple",
    "00:25:00": "Apple",
    "04:0c:ce": "Apple",
    "14:10:9f": "Apple",
    "3c:06:30": "Apple",
    "a4:83:e7": "Apple",
    "ac:bc:32": "Apple",
    "00:1b:63": "Apple",
    "00:21:6a": "Intel Mobile",
    "00:24:d7": "Intel Wireless",
    "60:57:18": "Intel Wireless",
    "80:86:f2": "Intel Wireless",
    "00:04:f2": "Polycom IP Phones",
    "00:15:65": "Yealink IP Phones",
    "80:5e:c0": "Yealink IP Phones",
    "00:04:0d": "Avaya IP Phones",
    "00:40:8c": "Axis Network Cameras",
    "ac:cc:8e": "Axis Network Cameras",
    "44:19:b6": "Hikvision Digital Cameras",
    "bc:ad:28": "Hikvision Digital Cameras",
    "bc:54:51": "Dahua Security Cameras",
    "00:07:4d": "Zebra Technologies (Printers/Scanners)",
    "00:15:70": "Zebra Technologies",
}


def clean_mac(s):
    """Normalize MAC address string to clean lowercase 12 hex chars."""
    if not s:
        return ""
    clean = re.sub(r"[^0-9a-fA-F]", "", str(s)).lower()
    return clean if len(clean) == 12 else ""


def format_mac(clean, fmt="colon"):
    """Format 12-char hex MAC into colon (00:11:22:33:44:55) or cisco (0011.2233.4455)."""
    if not clean or len(clean) != 12:
        return clean or ""
    if fmt == "cisco":
        return f"{clean[0:4]}.{clean[4:8]}.{clean[8:12]}"
    if fmt == "dash":
        return "-".join(clean[i:i+2] for i in range(0, 12, 2))
    return ":".join(clean[i:i+2] for i in range(0, 12, 2))


def is_ip(s):
    """Check if string is a valid IPv4 address."""
    s = (s or "").strip()
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def lookup_oui(clean_mac_str):
    """Return recognized hardware vendor / manufacturer name."""
    if not clean_mac_str or len(clean_mac_str) < 6:
        return "Unknown"
    prefix = ":".join(clean_mac_str[i:i+2] for i in range(0, 6, 2))
    return OUI_CATALOG.get(prefix, "Unregistered / Generic Device")


def base_port(p):
    """ge-0/0/14.0 -> ge-0/0/14 ; Gi1/0/14 stays (lower-cased for comparison)."""
    return (p or "").strip().split(".")[0].lower()


def norm_port(s):
    """Shorten verbose or lowercase interface names to standard compact form, e.g. GigabitEthernet1 or gi1 -> Gi1."""
    s = (s or "").strip()
    abbr = [
        (r'^(?:GigabitEthernet|gi)\s*', 'Gi'),
        (r'^(?:FastEthernet|fa)\s*', 'Fa'),
        (r'^(?:TenGigabitEthernet|tengige|te)\s*', 'Te'),
        (r'^(?:TwentyFiveGigE|twe)\s*', 'Twe'),
        (r'^(?:HundredGigE|hu)\s*', 'Hu'),
        (r'^(?:FortyGigabitEthernet|fo)\s*', 'Fo'),
        (r'^(?:Ethernet|eth)\s*', 'Eth'),
        (r'^(?:Port-channel|po)\s*', 'Po'),
    ]
    for pattern, short in abbr:
        s = re.sub(pattern, short, s, flags=re.IGNORECASE)
    return s
