/*
 * samvada — libsystemd C shim entry point (v0.5.1).
 *
 * The consumer's `main()` initializes the Cyrius runtime + heap and calls
 * `samvada_shim_init()`, which builds a static function table backed by
 * `sd_bus_*` wrappers and calls into `samvada_main(table)`.
 * The Cyrius side dispatches through the table via `fncallN`
 * (see src/samvada_ffi.cyr for slot offsets).
 *
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Build (consumer-side; samvada itself doesn't link libsystemd):
 *   cc -c deps/samvada_main.c $(pkg-config --cflags libsystemd) -o samvada_main.o
 *   # ... compile your own Cyrius object, which defines main() ...
 *   cc samvada_main.o your_app.o $(pkg-config --libs libsystemd) -o your_app
 * Your main() calls samvada_shim_init() once during startup. For a
 * standalone probe binary instead, add -DSAMVADA_STANDALONE_MAIN and this
 * file supplies main() itself.
 *
 * Every wrapper has <=6 args so dispatch is fncall6-safe on both
 * x86_64 SysV and aarch64 (cyrius's 6-arg fncall convention).
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <systemd/sd-bus.h>

/* ---- Cyrius runtime hooks (defined on the Cyrius side) ---- */
extern void _cyrius_init(void);
extern long alloc_init(void);
extern long samvada_main(int64_t fn_table_ptr);

/* ---- Slot offsets (must mirror src/samvada_ffi.cyr exactly) ---- */
#define SLOT_OPEN_SYSTEM_BUS         0
#define SLOT_GET_SESSION_PATH        8
#define SLOT_TAKE_DEVICE            16
#define SLOT_RELEASE_DEVICE         24
#define SLOT_PUMP_SIGNALS           32
#define SLOT_CLOSE_BUS              40
#define SLOT_SUBSCRIBE_PAUSE_RESUME 48
#define SLOT_UNSUBSCRIBE            56
#define SLOT_KIND                   64
#define SLOT_TAKE_CONTROL           72
#define SLOT_RELEASE_CONTROL        80
#define FFI_SIZE                    88

#define BACKEND_KIND_LIBSYSTEMD 1

/* logind well-known names */
#define LOGIND_DEST  "org.freedesktop.login1"
#define LOGIND_MGR   "/org/freedesktop/login1"
#define LOGIND_IF_M  "org.freedesktop.login1.Manager"
#define LOGIND_IF_S  "org.freedesktop.login1.Session"

/*
 * Negative return values are dbus / errno error codes (libsystemd
 * convention: -errno). 0 means success. Cyrius callers branch on
 * `< 0` for the err path and never inspect the magnitude beyond
 * logging it.
 */

static long
sb_open_system_bus(int64_t bus_out_ptr)
{
    sd_bus *bus = NULL;
    int r = sd_bus_default_system(&bus);
    if (r < 0) {
        return (long)r;
    }
    *(sd_bus **)(uintptr_t)bus_out_ptr = bus;
    return 0;
}

/*
 * dbus carries device numbers as `u` (uint32). Cyrius integers are 64-bit
 * SIGNED, so a negative or oversized value would silently wrap on the cast.
 * SECURITY.md's threat model claims these are "validated at C boundary" --
 * this is the validation that makes that claim true (0.5.1, audit MED-2).
 */
static int
devnum_ok(int64_t v)
{
    return (v >= 0) && (v <= (int64_t)UINT32_MAX);
}

/*
 * GetSessionByPID(pid) -> object_path. Writes the cstr (NUL-terminated
 * up to out_buf_len-1 bytes) into out_buf. Returns 0 on success,
 * -ENOBUFS if the path didn't fit, otherwise an sd-bus negative err.
 */
