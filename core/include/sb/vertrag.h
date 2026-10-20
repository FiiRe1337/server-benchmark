/* Vertrag 1.3: gemeinsame Bausteine für Progress-Events und Ergebnis (docs/cli.md,
 * Abschnitt "Vertragsstand 1.3"): Name der Hauptkennzahl (tick.metric), CPU-Score und
 * Referenzwerte (meta.cpu_score), CPU-Liste im phase-Event (SMT-Geschwister), Phase
 * eines Teilergebnisses (stopped_in) und die tatsächliche Puffergröße bei memory. */
#ifndef SB_VERTRAG_H
#define SB_VERTRAG_H
#include <stdint.h>
#include <stddef.h>
#include "cJSON.h"
#include "sb/topo.h"
#include "sb/kernels.h"

/* Phase, in der ein Test vorzeitig endete (Stop oder Laufzeitfehler); 0 = vollständig */
enum { SB_STOP_NONE = 0, SB_STOP_PREPARING = 1, SB_STOP_WARMUP = 2, SB_STOP_MEASURE = 3 };
const char *sb_stop_phase_name(int stop_phase);   /* "preparing" | "warmup" | "measure" | NULL */

/* tick.metric: kind = sb_compute_kind (0 cpu, 1 memory bandwidth, 2 memory latency), -1 = storage */
const char *sb_metric_name(int kind);

/* score = ops_per_s / sb_cpu_reference(w) * 1000 */
double sb_cpu_score(sb_cpu_workload w, double ops_per_s);
/* meta.cpu_score: {formula, points, reference_threads, reference_system, ref_ops_per_s{int,float,crypto,mixed}} */
cJSON *sb_cpu_score_json(void);

/* phase.cpus: je Thread (Reihenfolge = Thread-id) {id, cpu, group, number, numa_node, core,
 * smt_sibling_of}. pick[i] = Index in t->cpus (sb_topo_pick). smt_sibling_of = globaler Index
 * des ersten logischen Prozessors desselben Kerns, null für diesen selbst. */
cJSON *sb_phase_cpus_json(const sb_topo *t, const int *pick, int n);

/* memory: tatsächliche Puffergröße pro Thread (bandwidth: Vielfaches von 8 KiB, latency: von
 * 64 Byte, mindestens 8 KiB). latency != 0 für mode latency. */
uint64_t sb_mem_buffer_bytes(int latency, uint64_t buffer_size);

#endif
