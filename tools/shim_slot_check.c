/*
 * shim_slot_check.c — prove each fn-table slot reaches the wrapper it names,
 * and that the wrapper sends logind what it should.
 *
 * 1.1.2 (P(-1) audit). CI already cross-checks the C SLOT_* offsets
 * against the Cyrius ones, but nothing checked what the shim STORES at
 * each offset: swap two lines of samvada_shim_init() and every gate
 * stayed green, while samvada_session_take_device() sent ReleaseDevice.
 *
 * 1.1.2 (P(-1) re-verification). The first cut recorded only the MEMBER:
 * a shim sending TakeDevice(minor, major) -- a different device -- or
 * TakeControl(force = 1) -- stealing control from another compositor --
 * or ReleaseDevice to the wrong service passed. The fake now records the
 * destination, path, interface, member and signature of every call and
 * decodes its arguments by that signature ("uu", "u", "b", ""; anything
 * else is a failure), and each slot's call is compared field by field
 * against literals spelled HERE -- not the shim's LOGIND_* macros, which
 * a mutation would change on both sides. It also closes stdin before the
 * TakeDevice call, so a dup that lands on 0, 1 or 2 is seen.
 *
 * This file includes the shim itself and stands in for libsystemd at
 * link time: every sd_bus_* the shim calls is defined below and records
 * what it was asked to do. It is built WITHOUT -lsystemd, so a shim that
 * starts calling an sd_bus_* this file does not define fails to link —
 * the check can only be incomplete loudly. samvada_main() is stubbed to
 * capture the table, and each slot is then called through it, the way
 * the Cyrius side's fncallN does.
 *
 * 1.1.2 (final pass). The fake libsystemd never FAILED: every call
 * succeeded, sd_bus_process always found the queue empty and the reply
 * always said "active", so the shim's error contract -- the one P1-9 and
 * R-1 rely on for this backend, and nothing else tests -- was unpinned.
 * The reviewer's nine mutants survived the check (a failed ReleaseDevice /
 * ReleaseControl returning 0, active_out inverted, a pump cap of 1, a pump
 * swallowing its error, take_device ignoring a failed TakeDevice, devnum_ok
 * accepting everything, get_session_path without its ENOBUFS bound, a reply
 * never unref'd), and seven more of the same kind; ci.yml runs all sixteen. Every fake's result is now SETTABLE per call (g_open_rc,
 * g_call_rc, g_read_rc, g_read_inactive, g_read_path, g_read_fd,
 * g_process_events / g_process_rc, g_match_rc), the fake counts the
 * replies it hands out, the unrefs and the sd_bus_error_frees, and each
 * wrapper is checked on its failure paths: the sd-bus errno passed through
 * unchanged, nothing read or written after a failure, the reply dropped
 * exactly once on every path, -EINVAL for a device number outside uint32
 * with nothing sent, -ENOBUFS with the caller's buffer untouched, active_out
 * = logind's `inactive` flag, and a pump that stops at 256 events and
 * returns an error rather than a count.
 *
 * Positive controls (CI runs every one and each must FAIL with exit 1):
 *   -DSHIM_CHECK_SELFTEST   swaps TakeControl and ReleaseControl after
 *                           init (a table misroute);
 *   -DSHIM_CHECK_SHIM='"/abs/mutated.c"'
 *                           checks a COPY of the shim instead -- CI sed-
 *                           mutates one (major/minor swapped, force = 1,
 *                           a wrong interface, a dup floor of 0, and the
 *                           final pass's error-path mutants above), and
 *                           each must fail AT ITS OWN CHECK.
 *
 * Build:  cc -Wall -Wextra -Werror $(pkg-config --cflags libsystemd) \
 *            tools/shim_slot_check.c -o build/shim_slot_check
 * Exit:   0 every slot reaches its wrapper, 1 a slot does not.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */
#ifndef SHIM_CHECK_SHIM
#define SHIM_CHECK_SHIM "../deps/samvada_main.c"
#endif
#include SHIM_CHECK_SHIM

#include <stdarg.h>
#include <stdio.h>

