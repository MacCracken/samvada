# samvada — Benchmarks

> Perf history for samvada's CPU bench harness
> (`tests/samvada.bcyr`). One row per release.
>
> This is a **seed** doc — the v1.0 gate criterion in
> [`docs/development/roadmap.md`](development/roadmap.md) calls
> for *handshake latency* and *signal pump throughput*; both
> require a live-bus bench harness that does not yet exist.
> When it lands, those rows append below the current four.

## What's measured today

Four CPU-only paths exercised via `cyrius bench`:

| Bench | What it pins | Why it matters |
|---|---|---|
| `ffi_alloc` | `samvada_ffi_alloc` — `alloc(72)` + 9 zero-fill `store64`s | Hot at consumer init; once-per-process but on the cold path |
| `ffi_get_slot` | `samvada_ffi_get_slot(table, off)` — null-guard + `load64` | Hot path — every public API call dispatches through it before `fncallN` |
| `init_reject_null` | `samvada_init(0)` — `-EINVAL` early return | Misconfigured-FFI guard; pinned so a regression in early-exit ordering (e.g. doing alloc before validating table) shows up as an order-of-magnitude drift |
| `release_idempotent` | `samvada_release()` on uninit'd state | Cheapest public fn; consumers hit it on every clean shutdown |

These are **structural baselines**, not throughput numbers.
None of them touch a real dbus socket; the live-bus paths
(handshake, `TakeDevice`, signal pump) are HW-gated and are the
v1.0 gate work.

## Method

```sh
cyrius bench tests/samvada.bcyr
```

Each bench is `bench_run_batch(..., 1000, 1000)` — 1,000,000
total iterations with 1000 inner iterations per outer pass to
amortize `clock_gettime` overhead. Numbers are wall-clock per
iteration.

The harness is in `lib/bench.cyr` (vendored from cyrius
stdlib); see `tests/samvada.bcyr` for the four call sites.

## Variance & reproducibility

- **Hardware-bound.** Numbers are developer-machine baseline,
  not pinned-runner numbers. CI runs `cyrius bench` as a
  smoke-of-the-harness only — it does not gate on the
  measurements.
- **Frequency scaling.** AMD Ryzen mobile parts move ~10–15%
  on these sub-nanosecond paths depending on whether the
  governor's parked the cores. Anything ±20% across runs on
  the same box is noise.
- **Append-only.** Each release adds one column. Older columns
  stay frozen — this file is the audit trail across the v0.x
  line.

## Run history

| | Run 1 | Run 2 |
|---|---|---|
| **Date (UTC)** | `2026-05-01T21:47:19Z` | `2026-05-01T21:58:21Z` |
| **Commit** | `4c7ada9` | (`0.2.2` release commit) |
| **samvada** | `0.2.1` | `0.2.2` |
| **Toolchain** | `cyrius 5.7.48` | `cyrius 5.7.48` |
| **Host** | `Linux 7.0.2-arch1-1 x86_64`, AMD Ryzen 7 5800H (16T) | same |

### Results

| Benchmark | `0.2.1` | `0.2.2` | Δ |
|---|---|---|---|
| `ffi_alloc` | 56 ns | 59 ns | +5.4% |
| `ffi_get_slot` | 9 ns | 11 ns | +22% |
| `init_reject_null` | 6 ns | 7 ns | +17% |
| `release_idempotent` | 6 ns | 6 ns | 0% |

Notes:

- Run 2's deltas are within the documented per-iteration
  jitter floor on this host (single-laptop, governor not
  pinned). The HIGH-1 fix added a single `_samvada_table != 0`
  comparison + branch to `samvada_init`, which on the
  `init_reject_null` path is one extra load and one extra
  conditional jump — fits the +1 ns delta.
- `ffi_get_slot` was untouched in 0.2.2; the +2 ns is governor
  noise on a hot laptop. CI smoke does not gate on absolute
  numbers; multi-host pinned baselines are the v1.0 gate.
- `release_idempotent` is unchanged (0 ns delta) because the
  fix only touches `samvada_init`; the release path is
  identical in 0.2.2.

## When this doc graduates

The v1.0 gate fills three items below:

1. **Live-bus handshake latency** — first `samvada_init`
   round-trip against a real `dbus-broker` /
   `systemd-logind` (open bus + `GetSessionByPID`).
2. **`TakeDevice` round-trip** — measured against a real DRM
   card node, gated on the consumer (mabda) running the
   logind dispatcher end-to-end.
3. **Signal-pump throughput** — events drained per second
   under a synthetic `PauseDevice` / `ResumeDevice` flood.

When any of those land, this doc gets a new section with the
run shape and the numbers. The CPU-baseline columns stay in
their current place — they are the lower bound, not the
headline.
