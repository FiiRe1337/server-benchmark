/* CPU- und Memory-Tests: Threads pro logischem Prozessor (über Prozessorgruppen),
 * NUMA-Platzierung, Phasen preparing/warmup/measure/cleanup, Ticks. */
#ifndef SB_COMPUTE_H
#define SB_COMPUTE_H
#include "sb/storage.h"
#include "sb/topo.h"
#include "sb/kernels.h"

typedef enum { SB_CK_CPU, SB_CK_MEM_BW, SB_CK_MEM_LAT } sb_compute_kind;

/* Tick: value = ops/s (cpu), GB/s (Bandbreite) bzw. ns (Latenz) im Intervall */
typedef struct sb_ctick {
    double  t, value, cpu_pct, proc_cpu_pct;
    double  score;               /* nur cpu (Vertrag 1.3), sonst < 0 */
    int     nworkers;
    double *w_value;
} sb_ctick;

typedef struct sb_compute_result {
    sb_compute_kind kind;
    int       nthreads;
    uint32_t *cpus;              /* globaler CPU-Index pro Thread */
    cJSON    *cpus_json;         /* Vertrag 1.3.1: tests[].cpus (= phase.cpus), NULL ohne Topologie */
    int      *nodes;             /* NUMA-Knoten der CPU pro Thread */
    int      *mem_nodes;         /* Knoten, auf dem der Puffer angefordert wurde (memory) */
    double    value;             /* Gesamtwert der Messphase */
    double   *w_value;           /* pro Thread */
    uint64_t  units, *w_units;   /* Operationen, Bytes bzw. Sprünge in der Messphase */
    double    measured_s, cpu_pct;
    double    proc_cpu_pct;      /* sbench-Prozess, Mittel über die Messphase (Vertrag 1.3) */
    int       stop_phase;        /* SB_STOP_* (sb/vertrag.h), 0 = vollständig */
    double    score;             /* nur cpu */
    sb_simd   simd;              /* tatsächlich genutzt (memory bandwidth) */
    int       nontemporal;       /* tatsächlich genutzt */
    uint64_t  buffer_bytes;      /* pro Thread, nach Rundung */
    sb_ctick *ticks; int nticks, cap_ticks;
    sb_run_error *errs; int nerrs, cap_errs;
    int       aborted, measured;
} sb_compute_result;

/* Rückgabe: 0 ok, 1 Laufzeitfehler, 3 abgebrochen. r immer initialisiert, mit
 * sb_compute_result_free freigeben. */
int  sb_compute_run(int test_index, const sb_test *t, const sb_storage_opts *o, const sb_storage_cb *cb,
                    sb_compute_result *r);
void sb_compute_result_free(sb_compute_result *r);

/* Referenzwerte für den CPU-Score: ops/s eines Threads des Referenzsystems = 1000 Punkte */
double sb_cpu_reference(sb_cpu_workload w);

#endif