static long
sb_get_session_path(int64_t bus_, int64_t pid, int64_t out_buf, int64_t out_buf_len)
{
    /* A negative length would become a huge size_t and defeat the bounds
     * check below; reject before the cast (0.5.1, audit LOW-4). */
    if (out_buf == 0 || out_buf_len <= 0) {
        return -EINVAL;
    }
    sd_bus *bus = (sd_bus *)(uintptr_t)bus_;
    sd_bus_error err = SD_BUS_ERROR_NULL;
    sd_bus_message *reply = NULL;
    const char *path = NULL;

    int r = sd_bus_call_method(bus, LOGIND_DEST, LOGIND_MGR, LOGIND_IF_M,
                               "GetSessionByPID", &err, &reply, "u",
                               (uint32_t)pid);
    if (r < 0) {
        sd_bus_error_free(&err);
        return (long)r;
    }
    r = sd_bus_message_read(reply, "o", &path);
    if (r < 0) {
        sd_bus_message_unref(reply);
        sd_bus_error_free(&err);
        return (long)r;
    }
    /* Closes LOW-1 from the 2026-05-01 review, deferred twice. libsystemd
     * guarantees path != NULL on r >= 0, so this is defense-in-depth against
     * a contract violation -- but it is one line and the deferral rationale
     * ("wants its own test") never produced the test. */
    if (path == NULL) {
        sd_bus_message_unref(reply);
        sd_bus_error_free(&err);
        return -EINVAL;
    }

    size_t n = strlen(path);
    char *dst = (char *)(uintptr_t)out_buf;
    if (n + 1 > (size_t)out_buf_len) {
        sd_bus_message_unref(reply);
        sd_bus_error_free(&err);
        return -ENOBUFS;
    }
    memcpy(dst, path, n);
    dst[n] = '\0';

    sd_bus_message_unref(reply);
    sd_bus_error_free(&err);
    return 0;
}

/*
 * TakeDevice(uint32 major, uint32 minor) -> (h fd, b inactive).
 * Writes the FD into *fd_out and the inactive flag (0 or 1) into
 * *active_out. Note: dbus returns "inactive", not "active". Callers
 * inverting the sense should do so explicitly at the wrapper layer.
 */
static long
sb_take_device(int64_t bus_, int64_t sess_cstr, int64_t major, int64_t minor,
               int64_t fd_out, int64_t active_out)
{
    sd_bus *bus = (sd_bus *)(uintptr_t)bus_;
    const char *sess = (const char *)(uintptr_t)sess_cstr;
    sd_bus_error err = SD_BUS_ERROR_NULL;
    sd_bus_message *reply = NULL;
    int fd = -1;
    int inactive = 0;

    if (!devnum_ok(major) || !devnum_ok(minor)) {
        return -EINVAL;
    }

    int r = sd_bus_call_method(bus, LOGIND_DEST, sess, LOGIND_IF_S,
                               "TakeDevice", &err, &reply, "uu",
                               (uint32_t)major, (uint32_t)minor);
    if (r < 0) {
        sd_bus_error_free(&err);
        return (long)r;
    }
    r = sd_bus_message_read(reply, "hb", &fd, &inactive);
    if (r < 0) {
        sd_bus_message_unref(reply);
        sd_bus_error_free(&err);
        return (long)r;
    }

    /* sd_bus owns the fd until the message is unref'd; dup so the
     * caller has a stable handle independent of message lifetime.
     *
     * Two defensive checks per 2026-05-01 hardening review:
     *  - MED-1: if the message read succeeded but delivered fd<0
     *    (peer-bus contract violation), explicit -EBADF rather
     *    than reading stale errno from a syscall we never made.
     *  - errno is captured before the cleanup calls so a future
     *    libsystemd that touches errno on unref/free can't clobber
     *    the dup() failure code.
     */
    if (fd < 0) {
        sd_bus_message_unref(reply);
        sd_bus_error_free(&err);
        *(int32_t *)(uintptr_t)fd_out     = -1;
        *(int32_t *)(uintptr_t)active_out = (int32_t)inactive;
        return -EBADF;
    }
    /* F_DUPFD_CLOEXEC, not dup(): dup() CLEARS FD_CLOEXEC, so the DRM-master
     * fd would survive any execve the consumer performs and leak master
     * rights into an unrelated child (0.5.1, audit MED-3). */
    int dup_fd = fcntl(fd, F_DUPFD_CLOEXEC, 0);
    int dup_errno = (dup_fd < 0) ? errno : 0;
    *(int32_t *)(uintptr_t)fd_out     = (int32_t)dup_fd;
    *(int32_t *)(uintptr_t)active_out = (int32_t)inactive;

    sd_bus_message_unref(reply);
    sd_bus_error_free(&err);
    return (dup_fd < 0) ? -dup_errno : 0;
}