/* What logind must be sent -- spelled here, independently of the shim. */
#define WANT_DEST      "org.freedesktop.login1"
#define WANT_MGR_PATH  "/org/freedesktop/login1"
#define WANT_IF_MGR    "org.freedesktop.login1.Manager"
#define WANT_IF_SESS   "org.freedesktop.login1.Session"
#define WANT_SESSION   "/org/freedesktop/login1/session/_31"
#define WANT_PID       4242
#define WANT_MAJOR     226u
#define WANT_MINOR     0u

/* ---- what the fake libsystemd was asked to do ---- */
struct call {
    char dest[64], path[96], iface[64], member[32], types[16];
    uint32_t u[2];
    int b;
    int nvals;                      /* arguments decoded from `types` */
    int undecodable;                /* a signature this fake does not know */
};
#define LOG_MAX 8
static struct call g_calls[LOG_MAX];
static int g_ncalls;
static int g_opened, g_processed, g_closed, g_matched, g_unslotted;
static char g_match_sender[64], g_match_path[96], g_match_iface[64];
static int g_match_any_member;
static int g_read_fd = -1;          /* the fd the fake TakeDevice reply carries */

/* What the fake answers (1.1.2 final pass) -- set per call, reset_log() restores. */
static int g_open_rc;               /* sd_bus_open_system: 0, or a -errno */
static int g_call_rc;               /* sd_bus_call_method: 1 (sd-bus success), or a -errno */
static int g_read_rc;               /* sd_bus_message_read: 1, or a -errno */
static int g_read_inactive;         /* the `b` of a TakeDevice reply */
static const char *g_read_path;     /* the `o` of a GetSessionByPID reply */
static int g_process_events;        /* sd_bus_process returns 1 this many times, */
static int g_process_rc;            /* then this (0: the queue is empty) */
static int g_match_rc;              /* sd_bus_match_signal: 1, or a -errno */
/* ... and what it saw. */
static int g_reads, g_replies, g_unrefs, g_errfrees;

static int fake_bus_obj, fake_msg_obj, fake_slot_obj;

static void
copy(char *dst, size_t n, const char *src)
{
    snprintf(dst, n, "%s", src ? src : "(null)");
}

static void
reset_log(void)
{
    memset(g_calls, 0, sizeof(g_calls));
    g_ncalls = 0;
    g_opened = g_processed = g_closed = g_matched = g_unslotted = 0;
    g_open_rc = 0;
    g_call_rc = 1;
    g_read_rc = 1;
    g_read_inactive = 0;
    g_read_path = WANT_SESSION;
    g_process_events = 0;
    g_process_rc = 0;
    g_match_rc = 1;
    g_reads = g_replies = g_unrefs = g_errfrees = 0;
}

int
sd_bus_open_system(sd_bus **ret)
{
    g_opened++;
    if (g_open_rc < 0) {
        return g_open_rc;
    }
    *ret = (sd_bus *)(void *)&fake_bus_obj;
    return 0;
}

sd_bus *
sd_bus_flush_close_unref(sd_bus *bus)
{
    (void)bus;
    g_closed++;
    return NULL;
}

int
sd_bus_process(sd_bus *bus, sd_bus_message **ret)
{
    (void)bus;
    (void)ret;
    g_processed++;
    return (g_processed <= g_process_events) ? 1 : g_process_rc;
}

int
sd_bus_call_method(sd_bus *bus, const char *destination, const char *path,
                   const char *interface, const char *member,
                   sd_bus_error *reterr_error, sd_bus_message **ret_reply,
                   const char *types, ...)
{
    (void)bus;
    (void)reterr_error;
    if (g_ncalls < LOG_MAX) {
        struct call *c = &g_calls[g_ncalls++];
        const char *t = types ? types : "(null)";
        copy(c->dest, sizeof(c->dest), destination);
        copy(c->path, sizeof(c->path), path);
        copy(c->iface, sizeof(c->iface), interface);
        copy(c->member, sizeof(c->member), member);
        copy(c->types, sizeof(c->types), types);
        va_list ap;
        va_start(ap, types);
        if (strcmp(t, "uu") == 0) {
            c->u[0] = va_arg(ap, uint32_t);
            c->u[1] = va_arg(ap, uint32_t);
            c->nvals = 2;
        } else if (strcmp(t, "u") == 0) {
            c->u[0] = va_arg(ap, uint32_t);
            c->nvals = 1;
        } else if (strcmp(t, "b") == 0) {
            c->b = va_arg(ap, int);
            c->nvals = 1;
        } else if (strcmp(t, "") != 0) {
            c->undecodable = 1;
        }
        va_end(ap);
    }
    if (g_call_rc < 0) {
        return g_call_rc;           /* sd-bus leaves *ret_reply alone on failure */
    }
    if (ret_reply) {
        *ret_reply = (sd_bus_message *)(void *)&fake_msg_obj;
        g_replies++;
    }
    return g_call_rc;               /* sd-bus's positive success */
}

