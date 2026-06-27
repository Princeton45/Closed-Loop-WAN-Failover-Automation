#!/usr/bin/env python3
"""controller.py — the closed-loop daemon. Ties collector + decide + actuator together."""

import time
import signal
import logging

from collector import collect_metrics
from actuator import set_primary_ospf_cost
from decide import LinkController, Decision


ROUTER       = "10.99.99.11"
USERNAME     = "apiadmin"
PASSWORD     = "Lab-RESTCONF-123"
SLA_ID       = 1
PRIMARY_IF   = "1"
NORMAL_COST  = 10
FAILOVER_COST= 1000
POLL_SECONDS = 5
ACT_RETRIES  = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],   # stdout only — journald captures it
)
log = logging.getLogger("wan-controller")

# Graceful shutdown flag flipped by signal handler.
_running = True
def _stop(signum, frame):
    global _running
    log.info("Stop signal received; shutting down loop.")
    _running = False
signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)

def actuate_with_retry(cost, what):
    """Try the config change a few times before giving up. Returns True/False."""
    for attempt in range(1, ACT_RETRIES + 1):
        if set_primary_ospf_cost(ROUTER, USERNAME, PASSWORD, PRIMARY_IF, cost):
            log.info("%s succeeded (cost=%s) on attempt %d", what, cost, attempt)
            return True
        log.warning("%s attempt %d failed; retrying", what, attempt)
        time.sleep(2)
    log.error("%s FAILED after %d attempts — network NOT changed", what, ACT_RETRIES)
    return False

def main():
    ctrl = LinkController()
    log.info("Closed-loop WAN controller started. Watching SLA %s on %s.", SLA_ID, ROUTER)

    while _running:
        metrics = collect_metrics(ROUTER, USERNAME, PASSWORD, SLA_ID)
        decision = ctrl.decide(metrics)

        log.info("state=%s loss=%.1f%% rtt=%s decision=%s",
                ctrl.state.value, metrics["loss_pct"], metrics["rtt_ms"], decision)

        if decision == Decision.FAIL_OVER:
            log.warning("Primary degraded — draining to backup.")
            actuate_with_retry(FAILOVER_COST, "FAILOVER")
        elif decision == Decision.REVERT:
            log.info("Primary stable — reverting to primary.")
            actuate_with_retry(NORMAL_COST, "REVERT")

        time.sleep(POLL_SECONDS)

    log.info("Controller stopped.")

if __name__ == "__main__":
    main()