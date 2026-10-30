# Architecture notes

Non-obvious constraints, quirks, and invariants that a reader cannot derive from the code alone. Numbered chronologically — never renumber.

Not decisions (those live in [`../adr/`](../adr/)) and not guides (those live in [`../guides/`](../guides/)). An item here describes *how the world is*, not *what we chose* or *how to do something*.

## Items

- [`public-api.md`](public-api.md) — the public contract: each of the eight fns, its preconditions, every error code and the test that pins it.
- [`dbus-marshalling.md`](dbus-marshalling.md) — the slice of the D-Bus wire format samvada's calls exercise.

Neither is numbered. Add new entries as `NNN-kebab-case-title.md`; do not write entries for decisions — those are ADRs.
