/* Storage-Benchmark: IOCP-Engine, ein Thread + ein Completion Port pro Worker. */
#ifndef SB_STORAGE_H
#define SB_STORAGE_H
#include "sb/job.h"
#include "sb/hist.h"
#include "sb/util.h"

struct sb_ctick;

typedef struct sb_io_counts {
    uint64_t ops_r, ops_w, bytes_r, bytes_w, errors;
} sb_io_counts;

typedef struct sb_tick_sample {
    double   t;                 /* s seit Beginn der Messphase (Intervallende) */
    double   iops, mbps;
    double   read_iops, write_iops, read_mbps, write_mbps;
    double   lat_mean_us, lat_p50_us, lat_p99_us, lat_max_us;
    double   cpu_pct;           /* System */
    double   proc_cpu_pct;      /* nur sbench.exe, bezogen auf alle logischen CPUs */
    int      nworkers;
    double  *w_iops, *w_mbps;   /* pro Worker (nworkers Einträge) */
    double  *w_lat_mean_us, *w_lat_p99_us; /* pro Worker, < 0 = kein I/O im Intervall */
} sb_tick_sample;

typedef struct sb_run_error {
    char     code[40];
    char     msg[300];
    double   t;
    int      worker, target;    /* -1 = keine Angabe */
    uint64_t count;
} sb_run_error;

typedef struct sb_storage_result {
    int          nworkers, nspecs;
    int         *worker_target;     /* Worker -> Target-Index */
    sb_io_counts *w_counts;         /* pro Worker */
    sb_hist    **w_hist;            /* pro Worker, r+w zusammen */
    sb_io_counts *s_counts;         /* pro Spec */
    sb_hist    **s_hist;            /* pro Spec */
    sb_io_counts total;
    sb_hist     *hist;              /* gesamt */
    double       measured_s;        /* tatsächliche Dauer der Messphase */
    double       cpu_pct;           /* Mittel über die Messphase */
    double       proc_cpu_pct;      /* sbench-Prozess, Mittel über die Messphase (Vertrag 1.3) */
    int          stop_phase;        /* SB_STOP_* (sb/vertrag.h): Phase eines vorzeitigen Endes, 0 = vollständig */
    sb_tick_sample *ticks; int nticks, cap_ticks;
    sb_run_error *errs; int nerrs, cap_errs;
    int          aborted;           /* Stop-Event während des Tests */
    int          measured;          /* Messphase hat begonnen */
    int          ntargets;
    int         *t_created;         /* pro Target: von sbench angelegt */
    int         *t_deleted;         /* pro Target: nach dem Test gelöscht */
} sb_storage_result;

/* Callbacks in den Lauf-Kontext (Progress, Log) */
typedef struct sb_storage_cb {
    void *ctx;
    /* duration_s < 0: keine Dauer bekannt. extra: zusätzliche Felder des Events (Vertrag 1.3:
     * threads, cpus beim ersten phase-Event eines Tests) oder NULL; bleibt Eigentum des Aufrufers. */
    void (*phase)(void *ctx, int test, const char *name, double duration_s, const cJSON *extra);
    /* Fortschritt in preparing (unit "bytes") und warmup (unit "s") */
    void (*progress)(void *ctx, int test, const char *phase, double done, double total, const char *unit);
    void (*tick)(void *ctx, int test, const sb_tick_sample *s);
    void (*error)(void *ctx, int test, const char *code, const char *msg);
    void (*log)(void *ctx, const char *level, const char *msg);
    /* cpu/memory (compute.h): kind = sb_compute_kind, s = const sb_ctick * */
    void (*ctick)(void *ctx, int test, int kind, const struct sb_ctick *s);
} sb_storage_cb;

typedef struct sb_storage_opts {
    uint32_t tick_ms;
    HANDLE   stop_event;        /* gesetzt -> Abbruch */
    int      allow_destructive;
    int      keep_files;        /* --keep-files: angelegte Testdateien nicht löschen */
} sb_storage_opts;

/* Rückgabe: 0 ok, 1 Laufzeitfehler, 3 abgebrochen, 4 fehlende Rechte.
 * res wird immer initialisiert und muss mit sb_storage_result_free freigegeben werden. */
int  sb_storage_run(int test_index, const sb_test *t, const sb_storage_opts *o, const sb_storage_cb *cb,
                    sb_storage_result *res);
void sb_storage_result_free(sb_storage_result *r);

/* CPU-Last des Systems zwischen zwei GetSystemTimes-Proben */
typedef struct sb_cpu_sample { uint64_t idle, kernel, user; } sb_cpu_sample;
void   sb_cpu_sample_now(sb_cpu_sample *s);
double sb_cpu_pct(const sb_cpu_sample *a, const sb_cpu_sample *b);
/* CPU-Zeit dieses Prozesses (100-ns-Einheiten, Kernel+User) */
uint64_t sb_proc_cpu_now(void);

#endif