int
sd_bus_message_read(sd_bus_message *m, const char *types, ...)
{
    g_reads++;
    if (m == NULL) {
        return -EINVAL;             /* sd-bus: assert_return(m, -EINVAL) */
    }
    if (g_read_rc < 0) {
        return g_read_rc;
    }
    va_list ap;
    va_start(ap, types);
    if (strcmp(types, "hb") == 0) {
        int *fd = va_arg(ap, int *);
        int *inactive = va_arg(ap, int *);
        *fd = g_read_fd;
        *inactive = g_read_inactive;
    } else if (strcmp(types, "o") == 0) {
        const char **p = va_arg(ap, const char **);
        *p = g_read_path;
    }
    va_end(ap);
    return g_read_rc;
}

sd_bus_message *
sd_bus_message_unref(sd_bus_message *m)
{
    if (m != NULL) {
        g_unrefs++;
    }
    return NULL;
}

void
sd_bus_error_free(sd_bus_error *e)
{
    (void)e;
    g_errfrees++;
}

int
sd_bus_match_signal(sd_bus *bus, sd_bus_slot **ret, const char *sender,
                    const char *path, const char *interface, const char *member,
                    sd_bus_message_handler_t callback, void *userdata)
{
    (void)bus; (void)callback; (void)userdata;
    g_matched++;
    copy(g_match_sender, sizeof(g_match_sender), sender);
    copy(g_match_path, sizeof(g_match_path), path);
    copy(g_match_iface, sizeof(g_match_iface), interface);
    g_match_any_member = (member == NULL);
    if (g_match_rc < 0) {
        return g_match_rc;
    }
    *ret = (sd_bus_slot *)(void *)&fake_slot_obj;
    return g_match_rc;
}

sd_bus_slot *
sd_bus_slot_unref(sd_bus_slot *s)
{
    (void)s;
    g_unslotted++;
    return NULL;
}

/* ---- the Cyrius side, stubbed ---- */
static int64_t *g_table;

long
samvada_main(int64_t fn_table_ptr)
{
    g_table = (int64_t *)(uintptr_t)fn_table_ptr;
    return 0;
}

void _cyrius_init(void) {}
long alloc_init(void) { return 0; }

/* ---- the check ---- */
typedef long (*fn1)(int64_t);
typedef long (*fn2)(int64_t, int64_t);
typedef long (*fn4)(int64_t, int64_t, int64_t, int64_t);
typedef long (*fn6)(int64_t, int64_t, int64_t, int64_t, int64_t, int64_t);

static int g_bad;

static void
dump_calls(void)
{
    for (int i = 0; i < g_ncalls; i++) {
        const struct call *c = &g_calls[i];
        printf("        sent: dest=%s path=%s if=%s member=%s types=\"%s\" args=",
               c->dest, c->path, c->iface, c->member, c->types);
        if (c->undecodable)      printf("(undecodable)\n");
        else if (c->nvals == 0)  printf("()\n");
        else if (strcmp(c->types, "b") == 0) printf("(%d)\n", c->b);
        else if (c->nvals == 1)  printf("(%u)\n", c->u[0]);
        else                     printf("(%u, %u)\n", c->u[0], c->u[1]);
    }
}

static void
expect(int cond, const char *what)
{
    printf("  %s  %s\n", cond ? "ok  " : "FAIL", what);
    if (!cond) {
        g_bad = 1;
        dump_calls();
    }
}

