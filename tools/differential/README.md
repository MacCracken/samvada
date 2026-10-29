# Backend differential — C shim vs native Cyrius

Runs the identical call sequence through both backends **in one
process, sequentially**, and compares every return value.

    SAMVADA_DIFF_TAKES_CONTROL=1 ./tools/differential/run.sh

**It takes logind session control.** Both halves call `TakeDevice`,
which takes control of the caller's session first, and on a seated VT
session that turns the keyboard off and blanks the console until
control is released. Run it from a seatless session (ssh, a container),
never from the graphical session you are typing in; without the opt-in
variable it refuses to start (1.1.2).

Sequential A/B is forced, not chosen: samvada's module-scope state
and the `-EBUSY` re-init guard make two live backends in one process
impossible by construction.

## What is a contract, and what is not

**Error codes are the contract.** The frozen public API promises
sd-bus errno pass-through, so a consumer branching on a specific
magnitude must get the same answer from either backend. `init`,
`take_device`, `release_device` and `release` are compared strictly;
a mismatch fails the run.

**`pump_signals`' event count is NOT.** It reports how many messages
*that backend* happened to have buffered at that moment. libsystemd
drains its own queue at pump time; the native backend consumes the
same `NameAcquired` earlier, as a non-matching message inside
`get_session_path`'s reply loop. Both handle it exactly once.
Pinning the count would pin an internal buffering schedule, so it is
printed and excluded.

This distinction was found by running the differential, not by
reasoning about it: the first version reported BREACHED on a
difference that turned out to be legitimate.

## Expected output off a seat

    init                      0          0   MATCH
    take_device             -13        -13   MATCH
    release_device          -22        -22   MATCH
    pump_signals              1          0   differs (not a contract)
    release                   0          0   MATCH

`-13` is `AccessDenied` — a seatless session cannot own a DRM
device. On a seated session both backends should succeed. A successful
take is recorded as `0`, not as the descriptor, and the descriptor is
closed: the two backends hand out different numbers for the same
success (1.1.2; until then the raw numbers were compared, so a take
that succeeded on both sides would have read as a breach). That seated
run is the CG-lane check neither backend has had.
