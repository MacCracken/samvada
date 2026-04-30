# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in samvada, please
report it responsibly through **GitHub Security Advisories**:

1. Go to the [Security tab](../../security/advisories) of this repository.
2. Click **"Report a vulnerability"**.
3. Fill in the details and submit.

**Do not open a public issue for security vulnerabilities.**

## Scope

This policy covers samvada's own source — `src/*.cyr`,
`deps/samvada_main.c`, and the `dist/samvada.cyr` bundle.

samvada wraps `libsystemd`'s `sd_bus_*` API in v0.x; vulnerabilities
in libsystemd itself, in the dbus broker, in logind, or in the
underlying kernel session-management primitives should be reported
upstream (systemd / your distro). If an upstream bug specifically
affects samvada users — for example, a libsystemd misuse only
exposed by samvada's wrapper — flag it here and we will harden
the wrapper, document the workaround, or both.

## Supported Versions

| Version | Supported                                                  |
|---------|------------------------------------------------------------|
| 0.2.x   | **Yes** — current release, receives security fixes         |
| 0.1.x   | No — pre-protocol scaffold only                            |

Once v1.0 ships and the libsystemd C shim retires (replaced by
the pure-Cyrius dbus marshaller, or removed entirely — see
`docs/development/roadmap.md` §M3), v0.x will move to
security-fix-only maintenance for one release cycle, then EOL.

## Response Timeline

| Action                           | Target                     |
|----------------------------------|----------------------------|
| Acknowledgement                  | Within **48 hours**        |
| Initial assessment               | Within **5 business days** |
| Fix for CRITICAL severity        | Within **14 days**         |
| Fix for HIGH severity            | Within **30 days**         |
| Fix for MEDIUM / LOW severity    | Next scheduled release     |

Severity ladder: **CRITICAL** (exploitable immediately) /
**HIGH** (moderate effort) / **MEDIUM** (specific conditions) /
**LOW** (defense-in-depth).

## Threat Model

samvada sits between consumer Cyrius code and the system dbus
daemon (typically `dbus-broker` or `dbus-daemon`). Inputs samvada
trusts vs. validates:

| Source | Trust | Notes |
|---|---|---|
| Consumer-supplied `major` / `minor` device numbers | validated at C boundary | passed through to `TakeDevice` as `uu`; logind enforces ACLs |
| Consumer-supplied `pid` (for `GetSessionByPID`) | trusted | typically the consumer's own `getpid()`; no special permission to look up |
| Bytes from the system dbus socket | validated by libsystemd | `sd_bus_message_read` enforces signature match before we see the data |
| FDs received via `SCM_RIGHTS` | dup'd before the message is unref'd | prevents fd reuse-after-close in the consumer |
| logind's `PauseDevice` / `ResumeDevice` signals | trusted (signed by logind's bus name) | only the well-known dest can emit these |

## Design Principles

- **No filesystem I/O.** samvada does not read or write any
  files. The dbus socket is the only kernel-visible side effect.
- **No process creation.** No `execve`, no `fork`, no
  `sys_system`. CI scans for these patterns.
- **No writes to system paths.** `/etc/`, `/bin/`, `/sbin/` are
  CI-rejected as string literals — samvada has no business
  touching them.
- **No raw pointer arithmetic in user code.** Cyrius's
  `load64` / `store64` over named offset constants (the
  offset-table-on-heap pattern) replaces struct types.
- **`fncall6` ceiling honored.** Every C wrapper takes ≤6 args
  so dispatch stays portable across x86_64 SysV + aarch64.
- **Fd-passing is `dup`-safe.** The C shim `dup`s every fd
  returned via `SCM_RIGHTS` before unref'ing the dbus message,
  so the consumer's fd lifetime is independent of dbus message
  lifetime.
- **Errors are sd-bus negative errnos.** Pass-through from
  libsystemd; consumers branch on `< 0` and never inspect
  magnitudes beyond logging.

## Audit History

samvada is pre-audit while the C-shim era runs. The first audit
pass is gated to v1.0 — either the pure-Cyrius marshaller (Path
A in `roadmap.md` §M3) which gets a P(-1) scaffold-hardening
pass before tagging, or the removal path (Path B) which doesn't
need a samvada audit because the surface goes away.

In the meantime, every PR runs CI security scans for the
patterns above (raw execve / fork / sys_system / system-path
writes / large stack buffers) and fmt + lint + vet drift gates.

## Disclosure

We follow coordinated disclosure. Once a fix is released, we
will publish a security advisory crediting the reporter (unless
anonymity is requested). Audit findings that surface internally
are disclosed through `docs/audit/*.md` and the corresponding
CHANGELOG entry.
