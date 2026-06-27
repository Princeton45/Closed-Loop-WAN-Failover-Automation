#!/usr/bin/env python3
"""probe.py — minimal RESTCONF reader for IP SLA stats.
Its only job: prove we can pull telemetry and see the JSON shape."""

import requests
import urllib3
import json

# The lab cert is self-signed, so requests would warn on every call. Silence that noise

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Connection facts. Will be moved to a config file later (not hardcoded). ---
ROUTER   = "10.99.99.11"
USERNAME = "apiadmin"
PASSWORD = "Lab-RESTCONF-123"

# Every RESTCONF GET request will need these 2 headers
HEADERS = {
    "Accept": "application/yang-data+json",
    "Content-Type": "application/yang-data+json",
}

def get_ip_sla_raw(router, sla_id):
    """GET the operational stats for one IP SLA entry and return the parsed JSON (a dict)."""
    # The URL says: from this router's RESTCONF datastore, give me the sla-oper-entry
    # whose key (oper-id) is sla_id. The '=' syntax is how RESTCONF selects a list key.
    url = (
        f"https://{router}/restconf/data/"
        f"Cisco-IOS-XE-ip-sla-oper:ip-sla-stats/sla-oper-entry={sla_id}"
    )

    # The actual network call. auth=(...) does HTTP Basic auth. verify=False = trust the
    # self-signed lab cert. timeout means we don't hang forever if the router is unreachable.
    response = requests.get(
        url,
        headers=HEADERS,
        auth=(USERNAME, PASSWORD),
        verify=False,
        timeout=10,
    )

    # raise_for_status() turns an HTTP error (401, 404, 500...) into a Python exception
    # so we notice instead of silently parsing an error page. 
    response.raise_for_status()

    # .json() parses the response body text into Python dicts/lists.
    return response.json()

# If script is ran directly from terminal, then execute the function. 
# If the probe.py script is imported into another file, don't run the function.
# json.dumps makes the output of the data easier to read.
if __name__ == "__main__":
    data = get_ip_sla_raw(ROUTER, 1)
    print(json.dumps(data, indent=2))
