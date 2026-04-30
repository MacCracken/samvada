/*
 * samvada — libsystemd C shim entry point (v0.2.0).
 *
 * Mirrors mabda/deps/wgpu_main.c exactly: C `main()` initializes
 * the Cyrius runtime + heap, builds a function table backed by
 * `sd_bus_*` wrappers, then calls into `samvada_main(table)`.
 * The Cyrius side dispatches through the table via `fncallN`
 * (see src/samvada_ffi.cyr for slot offsets).
 *
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Build (consumer-side; samvada itself doesn't link libsystemd):
 *   cc -c deps/samvada_main.c -o build/samvada_main.o
 *   cyrius build src/main.cyr build/samvada.o
 *   cc build/samvada_main.o build/samvada.o -lsystemd -o samvada
 *
 * Every wrapper has <=6 args so dispatch is fncall6-safe on both
 * x86_64 SysV and aarch64 (cyrius's 6-arg fncall convention).
 */

#define _GNU_SOURCE
#include <errno.h>
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
#define FFI_SIZE                    72

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
 * GetSessionByPID(pid) -> object_path. Writes the cstr (NUL-terminated
 * up to out_buf_len-1 bytes) into out_buf. Returns 0 on success,
 * -ENOBUFS if the path didn't fit, otherwise an sd-bus negative err.
 */
static long
sb_get_session_path(int64_t bus_, int64_t pid, int64_t out_buf, int64_t out_buf_len)
{
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
     * caller has a stable handle independent of message lifetime. */
    int dup_fd = (fd >= 0) ? dup(fd) : -1;
    *(int32_t *)(uintptr_t)fd_out     = (int32_t)dup_fd;
    *(int32_t *)(uintptr_t)active_out = (int32_t)inactive;

    sd_bus_message_unref(reply);
    sd_bus_error_free(&err);
    return (dup_fd < 0) ? -errno : 0;
}

static long
sb_release_device(int64_t bus_, int64_t sess_cstr, int64_t major, int64_t minor)
{
    sd_bus *bus = (sd_bus *)(uintptr_t)bus_;
    const char *sess = (const char *)(uintptr_t)sess_cstr;
    sd_bus_error err = SD_BUS_ERROR_NULL;

    int r = sd_bus_call_method(bus, LOGIND_DEST, sess, LOGIND_IF_S,
                               "ReleaseDevice", &err, NULL, "uu",
                               (uint32_t)major, (uint32_t)minor);
    sd_bus_error_free(&err);
    return (long)r;
}

/*
 * Drains pending bus events. Returns the number of messages
 * processed (>=0), or a negative sd-bus errno. Cyrius callers
 * pump this from their own loop tick; samvada owns no event loop.
 */
static long
sb_pump_signals(int64_t bus_)
{
    sd_bus *bus = (sd_bus *)(uintptr_t)bus_;
    long drained = 0;
    for (;;) {
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
    sd_bus_slot *slot = (sd_bus_slot *)(uintptr_t)slot_;
    sd_bus_slot_unref(slot);
    return 0;
}

/* ---- Entry point ---- */

int
main(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    _cyrius_init();
    alloc_init();

    /*
     * 9-slot table laid out in src/samvada_ffi.cyr. Stack-resident
     * is fine because samvada_main() never returns until the
     * process exits — the table outlives every dispatch through it.
     */
    int64_t table[FFI_SIZE / 8];
    memset(table, 0, sizeof(table));

    table[SLOT_OPEN_SYSTEM_BUS         / 8] = (int64_t)(uintptr_t)sb_open_system_bus;
    table[SLOT_GET_SESSION_PATH        / 8] = (int64_t)(uintptr_t)sb_get_session_path;
    table[SLOT_TAKE_DEVICE             / 8] = (int64_t)(uintptr_t)sb_take_device;
    table[SLOT_RELEASE_DEVICE          / 8] = (int64_t)(uintptr_t)sb_release_device;
    table[SLOT_PUMP_SIGNALS            / 8] = (int64_t)(uintptr_t)sb_pump_signals;
    table[SLOT_CLOSE_BUS               / 8] = (int64_t)(uintptr_t)sb_close_bus;
    table[SLOT_SUBSCRIBE_PAUSE_RESUME  / 8] = (int64_t)(uintptr_t)sb_subscribe_pause_resume;
    table[SLOT_UNSUBSCRIBE             / 8] = (int64_t)(uintptr_t)sb_unsubscribe;
    table[SLOT_KIND                    / 8] = BACKEND_KIND_LIBSYSTEMD;

    return (int)samvada_main((int64_t)(uintptr_t)table);
}