/* Exactly one method call was made, and it is this one (args checked by the caller). */
static int
only_call(const char *path, const char *iface, const char *member, const char *types)
{
    const struct call *c = &g_calls[0];
    return g_ncalls == 1 && !c->undecodable
        && strcmp(c->dest, WANT_DEST) == 0 && strcmp(c->path, path) == 0
        && strcmp(c->iface, iface) == 0 && strcmp(c->member, member) == 0
        && strcmp(c->types, types) == 0
        && g_opened == 0 && g_processed == 0 && g_closed == 0
        && g_matched == 0 && g_unslotted == 0;
}

static int64_t
slot(int off)
{
    return g_table[off / 8];
}

/* Every reply handed out was unref'd exactly once, and the error freed once. */
static int
balanced(void)
{
    return g_unrefs == g_replies && g_errfrees == 1;
}

/*
 * 1.1.2 (final pass): each wrapper on its failure paths, the fake told to
 * fail. Distinct errnos per wrapper, so a pass-through of the WRONG one is
 * seen too.
 */
static void
error_paths(int64_t bus, int64_t sess, int active_when_0)
{
    char path[128];
    int64_t out[2];
    int pfd[2];
    long rc;

    /* slot 0 */
    reset_log();
    g_open_rc = -ENOENT;
    int64_t bus_out = 0x1234;
    rc = ((fn1)(uintptr_t)slot(SLOT_OPEN_SYSTEM_BUS))((int64_t)(uintptr_t)&bus_out);
    expect(rc == -ENOENT && bus_out == 0x1234,
           "slot  0 open_system_bus, sd_bus_open_system fails -> its errno, nothing stored");

    /* slot 8 */
    reset_log();
    g_call_rc = -EACCES;
    memset(path, 0, sizeof(path));
    rc = ((fn4)(uintptr_t)slot(SLOT_GET_SESSION_PATH))(bus, WANT_PID, (int64_t)(uintptr_t)path,
                                                        (int64_t)sizeof(path));
    expect(rc == -EACCES && g_reads == 0 && path[0] == 0 && g_errfrees == 1,
           "slot  8 get_session_path, GetSessionByPID fails -> its errno, nothing read");

    reset_log();
    g_read_rc = -EBADMSG;
    rc = ((fn4)(uintptr_t)slot(SLOT_GET_SESSION_PATH))(bus, WANT_PID, (int64_t)(uintptr_t)path,
                                                        (int64_t)sizeof(path));
    expect(rc == -EBADMSG && path[0] == 0 && g_replies == 1 && balanced(),
           "slot  8 get_session_path, the reply does not decode -> its errno, the reply dropped");

    reset_log();
    g_read_path = NULL;
    rc = ((fn4)(uintptr_t)slot(SLOT_GET_SESSION_PATH))(bus, WANT_PID, (int64_t)(uintptr_t)path,
                                                        (int64_t)sizeof(path));
    expect(rc == -EINVAL && path[0] == 0 && g_replies == 1 && balanced(),
           "slot  8 get_session_path, a NULL path -> -EINVAL, the reply dropped");

    /* One byte short of the NUL: -ENOBUFS and the caller's buffer untouched
     * (the array is larger than the length passed, so a missing bound
     * overwrites it instead of the stack). */
    size_t n = strlen(WANT_SESSION);
    reset_log();
    memset(path, 0x5a, sizeof(path));
    rc = ((fn4)(uintptr_t)slot(SLOT_GET_SESSION_PATH))(bus, WANT_PID, (int64_t)(uintptr_t)path, (int64_t)n);
    int untouched = 1;
    for (size_t i = 0; i < sizeof(path); i++) {
        untouched &= (path[i] == 0x5a);
    }
    expect(rc == -ENOBUFS && untouched && g_replies == 1 && balanced(),
           "slot  8 get_session_path, a path one byte too long -> -ENOBUFS, nothing written");
    reset_log();
    memset(path, 0x5a, sizeof(path));
    rc = ((fn4)(uintptr_t)slot(SLOT_GET_SESSION_PATH))(bus, WANT_PID, (int64_t)(uintptr_t)path, (int64_t)(n + 1));
    expect(rc == 0 && strcmp(path, WANT_SESSION) == 0 && path[n + 1] == 0x5a,
           "slot  8 get_session_path, an exact fit -> copied and NUL-terminated, nothing past it");

    reset_log();
    long r0 = ((fn4)(uintptr_t)slot(SLOT_GET_SESSION_PATH))(bus, WANT_PID, 0, (int64_t)sizeof(path));
    long r1 = ((fn4)(uintptr_t)slot(SLOT_GET_SESSION_PATH))(bus, WANT_PID, (int64_t)(uintptr_t)path, 0);
    long r2 = ((fn4)(uintptr_t)slot(SLOT_GET_SESSION_PATH))(bus, WANT_PID, (int64_t)(uintptr_t)path, -1);
    expect(r0 == -EINVAL && r1 == -EINVAL && r2 == -EINVAL && g_ncalls == 0,
           "slot  8 get_session_path, no buffer or a length <= 0 -> -EINVAL, nothing sent");

    /* slot 16 */
    reset_log();
    g_call_rc = -EPERM;
    out[0] = out[1] = 77;
    rc = ((fn6)(uintptr_t)slot(SLOT_TAKE_DEVICE))(bus, sess, WANT_MAJOR, WANT_MINOR,
                                                   (int64_t)(uintptr_t)&out[0], (int64_t)(uintptr_t)&out[1]);
    expect(rc == -EPERM && g_reads == 0 && out[0] == 77 && out[1] == 77 && g_errfrees == 1,
           "slot 16 take_device, TakeDevice fails -> its errno, nothing read");

    reset_log();
    g_read_rc = -EBADMSG;
    out[0] = out[1] = 77;
    rc = ((fn6)(uintptr_t)slot(SLOT_TAKE_DEVICE))(bus, sess, WANT_MAJOR, WANT_MINOR,
                                                   (int64_t)(uintptr_t)&out[0], (int64_t)(uintptr_t)&out[1]);
    expect(rc == -EBADMSG && out[0] == 77 && out[1] == 77 && g_replies == 1 && balanced(),
           "slot 16 take_device, the reply does not decode -> its errno, the reply dropped");

    reset_log();
    g_read_fd = -1;
    out[0] = out[1] = 77;
    rc = ((fn6)(uintptr_t)slot(SLOT_TAKE_DEVICE))(bus, sess, WANT_MAJOR, WANT_MINOR,
                                                   (int64_t)(uintptr_t)&out[0], (int64_t)(uintptr_t)&out[1]);
    expect(rc == -EBADF && (int)(int32_t)out[0] == -1 && g_replies == 1 && balanced(),
           "slot 16 take_device, the reply carries no fd -> -EBADF, fd_out -1");

    /* active_out is logind's `inactive`, verbatim (the shim's documented
     * contract: dbus says "inactive", the caller inverts if it wants). */
    if (pipe(pfd) != 0) {
        printf("FAIL: pipe()\n");
        g_bad = 1;
        return;
    }
    reset_log();
    g_read_fd = pfd[0];
    g_read_inactive = 1;
    out[0] = out[1] = 0;
    rc = ((fn6)(uintptr_t)slot(SLOT_TAKE_DEVICE))(bus, sess, WANT_MAJOR, WANT_MINOR,
                                                   (int64_t)(uintptr_t)&out[0], (int64_t)(uintptr_t)&out[1]);
    if ((int)(int32_t)out[0] >= 0) {
        close((int)(int32_t)out[0]);
    }
    expect(rc == 0 && (int)(int32_t)out[1] == 1 && active_when_0 == 0,
           "slot 16 take_device      -> active_out carries logind's inactive flag (1 and 0)");
    close(pfd[0]);
    close(pfd[1]);

    /* dbus `u` is uint32: anything outside it is refused before a call. */
    static const int64_t bad_nums[] = { -1, (int64_t)UINT32_MAX + 1, INT64_MIN, INT64_MAX };
    reset_log();
    int all_einval = 1;
    for (size_t i = 0; i < sizeof(bad_nums) / sizeof(bad_nums[0]); i++) {
        all_einval &= ((fn6)(uintptr_t)slot(SLOT_TAKE_DEVICE))(bus, sess, bad_nums[i], WANT_MINOR,
                          (int64_t)(uintptr_t)&out[0], (int64_t)(uintptr_t)&out[1]) == -EINVAL;
        all_einval &= ((fn6)(uintptr_t)slot(SLOT_TAKE_DEVICE))(bus, sess, WANT_MAJOR, bad_nums[i],
                          (int64_t)(uintptr_t)&out[0], (int64_t)(uintptr_t)&out[1]) == -EINVAL;
    }
    expect(all_einval && g_ncalls == 0,
           "slot 16 take_device, a device number outside uint32 -> -EINVAL, nothing sent");
    reset_log();
    g_call_rc = -EPERM;
    rc = ((fn6)(uintptr_t)slot(SLOT_TAKE_DEVICE))(bus, sess, (int64_t)UINT32_MAX, 0,
                                                   (int64_t)(uintptr_t)&out[0], (int64_t)(uintptr_t)&out[1]);
    expect(rc == -EPERM && g_ncalls == 1 && g_calls[0].u[0] == UINT32_MAX && g_calls[0].u[1] == 0,
           "slot 16 take_device, major 0xFFFFFFFF -> sent as is");

    /* slot 24 */
    reset_log();
    g_call_rc = -ENXIO;
    rc = ((fn4)(uintptr_t)slot(SLOT_RELEASE_DEVICE))(bus, sess, WANT_MAJOR, WANT_MINOR);
    expect(rc == -ENXIO && g_ncalls == 1 && g_errfrees == 1,
           "slot 24 release_device, ReleaseDevice fails -> its errno");
    reset_log();
    all_einval = 1;
    for (size_t i = 0; i < sizeof(bad_nums) / sizeof(bad_nums[0]); i++) {
        all_einval &= ((fn4)(uintptr_t)slot(SLOT_RELEASE_DEVICE))(bus, sess, bad_nums[i], WANT_MINOR) == -EINVAL;
        all_einval &= ((fn4)(uintptr_t)slot(SLOT_RELEASE_DEVICE))(bus, sess, WANT_MAJOR, bad_nums[i]) == -EINVAL;
    }
    expect(all_einval && g_ncalls == 0,
           "slot 24 release_device, a device number outside uint32 -> -EINVAL, nothing sent");

    /* slot 32: the bounded pump (audit MED-5). */
    reset_log();
    g_process_events = 3;
    rc = ((fn1)(uintptr_t)slot(SLOT_PUMP_SIGNALS))(bus);
    expect(rc == 3 && g_processed == 4,
           "slot 32 pump_signals, 3 events -> 3, then the empty queue ends it");
    reset_log();
    g_process_events = 100000;
    rc = ((fn1)(uintptr_t)slot(SLOT_PUMP_SIGNALS))(bus);
    expect(rc == 256 && g_processed == 256,
           "slot 32 pump_signals, a flood -> capped at 256 events per call");
    reset_log();
    g_process_rc = -ECONNRESET;
    rc = ((fn1)(uintptr_t)slot(SLOT_PUMP_SIGNALS))(bus);
    expect(rc == -ECONNRESET && g_processed == 1,
           "slot 32 pump_signals, sd_bus_process fails -> its errno");
    reset_log();
    g_process_events = 2;
    g_process_rc = -ECONNRESET;
    rc = ((fn1)(uintptr_t)slot(SLOT_PUMP_SIGNALS))(bus);
    expect(rc == -ECONNRESET && g_processed == 3,
           "slot 32 pump_signals, fails after 2 events -> its errno, not the count");

    /* slot 48 / 56 */
    reset_log();
    g_match_rc = -ENOMEM;
    rc = ((fn4)(uintptr_t)slot(SLOT_SUBSCRIBE_PAUSE_RESUME))(bus, sess, 0, 0);
    expect(rc == -ENOMEM && g_matched == 1,
           "slot 48 subscribe, sd_bus_match_signal fails -> its errno");
    reset_log();
    long u0 = ((fn1)(uintptr_t)slot(SLOT_UNSUBSCRIBE))(0);
    long u1 = ((fn1)(uintptr_t)slot(SLOT_UNSUBSCRIBE))(-ENOMEM);
    expect(u0 == -EINVAL && u1 == -EINVAL && g_unslotted == 0,
           "slot 56 unsubscribe, not a slot (<= 0) -> -EINVAL, nothing dropped");

    /* slot 72 / 80 */
    reset_log();
    g_call_rc = -EBUSY;
    rc = ((fn2)(uintptr_t)slot(SLOT_TAKE_CONTROL))(bus, sess);
    expect(rc == -EBUSY && g_ncalls == 1 && g_errfrees == 1,
           "slot 72 take_control, TakeControl fails -> its errno");
    reset_log();
    g_call_rc = -ENOTCONN;
    rc = ((fn2)(uintptr_t)slot(SLOT_RELEASE_CONTROL))(bus, sess);
    expect(rc == -ENOTCONN && g_ncalls == 1 && g_errfrees == 1,
           "slot 80 release_control, ReleaseControl fails -> its errno");
}

