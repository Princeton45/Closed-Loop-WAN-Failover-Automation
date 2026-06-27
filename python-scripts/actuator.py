#!/usr/bin/env python3
"""actuator.py — write OSPF cost via RESTCONF PATCH. Idempotent + failure-aware."""

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "Accept": "application/yang-data+json",
    "Content-Type": "application/yang-data+json",
}

def set_primary_ospf_cost(router, username, password, interface, cost):
    """PATCH the OSPF cost on `interface`. Returns True on success, False on failure.
    Returning a bool (not raising) lets the controller decide what to do on failure."""
    url = (
        f"https://{router}/restconf/data/"
        f"Cisco-IOS-XE-native:native/interface/GigabitEthernet={interface}"
        f"/ip/Cisco-IOS-XE-ospf:ospf"
    )
    body = {"Cisco-IOS-XE-ospf:ospf": {"cost": cost}}

    try:
        resp = requests.patch(url, headers=HEADERS, json=body,
                            auth=(username, password), verify=False, timeout=10)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        # Log and report failure. We do NOT pretend it worked.
        print(f"[ACTUATOR] PATCH failed: {e}")
        return False

if __name__ == "__main__":
    # Manual test: raise then restore. Watch `show ip route` on R1 between calls.
    ok = set_primary_ospf_cost("10.99.99.11", "apiadmin", "Lab-RESTCONF-123",
                               interface="1", cost=1000)
    print("raise cost ->", ok)