static long
sb_release_device(int64_t bus_, int64_t sess_cstr, int64_t major, int64_t minor)
{
    sd_bus *bus = (sd_bus *)(uintptr_t)bus_;
    const char *sess = (const char *)(uintptr_t)sess_cstr;
    sd_bus_error err = SD_BUS_ERROR_NULL;

    if (!devnum_ok(major) || !devnum_ok(minor)) {
        return -EINVAL;
    }

    int r = sd_bus_call_method(bus, LOGIND_DEST, sess, LOGIND_IF_S,
                               "ReleaseDevice", &err, NULL, "uu",
                               (uint32_t)major, (uint32_t)minor);
    sd_bus_error_free(&err);
    /* sd_bus_call_method returns a POSITIVE value (1) on success. The
     * documented Cyrius contract is `0 | -err`, so normalise here rather
     * than leaking libsystemd's convention (0.5.1, audit MED-4). */
    return (r < 0) ? (long)r : 0;
}

/*
 * TakeControl(b force) -- MANDATORY before TakeDevice. logind rejects
 * TakeDevice with org.freedesktop.login1.NotInControl unless this same bus
 * connection holds session control. samvada 0.2.0-0.5.0 never sent it, so
 * take_device could not succeed in any environment (audit CRIT-1).
 *
 * force = 0: do not steal control from another controller.
 */
static long
sb_take_control(int64_t bus_, int64_t sess_cstr)
{
    sd_bus *bus = (sd_bus *)(uintptr_t)bus_;
    const char *sess = (const char *)(uintptr_t)sess_cstr;
    sd_bus_error err = SD_BUS_ERROR_NULL;

    int r = sd_bus_call_method(bus, LOGIND_DEST, sess, LOGIND_IF_S,
                               "TakeControl", &err, NULL, "b", 0);
    sd_bus_error_free(&err);
    return (r < 0) ? (long)r : 0;
}

/* ReleaseControl() -- drop session control at teardown. Takes no arguments
 * on the wire; logind also drops control implicitly when the connection
 * closes, so failure here is not fatal. */
static long
sb_release_control(int64_t bus_, int64_t sess_cstr)
{
    sd_bus *bus = (sd_bus *)(uintptr_t)bus_;
    const char *sess = (const char *)(uintptr_t)sess_cstr;
    sd_bus_error err = SD_BUS_ERROR_NULL;

    int r = sd_bus_call_method(bus, LOGIND_DEST, sess, LOGIND_IF_S,
                               "ReleaseControl", &err, NULL, "");
    sd_bus_error_free(&err);
    return (r < 0) ? (long)r : 0;
}

/*
 * Drains pending bus events. Returns the number of messages
 * processed (>=0), or a negative sd-bus errno. Cyrius callers
 * pump this from their own loop tick; samvada owns no event loop.
 */
/*
 * Bounded drain. The pre-0.5.1 loop ran until the queue emptied, so any peer
 * able to emit signals on this connection could hold the caller captive --
 * measured at 0.77 s for a single call under an unprivileged local flood
 * (audit MED-5). The public Cyrius signature `samvada_pump_signals()` is
 * FROZEN and takes no arguments, so the cap lives here rather than becoming
 * a parameter. Callers pump once per event-loop tick; a residual queue is
 * simply drained on the next tick.
 */
#define SAMVADA_PUMP_MAX_EVENTS 256

static long
sb_pump_signals(int64_t bus_)
{
    sd_bus *bus = (sd_bus *)(uintptr_t)bus_;
    long drained = 0;
    while (drained < SAMVADA_PUMP_MAX_EVENTS) {
        int r = sd_bus_process(bus, NULL);
        if (r < 0)  { return (long)r; }
        if (r == 0) { break; }
        drained++;
    }
    return drained;
}

static long
sb_close_bus(int64_t bus_)
{
    sd_bus *bus = (sd_bus *)(uintptr_t)bus_;
    sd_bus_unref(bus);
    return 0;
}

/*
 * Subscribe to PauseDevice + ResumeDevice on the given session
 * path. The callback signature on the Cyrius side is the standard
 * sd_bus_message_handler_t shape (msg, userdata, ret_error) — but
 * Cyrius callers will typically register a thin shim that captures
 * (major, minor, kind) and queues the event for their own loop.
 *
 * NOTE: this single C wrapper installs *one* match (PauseDevice).
 * For ResumeDevice the Cyrius caller invokes us a second time with
 * a different callback; each call returns its own slot to unsubscribe
 * independently. Two slots > one C function juggling two callbacks
 * keeps the fncall arg count <=6.
 *
 * Returns the sd_bus_slot* (cast to long, always positive) on
 * success, or a negative errno on failure.
 */
