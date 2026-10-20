/* CPU- und NUMA-Topologie (Prozessorgruppen, Kerne, Knoten), RAM pro Knoten (SRAT),
 * SIMD-Erkennung. Globaler CPU-Index = Prozessorgruppe * 64 + Bitnummer (Vertrag). */
#ifndef SB_TOPO_H
#define SB_TOPO_H
#include <stdint.h>
#include <stddef.h>

typedef struct sb_cpu {
    uint32_t index;        /* global: group * 64 + bit */
    uint16_t group;
    uint8_t  bit;
    int      node;         /* NUMA-Knoten, -1 = unbekannt */
    int      core;         /* laufende Nummer des physischen Kerns */
    int      smt;          /* 0 = erster logischer Prozessor des Kerns, 1.. = SMT-Geschwister */
} sb_cpu;

typedef enum { SB_RAM_TOTAL, SB_RAM_SRAT, SB_RAM_ESTIMATE } sb_ram_method;

typedef struct sb_node {
    int       id;
    uint32_t *cpus; int ncpus;        /* globale Indizes, aufsteigend */
    uint64_t  ram_bytes, ram_free_bytes;
    sb_ram_method ram_method;
} sb_node;

typedef struct sb_topo {
    sb_cpu  *cpus; int ncpus;         /* alle aktiven logischen Prozessoren, nach Index sortiert */
    sb_node *nodes; int nnodes;       /* Knoten mit CPUs oder Speicher, nach id sortiert */
    int      ncores;
    uint64_t ram_total;               /* GlobalMemoryStatusEx.ullTotalPhys */
} sb_topo;

/* 0 = ok. Mit sb_topo_free freigeben. */
int  sb_topo_get(sb_topo *t);
void sb_topo_free(sb_topo *t);
const sb_node *sb_topo_node(const sb_topo *t, int id);
const char *sb_ram_method_name(sb_ram_method m);

/* Reihenfolge der CPUs für n Threads: zuerst je ein logischer Prozessor pro physischem Kern,
 * abwechselnd über die Knoten (bzw. nur Knoten node >= 0), danach SMT-Geschwister; bei mehr
 * Threads als CPUs wiederholt sich die Liste. out[n] erhält Indizes in t->cpus. Rückgabe: Anzahl
 * verfügbarer CPUs (0 = Knoten ohne CPUs). */
int sb_topo_pick(const sb_topo *t, int node, int n, int *out);

/* Thread an einen logischen Prozessor binden (SetThreadGroupAffinity). 0 = ok. */
int sb_topo_pin_thread(void *thread_handle, const sb_cpu *c);

/* SRAT (ACPI System Resource Affinity Table): Summe der Speicherbereiche je
 * Proximity Domain (nur enabled, ohne hot-pluggable und non-volatile).
 * dom/bytes: Ausgabe-Arrays mit max Einträgen. Rückgabe: Anzahl Domains, -1 = kein SRAT. */
int sb_srat_parse(const uint8_t *tab, size_t len, uint32_t *dom, uint64_t *bytes, int max);

/* SIMD */
typedef enum { SB_SIMD_AUTO, SB_SIMD_SCALAR, SB_SIMD_SSE2, SB_SIMD_AVX2, SB_SIMD_AVX512, SB_SIMD_NEON } sb_simd;
const char *sb_simd_name(sb_simd s);
int  sb_simd_supported(sb_simd s);    /* CPU + Betriebssystem (XSAVE-Zustände) */
sb_simd sb_simd_auto(void);           /* x64: avx2, sonst sse2; ARM64: neon */
int  sb_nontemporal_supported(void);  /* x64: ja (MOVNT*), ARM64: nein */

#endif
