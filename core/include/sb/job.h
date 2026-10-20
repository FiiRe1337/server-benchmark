/* Internes Modell eines Jobs (sb-job/1) nach dem Parsen. */
#ifndef SB_JOB_H
#define SB_JOB_H
#include <stdint.h>
#include "cJSON.h"

#define SB_MAX_SPECS   64
#define SB_MAX_TARGETS 64

typedef enum { SB_T_STORAGE, SB_T_CPU, SB_T_MEMORY, SB_T_NET_CLIENT, SB_T_NET_SERVER, SB_T_HYPERV_INFO, SB_T_UNKNOWN } sb_test_type;
typedef enum { SB_TGT_FILE, SB_TGT_VOLUME, SB_TGT_PHYSICAL } sb_target_type;
typedef enum { SB_CACHE_UNBUFFERED, SB_CACHE_BUFFERED, SB_CACHE_WRITETHROUGH } sb_cache_mode;

typedef struct sb_target {
    sb_target_type type;
    char    *path;          /* Datei, \\.\D: oder \\.\PhysicalDriveN */
    uint64_t file_size;     /* nur file: Größe der Testdatei */
    int      allow_destructive; /* volume/physical: Schreiben freigegeben (zusätzlich --allow-destructive) */
} sb_target;

typedef struct sb_spec {
    const char *name;       /* optional, zeigt in den Job-Baum */
    uint32_t weight;        /* Summe 100 */
    uint32_t read_pct;      /* 0..100 */
    uint32_t random_pct;    /* 0..100 */
    uint64_t block_size;
    uint64_t align;         /* 0 = block_size */
    uint32_t burst_len;     /* 0/1 = kein Burst */
    uint32_t delay_ms;      /* Pause vor jedem Burst */
} sb_spec;

typedef struct sb_test {
    sb_test_type  type;
    char         *type_str;
    const char   *name;       /* optional, zeigt in den Job-Baum */
    cJSON        *raw;        /* Referenz in den Job-Baum, nicht freigeben */
    /* storage */
    sb_target     targets[SB_MAX_TARGETS];
    int           ntargets;
    sb_spec       specs[SB_MAX_SPECS];
    int           nspecs;
    uint32_t      workers;
    uint32_t      queue_depth;
    double        warmup_s;
    double        duration_s;
    sb_cache_mode cache;
    uint64_t      seed;       /* fest: 0x5be7c0de00000000 ^ Testindex (Schema hat kein seed-Feld) */
    /* cpu / memory (M2) */
    uint32_t      threads;    /* 0 = alle logischen Prozessoren (bzw. des Knotens numa_node) */
    int           workload;   /* cpu: sb_cpu_workload */
    int           mem_mode;   /* 0 = bandwidth, 1 = latency */
    int           mem_op;     /* 0 = read, 1 = write, 2 = copy */
    uint64_t      buffer_size;/* pro Thread */
    int           numa_node;  /* -1 = alle Knoten */
    int           mem_node;   /* -1 = Knoten des jeweiligen Threads (lokal) */
    int           nontemporal;
    int           simd;       /* sb_simd */
} sb_test;

typedef struct sb_job {
    cJSON   *root;           /* Original-JSON (gehört dem Job) */
    char    *name;
    sb_test *tests;
    int      ntests;
} sb_job;

/* Validierungsfehler mit JSON-Pointer */
typedef struct sb_verr { char pointer[160]; char code[48]; char msg[256]; } sb_verr;
typedef struct sb_verrs { sb_verr *v; int n, cap; } sb_verrs;

void sb_verrs_add(sb_verrs *e, const char *pointer, const char *code, const char *fmt, ...);
void sb_verrs_free(sb_verrs *e);

/* Parst + prüft. Rückgabe 0 = gültig, 2 = ungültig (errs gefüllt), 1 = I/O-/Parserfehler.
 * warns (optional) erhält W_NEEDS_ADMIN, W_DESTRUCTIVE, W_LOW_DISK_SPACE.
 * job->root bleibt auch bei ungültigem Job gesetzt (für Fehlermeldungen). */
int  sb_job_load_text(sb_job *job, const char *text, sb_verrs *errs, sb_verrs *warns);
int  sb_job_load_file(sb_job *job, const char *path, sb_verrs *errs, sb_verrs *warns);
/* Test schreibt (mindestens eine Spec mit read_pct < 100) */
int  sb_test_writes(const sb_test *t);
void sb_job_free(sb_job *job);

/* Gewichtete Spec-Auswahl: kumulierte Gewichte, r in [0, total) */
int  sb_pick_spec(const uint32_t *cum, int n, uint64_t r);

const char *sb_test_type_name(sb_test_type t);
/* Größe nach sb-job/1 $defs/size */
int sb_json_size(const cJSON *c, uint64_t *out);
const char *sb_target_type_name(sb_target_type t);
const char *sb_cache_name(sb_cache_mode c);

#endif