int
main(void)
{
    if (samvada_shim_init() != 0 || g_table == NULL) {
        printf("FAIL: samvada_shim_init did not hand samvada_main a table\n");
        return 1;
    }
#ifdef SHIM_CHECK_SELFTEST
    /* Two slots of the same arity, so the swap misroutes rather than
     * crashes: a swap across arities is caught too, but as a SIGSEGV. */
    int64_t t = g_table[SLOT_TAKE_CONTROL / 8];
    g_table[SLOT_TAKE_CONTROL / 8] = g_table[SLOT_RELEASE_CONTROL / 8];
    g_table[SLOT_RELEASE_CONTROL / 8] = t;
#endif
    int64_t bus = (int64_t)(uintptr_t)&fake_bus_obj;
    static const char sess_path[] = WANT_SESSION;
    int64_t sess = (int64_t)(uintptr_t)sess_path;
    char path[128];
    int64_t out[2];
    int pfd[2];
    long rc;

    reset_log();
    int64_t bus_out = 0;
    rc = ((fn1)(uintptr_t)slot(SLOT_OPEN_SYSTEM_BUS))((int64_t)(uintptr_t)&bus_out);
    expect(rc == 0 && g_opened == 1 && bus_out == bus && g_ncalls == 0,
           "slot  0 open_system_bus  -> sd_bus_open_system, a private connection");

    reset_log();
    memset(path, 0, sizeof(path));
    rc = ((fn4)(uintptr_t)slot(SLOT_GET_SESSION_PATH))(bus, WANT_PID, (int64_t)(uintptr_t)path,
                                                        (int64_t)sizeof(path));
    expect(rc == 0 && only_call(WANT_MGR_PATH, WANT_IF_MGR, "GetSessionByPID", "u")
           && g_calls[0].u[0] == WANT_PID && strcmp(path, WANT_SESSION) == 0,
           "slot  8 get_session_path -> Manager.GetSessionByPID(pid), path copied out");
    expect(rc == 0 && g_replies == 1 && balanced(),
           "slot  8 get_session_path -> the reply dropped and the error freed");

    reset_log();
    if (pipe(pfd) != 0) {
        printf("FAIL: pipe()\n");
        return 1;
    }
    /* stdin closed, so fd 0 is the lowest free one: a dup that may land
     * below 3 lands on it (put back after). */
    int saved_stdin = fcntl(0, F_DUPFD_CLOEXEC, 10);
    close(0);
    g_read_fd = pfd[0];
    out[0] = out[1] = 0;
    rc = ((fn6)(uintptr_t)slot(SLOT_TAKE_DEVICE))(bus, sess, WANT_MAJOR, WANT_MINOR,
                                                   (int64_t)(uintptr_t)&out[0],
                                                   (int64_t)(uintptr_t)&out[1]);
    int got = (int)(int32_t)out[0];
    int cloexec = got >= 0 ? (fcntl(got, F_GETFD) & FD_CLOEXEC) : 0;
    if (got >= 0) {
        close(got);
    }
    if (saved_stdin >= 0) {
        dup2(saved_stdin, 0);
        close(saved_stdin);
    }
    expect(rc == 0 && only_call(WANT_SESSION, WANT_IF_SESS, "TakeDevice", "uu")
           && g_calls[0].u[0] == WANT_MAJOR && g_calls[0].u[1] == WANT_MINOR,
           "slot 16 take_device      -> Session.TakeDevice(226, 0), major first");
    expect(got >= 3 && got != pfd[0] && cloexec,
           "slot 16 take_device      -> the fd duplicated close-on-exec, never onto 0-2");
    expect(rc == 0 && g_replies == 1 && balanced(),
           "slot 16 take_device      -> the reply dropped and the error freed");
    int active_when_0 = (int)(int32_t)out[1];

    /* A dup that fails after logind granted the device fails the take, and
     * sends nothing more: the Cyrius layer gives the device back (1.1.2). */
    reset_log();
    g_read_fd = 1000;               /* not open: F_DUPFD_CLOEXEC fails EBADF */
    close(1000);
    rc = ((fn6)(uintptr_t)slot(SLOT_TAKE_DEVICE))(bus, sess, WANT_MAJOR, WANT_MINOR,
                                                   (int64_t)(uintptr_t)&out[0],
                                                   (int64_t)(uintptr_t)&out[1]);
    expect(rc == -EBADF && only_call(WANT_SESSION, WANT_IF_SESS, "TakeDevice", "uu")
           && (int)(int32_t)out[0] < 0,
           "slot 16 take_device, dup fails -> -EBADF, TakeDevice only (no second release)");
    close(pfd[0]);
    close(pfd[1]);

    reset_log();
    rc = ((fn4)(uintptr_t)slot(SLOT_RELEASE_DEVICE))(bus, sess, WANT_MAJOR, WANT_MINOR);
    expect(rc == 0 && only_call(WANT_SESSION, WANT_IF_SESS, "ReleaseDevice", "uu")
           && g_calls[0].u[0] == WANT_MAJOR && g_calls[0].u[1] == WANT_MINOR,
           "slot 24 release_device   -> Session.ReleaseDevice(226, 0), sd-bus's 1 normalised to 0");

    reset_log();
    rc = ((fn1)(uintptr_t)slot(SLOT_PUMP_SIGNALS))(bus);
    expect(rc == 0 && g_processed == 1 && g_ncalls == 0,
           "slot 32 pump_signals     -> sd_bus_process");

    reset_log();
    rc = ((fn4)(uintptr_t)slot(SLOT_SUBSCRIBE_PAUSE_RESUME))(bus, sess, 0, 0);
    expect(rc == (long)(uintptr_t)&fake_slot_obj && g_matched == 1 && g_ncalls == 0
           && strcmp(g_match_sender, WANT_DEST) == 0 && strcmp(g_match_path, WANT_SESSION) == 0
           && strcmp(g_match_iface, WANT_IF_SESS) == 0 && g_match_any_member,
           "slot 48 subscribe        -> sd_bus_match_signal(login1, session, Session, any)");

    reset_log();
    rc = ((fn1)(uintptr_t)slot(SLOT_UNSUBSCRIBE))((int64_t)(uintptr_t)&fake_slot_obj);
    expect(rc == 0 && g_unslotted == 1 && g_ncalls == 0,
           "slot 56 unsubscribe      -> sd_bus_slot_unref");

    expect(slot(SLOT_KIND) == BACKEND_KIND_LIBSYSTEMD,
           "slot 64 kind             == LIBSYSTEMD");

    reset_log();
    rc = ((fn2)(uintptr_t)slot(SLOT_TAKE_CONTROL))(bus, sess);
    expect(rc == 0 && only_call(WANT_SESSION, WANT_IF_SESS, "TakeControl", "b")
           && g_calls[0].b == 0,
           "slot 72 take_control     -> Session.TakeControl(force = false)");

    reset_log();
    rc = ((fn2)(uintptr_t)slot(SLOT_RELEASE_CONTROL))(bus, sess);
    expect(rc == 0 && only_call(WANT_SESSION, WANT_IF_SESS, "ReleaseControl", "")
           && g_calls[0].nvals == 0,
           "slot 80 release_control  -> Session.ReleaseControl()");

    reset_log();
    rc = ((fn1)(uintptr_t)slot(SLOT_CLOSE_BUS))(bus);
    expect(rc == 0 && g_closed == 1 && g_ncalls == 0,
           "slot 40 close_bus        -> sd_bus_flush_close_unref");

    error_paths(bus, sess, active_when_0);

    printf(g_bad ? "\nthe fn table does not match its wrappers\n"
                 : "\nevery slot reaches the wrapper it names, with logind's arguments\n");
    return g_bad;
}
