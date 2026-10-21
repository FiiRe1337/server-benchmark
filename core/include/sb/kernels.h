/* Rechenkerne für Memory- und CPU-Tests (docs/core.md, Abschnitt M2). */
#ifndef SB_KERNELS_H
#define SB_KERNELS_H
#include <stddef.h>
#include <stdint.h>
#include "sb/topo.h"

/* Speicher: p/d/s 64-Byte-ausgerichtet, bytes Vielfaches von 256. nt = Non-temporal Stores. */
typedef uint64_t (*sb_mk_read_fn)(const void *p, size_t bytes);
typedef void     (*sb_mk_write_fn)(void *p, size_t bytes, int nt);
typedef void     (*sb_mk_copy_fn)(void *d, const void *s, size_t bytes, int nt);
typedef struct sb_memkern { sb_mk_read_fn read; sb_mk_write_fn write; sb_mk_copy_fn copy; } sb_memkern;
/* 0 = ok, sonst ISA nicht verfügbar */
int sb_memkern_get(sb_simd isa, sb_memkern *k);

/* Pointer-Chasing: Puffer mit Zeigerkette (sb_chase_build), n Sprünge ab *pos */
void     sb_chase_build(void *buf, size_t bytes, size_t line, uint64_t seed);
uint64_t sb_chase(void **pos, uint64_t n);

/* CPU-Lasten: n Operationen, state = 8 x uint64_t pro Thread (Eingabe/Ausgabe) */
typedef enum { SB_CPU_INT, SB_CPU_FLOAT, SB_CPU_CRYPTO, SB_CPU_MIXED } sb_cpu_workload;
const char *sb_cpu_workload_name(sb_cpu_workload w);
void sb_cpu_kernel(sb_cpu_workload w, uint64_t state[8], uint64_t n);

#endif
