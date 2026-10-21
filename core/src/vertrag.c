/* Vertrag 1.3: siehe sb/vertrag.h. Reine Funktionen ohne Systemaufrufe (Unit-Tests). */
#include <string.h>
#include "sb/vertrag.h"
#include "sb/compute.h"

const char *sb_stop_phase_name(int p) {
    switch (p) {
    case SB_STOP_PREPARING: return "preparing";
    case SB_STOP_WARMUP: return "warmup";
    case SB_STOP_MEASURE: return "measure";
    default: return NULL;
    }
}

const char *sb_metric_name(int kind) {
    switch (kind) {
    case SB_CK_CPU: return "ops_per_s";
    case SB_CK_MEM_BW: return "gbps";
    case SB_CK_MEM_LAT: return "latency_ns";
    default: return "iops";
    }
}

double sb_cpu_score(sb_cpu_workload w, double ops_per_s) {
    double ref = sb_cpu_reference(w);
    return ref > 0 ? ops_per_s / ref * 1000.0 : 0.0;
}

cJSON *sb_cpu_score_json(void) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "formula", "score = ops_per_s / ref_ops_per_s[workload] * 1000");
    cJSON_AddNumberToObject(o, "points", 1000);
    cJSON_AddNumberToObject(o, "reference_threads", 1);
    cJSON_AddStringToObject(o, "reference_system", "GitHub-Runner windows-2022/windows-2025, AMD EPYC 7763, 1 Thread");
    cJSON *r = cJSON_AddObjectToObject(o, "ref_ops_per_s");
    for (int w = SB_CPU_INT; w <= SB_CPU_MIXED; w++)
        cJSON_AddNumberToObject(r, sb_cpu_workload_name((sb_cpu_workload)w), sb_cpu_reference((sb_cpu_workload)w));
    return o;
}

cJSON *sb_phase_cpus_json(const sb_topo *t, const int *pick, int n) {
    cJSON *a = cJSON_CreateArray();
    for (int i = 0; i < n; i++) {
        const sb_cpu *c = &t->cpus[pick[i]];
        cJSON *x = cJSON_CreateObject();
        cJSON_AddNumberToObject(x, "id", i);
        cJSON_AddNumberToObject(x, "cpu", c->index);
        cJSON_AddNumberToObject(x, "group", c->group);
        cJSON_AddNumberToObject(x, "number", c->bit);
        cJSON_AddNumberToObject(x, "numa_node", c->node);
        cJSON_AddNumberToObject(x, "core", c->core);
        int sib = -1;
        if (c->smt > 0)
            for (int k = 0; k < t->ncpus; k++)
                if (t->cpus[k].core == c->core && t->cpus[k].smt == 0) { sib = (int)t->cpus[k].index; break; }
        if (sib >= 0) cJSON_AddNumberToObject(x, "smt_sibling_of", sib);
        else cJSON_AddNullToObject(x, "smt_sibling_of");
        cJSON_AddItemToArray(a, x);
    }
    return a;
}

uint64_t sb_mem_buffer_bytes(int latency, uint64_t buffer_size) {
    uint64_t b = latency ? buffer_size / 64 * 64 : buffer_size / 8192 * 8192;   /* copy: zwei gleich große Hälften */
    return b < 8192 ? 8192 : b;
}
