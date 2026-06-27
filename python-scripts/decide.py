#!/usr/bin/env python3
"""decide.py — the state machine. Pure logic. No imports that touch the network.
Because it's pure, you can unit-test every path with fake metrics (see the bottom)."""

import time
from enum import Enum

class State(Enum):
    HEALTHY     = "HEALTHY"
    FAILED_OVER = "FAILED_OVER"


LOSS_THRESHOLD = 5.0    # percent loss that counts as "bad"
RTT_THRESHOLD  = 250.0  # ms RTT that counts as "bad" (None RTT is treated as bad)
FAIL_AFTER     = 3      # consecutive bad polls before we fail over
CLEAR_AFTER    = 6      # consecutive good polls before we even CONSIDER reverting
DWELL_SECONDS  = 60     # min time to stay failed-over before reverting (anti-flap)

class Decision:
    """Possible actions the controller should take after a DECIDE call."""
    NONE        = "NONE"          # do nothing, stay put
    FAIL_OVER   = "FAIL_OVER"     # raise primary cost, drain to backup
    REVERT      = "REVERT"        # restore primary cost

class LinkController:
    def __init__(self):
        self.state = State.HEALTHY
        self.bad_streak = 0
        self.good_streak = 0
        self.failover_time = None    # timestamp we entered FAILED_OVER

    def _is_bad(self, metrics):
        """Translate raw metrics into a single bad/good verdict."""
        if not metrics["ok"]:
            return True                      # can't read the router => treat as bad
        if metrics["loss_pct"] >= LOSS_THRESHOLD:
            return True
        rtt = metrics["rtt_ms"]
        if rtt is None or rtt >= RTT_THRESHOLD:
            return True
        return False

    def decide(self, metrics, now=None):
        """Feed one poll's metrics in; get back a Decision. Updates internal state.
        'now' is injectable so tests can fake the clock — another testability trick."""
        if now is None:
            now = time.time()

        bad = self._is_bad(metrics)

        # Maintain the streak counters. A good poll resets bad_streak and vice versa —
        # streaks are CONSECUTIVE by construction.
        if bad:
            self.bad_streak += 1
            self.good_streak = 0
        else:
            self.good_streak += 1
            self.bad_streak = 0

        # --- Transition logic ---
        if self.state == State.HEALTHY:
            if self.bad_streak >= FAIL_AFTER:
                self.state = State.FAILED_OVER
                self.failover_time = now
                self.good_streak = 0
                return Decision.FAIL_OVER
            return Decision.NONE

        # state == FAILED_OVER
        dwell_ok = (now - self.failover_time) >= DWELL_SECONDS
        if self.good_streak >= CLEAR_AFTER and dwell_ok:
            self.state = State.HEALTHY
            self.failover_time = None
            self.bad_streak = 0
            return Decision.REVERT
        return Decision.NONE


# --- Unit tests that can be ran WITH NO LAB.
if __name__ == "__main__":
    def good(): return {"ok": True, "loss_pct": 0.0,  "rtt_ms": 5}
    def bad():  return {"ok": True, "loss_pct": 50.0, "rtt_ms": 5}

    c = LinkController()
    t = 0
    # Two bad polls: not enough yet (FAIL_AFTER=3).
    assert c.decide(bad(), now=t) == Decision.NONE; t += 5
    assert c.decide(bad(), now=t) == Decision.NONE; t += 5
    # Third bad poll: fail over.
    assert c.decide(bad(), now=t) == Decision.FAIL_OVER; t += 5
    # Good polls start, but dwell (60s) not elapsed yet -> no revert.
    for _ in range(CLEAR_AFTER):
        assert c.decide(good(), now=t) == Decision.NONE; t += 5
    # Jump past the dwell timer, one more good poll -> revert.
    t += DWELL_SECONDS
    assert c.decide(good(), now=t) == Decision.REVERT
    print("All state-machine tests passed.")