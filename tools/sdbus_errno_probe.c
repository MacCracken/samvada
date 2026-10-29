/* sdbus_errno_probe.c — ask libsystemd what errno each D-Bus error name maps to.
 *
 * The native backend must return the same negative errno the C shim's sd-bus
 * would (CLAUDE.md: "Errors are negative sd-bus errnos at the FFI boundary").
 * dbus_session_map_error() in src/dbus_session.cyr is that mapping, and
 * tests/fixtures/sdbus_errno_map.tsv is its ground truth. This probe is how
 * the fixture was made and how CI re-checks it against the runner's
 * libsystemd:
 *
 *   cc tools/sdbus_errno_probe.c $(pkg-config --cflags --libs libsystemd) -o build/probe
 *   ./build/probe < tests/fixtures/sdbus_errno_map.tsv | diff tests/fixtures/sdbus_errno_map.tsv -
 *
 * Reads the fixture itself: '#' lines are echoed unchanged, and every other
 * line's first tab-separated field is a name, printed back as
 * "<name>\t<errno sd_bus_error_set() returns>". Not shipped, not linked into
 * anything; samvada itself does not link libsystemd.
 */
#include <stdio.h>
#include <string.h>
#include <systemd/sd-bus.h>

int main(void) {
    char line[512];
    while (fgets(line, sizeof line, stdin)) {
        line[strcspn(line, "\n")] = '\0';
        if (line[0] == '#') {
            printf("%s\n", line);
            continue;
        }
        line[strcspn(line, "\t")] = '\0';
        if (line[0] == '\0') {
            continue;
        }
        sd_bus_error e = SD_BUS_ERROR_NULL;
        int r = sd_bus_error_set(&e, line, "probe");
        printf("%s\t%d\n", line, r);
        sd_bus_error_free(&e);
    }
    return 0;
}
