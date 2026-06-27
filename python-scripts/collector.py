#!/usr/bin/env python3
"""collector.py — pull IP SLA stats and reduce them to the numbers DECIDE needs.
Loss is computed as a DELTA between polls, not a lifetime average, so a degraded
link shows high loss immediately instead of being diluted by hours of past success."""

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "Accept": "application/yang-data+json",
    "Content-Type": "application/yang-data+json",
}

# Module-level memory of the last poll's cumulative counters. We need the PREVIOUS
# totals to compute how many probes succeeded/failed SINCE the last time we looked.
_last_success = None
_last_failure = None

def collect_metrics(router, username, password, sla_id):
    """Return {'ok', 'loss_pct', 'rtt_ms', 'last_code'}.
    loss_pct is loss over the probes that ran since the previous call."""
    global _last_success, _last_failure

    url = (
        f"https://{router}/restconf/data/"
        f"Cisco-IOS-XE-ip-sla-oper:ip-sla-stats/sla-oper-entry={sla_id}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, auth=(username, password),
                            verify=False, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        return {"ok": False, "loss_pct": 100.0, "rtt_ms": None,
                "last_code": "unreachable", "error": str(e)}

    entry = data.get("Cisco-IOS-XE-ip-sla-oper:sla-oper-entry", {})

    # Cumulative lifetime counters straight from the router.
    success = entry.get("success-count", 0)
    failure = entry.get("failure-count", 0)

    # The instantaneous health of the MOST RECENT probe. 'ret-code-ok' = good.
    last_code = entry.get("latest-return-code", "unknown")

    # --- Delta math: how many probes ran since we last polled, and how many failed? ---
    if _last_success is None:
        # First call ever: no previous baseline, so we can't compute a delta yet.
        # Fall back to the latest return code for this one poll.
        loss_pct = 0.0 if last_code == "ret-code-ok" else 100.0
    else:
        d_success = success - _last_success
        d_failure = failure - _last_failure
        d_total = d_success + d_failure
        # If no new probes ran between polls (poll faster than SLA frequency),
        # don't divide by zero — reuse the latest return code as the signal.
        if d_total <= 0:
            loss_pct = 0.0 if last_code == "ret-code-ok" else 100.0
        else:
            loss_pct = d_failure / d_total * 100.0

    # Remember this poll's totals for next time.
    _last_success, _last_failure = success, failure

    # RTT: only present when the latest probe succeeded. The 'could-not-find' shape
    # in the JSON means there's no latest RTT, so report None.
    rtt = None
    latest = entry.get("rtt-info", {}).get("latest-rtt", {})
    if "rtt" in latest:            # a real value only appears on success
        rtt = latest.get("rtt")

    return {"ok": True, "loss_pct": loss_pct, "rtt_ms": rtt, "last_code": last_code}

if __name__ == "__main__":
    import time
    # Two polls a few seconds apart so the delta has something to measure.
    print(collect_metrics("10.99.99.11", "apiadmin", "Lab-RESTCONF-123", 1))
    time.sleep(6)
    print(collect_metrics("10.99.99.11", "apiadmin", "Lab-RESTCONF-123", 1))