static long
sb_subscribe_pause_resume(int64_t bus_, int64_t sess_cstr,
                          int64_t callback, int64_t userdata)
{
    sd_bus *bus = (sd_bus *)(uintptr_t)bus_;
    const char *sess = (const char *)(uintptr_t)sess_cstr;
    sd_bus_message_handler_t cb = (sd_bus_message_handler_t)(uintptr_t)callback;
    sd_bus_slot *slot = NULL;

    int r = sd_bus_match_signal(bus, &slot, LOGIND_DEST, sess, LOGIND_IF_S,
                                NULL /* any member: caller filters */,
                                cb, (void *)(uintptr_t)userdata);
    if (r < 0) {
        return (long)r;
    }
    return (long)(uintptr_t)slot;
}

static long
sb_unsubscribe(int64_t slot_)
{
    /* sb_subscribe_pause_resume returns EITHER a slot pointer (positive) or
     * a negative errno. A caller that forgot to check would hand us the
     * errno, which would become a wild pointer. Reject anything that cannot
     * be a pointer (0.5.1, audit LOW-7). */
    if (slot_ <= 0) {
        return -EINVAL;
    }
    sd_bus_slot *slot = (sd_bus_slot *)(uintptr_t)slot_;
    sd_bus_slot_unref(slot);
    return 0;
}

/* ---- Entry point ---- */

/*
 * The fn-table MUST have static storage duration. samvada_init() stashes
 * this pointer in Cyrius module-scope state and re-reads it with load64 on
 * EVERY subsequent dispatch -- it borrows the table, it never copies it. A
 * stack-local would dangle the moment its frame popped.
 *
 * The pre-0.5.1 comment here claimed "samvada_main() never returns until the
 * process exits". That was false: src/samvada.cyr's samvada_main() calls
 * samvada_init() and returns immediately. It was harmless only because the
 * old main() exited right after (audit LOW-1 / INFO).
 */
static int64_t samvada_fn_table[FFI_SIZE / 8];

/*
 * Library-style entry point -- call this once from your own main().
 *
 * Returns 0 on success or a negative sd-bus errno. After it returns 0 the
 * Cyrius public API (samvada_session_take_device etc.) is live.
 */
long
samvada_shim_init(void)
{
    memset(samvada_fn_table, 0, sizeof(samvada_fn_table));

    samvada_fn_table[SLOT_OPEN_SYSTEM_BUS        / 8] = (int64_t)(uintptr_t)sb_open_system_bus;
    samvada_fn_table[SLOT_GET_SESSION_PATH       / 8] = (int64_t)(uintptr_t)sb_get_session_path;
    samvada_fn_table[SLOT_TAKE_DEVICE            / 8] = (int64_t)(uintptr_t)sb_take_device;
    samvada_fn_table[SLOT_RELEASE_DEVICE         / 8] = (int64_t)(uintptr_t)sb_release_device;
    samvada_fn_table[SLOT_PUMP_SIGNALS           / 8] = (int64_t)(uintptr_t)sb_pump_signals;
    samvada_fn_table[SLOT_CLOSE_BUS              / 8] = (int64_t)(uintptr_t)sb_close_bus;
    samvada_fn_table[SLOT_SUBSCRIBE_PAUSE_RESUME / 8] = (int64_t)(uintptr_t)sb_subscribe_pause_resume;
    samvada_fn_table[SLOT_UNSUBSCRIBE            / 8] = (int64_t)(uintptr_t)sb_unsubscribe;
    samvada_fn_table[SLOT_KIND                   / 8] = BACKEND_KIND_LIBSYSTEMD;
    samvada_fn_table[SLOT_TAKE_CONTROL           / 8] = (int64_t)(uintptr_t)sb_take_control;
    samvada_fn_table[SLOT_RELEASE_CONTROL        / 8] = (int64_t)(uintptr_t)sb_release_control;

    return samvada_main((int64_t)(uintptr_t)samvada_fn_table);
}

/*
 * Standalone main(), compiled ONLY under -DSAMVADA_STANDALONE_MAIN.
 *
 * BREAKING in 0.5.1: this used to be compiled unconditionally, which made the
 * shim impossible to link into any consumer that owns its own main() -- i.e.
 * every real consumer (mabda's deps/wgpu_main.c defines one). Linking both
 * produced "multiple definition of `main'". The documented two-stage build
 * could therefore never have worked (audit CRIT-2). Consumers now call
 * samvada_shim_init() from their own main(); pass -DSAMVADA_STANDALONE_MAIN
 * only for a standalone probe binary.
 */
#ifdef SAMVADA_STANDALONE_MAIN
int
main(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    _cyrius_init();
    alloc_init();

    return (int)samvada_shim_init();
}
#endif /* SAMVADA_STANDALONE_MAIN */
