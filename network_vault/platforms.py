"""What each device platform is: its vendor family and how Netmiko talks to it.

Feature modules (backup, optics, health, locator...) pick their commands by `family` or by the
platform key; nothing else should compare platform strings.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    family: str                 # juniper | cisco | sonicwall | other (unknown key: Cisco IOS-style commands)
    netmiko: str                # Netmiko device_type
    stack_cmd: str = ""         # Cisco: command that lists stack members


PROFILES = {
    "juniper_junos": Platform("juniper", "juniper_junos"),
    "cisco_c1300": Platform("cisco", "cisco_s300", "show stack"),
    "cisco_s300": Platform("cisco", "cisco_s300", "show switch"),
    "cisco_ios": Platform("cisco", "cisco_ios", "show switch"),
    "sonicwall_sonicos": Platform("sonicwall", "sonicwall_sonicos"),
}


def profile(platform):
    """Profile for a platform key. An unknown key is passed to Netmiko as its device_type."""
    return PROFILES.get(platform or "") or Platform("other", platform or "", "show switch")
