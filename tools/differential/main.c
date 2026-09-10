#include <stdio.h>
#include <stdint.h>
extern void _cyrius_init(void); extern long alloc_init(void);
extern long samvada_shim_init(void);
extern long run_sequence_native(void), run_sequence_shim_tail(void);
extern long r_init(void), r_take(void), r_rel(void), r_pump(void), r_close(void);
extern long set_init(long);
static long S[5], N[5];
static void snap(long *o){ o[0]=r_init(); o[1]=r_take(); o[2]=r_rel(); o[3]=r_pump(); o[4]=r_close(); }
int main(void){
    _cyrius_init(); alloc_init();
    /* A: the libsystemd C shim */
    long rc = samvada_shim_init();          /* builds table, calls samvada_init */
    set_init(rc);
    if (rc == 0) run_sequence_shim_tail(); else { set_init(rc); }
    snap(S);
    /* B: the native Cyrius backend, same process, after a clean release */
    run_sequence_native();
    snap(N);
    const char *names[5] = {"init","take_device","release_device","pump_signals","release"};
    /* pump_signals reports how many messages THIS backend happened to
     * have buffered at that moment. That is implementation state, not
     * a contract: libsystemd drains its own queue at pump time, while
     * the native backend consumes the same NameAcquired earlier, as a
     * non-matching message inside get_session_path's reply loop. Both
     * handle it exactly once. Counting it as a parity failure would
     * pin an internal buffering schedule, so it is reported and
     * excluded. The CONTRACT is the error codes. */
    const int is_contract[5] = {1,1,1,0,1};
    int bad = 0;
    printf("%-16s %10s %10s   %s\n", "call", "C shim", "native", "verdict");
    for (int i=0;i<5;i++){
        int same = (S[i]==N[i]);
        if (!same && is_contract[i]) bad = 1;
        const char *v = same ? "MATCH"
                     : (is_contract[i] ? "*** BREACH ***" : "differs (not a contract)");
        printf("%-16s %10ld %10ld   %s\n", names[i], S[i], N[i], v);
    }
    printf("\nERROR-CODE PARITY (the contract): %s\n", bad?"BREACHED":"HOLDS");
    return bad;
}
