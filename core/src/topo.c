/* CPU-/NUMA-Topologie, RAM pro Knoten, SIMD-Erkennung (docs/core.md, Abschnitt M2). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sb/util.h"
#include "sb/topo.h"
#if defined(_M_X64) || defined(__x86_64__)
#define SB_X64 1
#include <immintrin.h>
#ifdef _MSC_VER
#include <intrin.h>
#else
#include <cpuid.h>
#endif
#endif

const char *sb_ram_method_name(sb_ram_method m) {
    return m == SB_RAM_SRAT ? "srat" : m == SB_RAM_ESTIMATE ? "estimate" : "total";
}

static int cmp_cpu(const void *a, const void *b) {
    uint32_t x = ((const sb_cpu *)a)->index, y = ((const sb_cpu *)b)->index;
    return x < y ? -1 : x > y;
}
static int cmp_u32(const void *a, const void *b) {
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return x < y ? -1 : x > y;
}

static void *lpi(LOGICAL_PROCESSOR_RELATIONSHIP rel, DWORD *len) {
    *len = 0;
    GetLogicalProcessorInformationEx(rel, NULL, len);
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || !*len) return NULL;
    void *b = malloc(*len);
    if (b && !GetLogicalProcessorInformationEx(rel, (PSYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)b, len)) { free(b); b = NULL; }
    return b;
}

static sb_cpu *find_cpu(sb_topo *t, uint16_t g, int bit) {
    for (int i = 0; i < t->ncpus; i++) if (t->cpus[i].group == g && t->cpus[i].bit == bit) return &t->cpus[i];
    return NULL;
}

int sb_srat_parse(const uint8_t *tab, size_t len, uint32_t *dom, uint64_t *bytes, int max) {
    if (!tab || len < 48 || memcmp(tab, "SRAT", 4)) return -1;
    uint32_t tl;
    memcpy(&tl, tab + 4, 4);
    if (tl < len) len = tl;
    int n = 0;
    size_t off = 48;
    while (off + 2 <= len) {
        uint8_t type = tab[off], l = tab[off + 1];
        if (l < 2 || off + l > len) break;
        if (type == 1 && l >= 40) {      /* Memory Affinity Structure */
            uint32_t pd, flags, bl, bh;
            memcpy(&pd, tab + off + 2, 4);
            memcpy(&bl, tab + off + 16, 4);
            memcpy(&bh, tab + off + 20, 4);
            memcpy(&flags, tab + off + 28, 4);
            uint64_t sz = ((uint64_t)bh << 32) | bl;
            if ((flags & 1) && !(flags & 2) && !(flags & 4) && sz) {
                int k = 0;
                while (k < n && dom[k] != pd) k++;
                if (k == n) {
                    if (n == max) { off += l; continue; }
                    dom[n] = pd; bytes[n] = 0; n++;
                }
                bytes[k] += sz;
            }
        }
        off += l;
    }
    return n;
}

/* RAM pro Knoten aus der SRAT. 1 = übernommen. */
static int ram_from_srat(sb_topo *t) {
    const DWORD acpi = 0x41435049u /* 'ACPI' */, srat = 0x54415253u /* "SRAT" als DWORD */;
    UINT n = GetSystemFirmwareTable(acpi, srat, NULL, 0);
    if (!n) return 0;
    uint8_t *b = (uint8_t *)malloc(n);
    if (!b) return 0;
    int ok = 0;
    if (GetSystemFirmwareTable(acpi, srat, b, n) == n) {
        uint32_t dom[256]; uint64_t by[256];
        int nd = sb_srat_parse(b, n, dom, by, 256);
        uint64_t *per = (uint64_t *)calloc((size_t)t->nnodes, sizeof(uint64_t));
        uint64_t sum = 0;
        int mapped = 1;
        for (int i = 0; per && i < nd; i++) {
            USHORT node = 0xFFFF;
            if (!GetNumaProximityNodeEx(dom[i], &node)) { mapped = 0; break; }
            int k = 0;
            while (k < t->nnodes && t->nodes[k].id != node) k++;
            if (k == t->nnodes) { mapped = 0; break; }
            per[k] += by[i];
            sum += by[i];
        }
        ULONGLONG inst_kb = 0;
        uint64_t inst = GetPhysicallyInstalledSystemMemory(&inst_kb) ? (uint64_t)inst_kb * 1024 : 0;
        uint64_t upper = (inst > t->ram_total ? inst : t->ram_total) + (inst > t->ram_total ? inst : t->ram_total) / 20 + (1ULL << 30);
        /* plausibel: alle Domains zugeordnet, Summe zwischen halbem sichtbaren RAM und installiertem RAM + 5 % */
        if (per && nd > 0 && mapped && sum >= t->ram_total / 2 && sum <= upper) {
            for (int k = 0; k < t->nnodes; k++) {
                t->nodes[k].ram_bytes = per[k] > t->nodes[k].ram_free_bytes ? per[k] : t->nodes[k].ram_free_bytes;
                t->nodes[k].ram_method = SB_RAM_SRAT;
            }
            ok = 1;
        }
        free(per);
    }
    free(b);
    return ok;
}

static int add_node(sb_topo *t, int id) {
    for (int k = 0; k < t->nnodes; k++) if (t->nodes[k].id == id) return k;
    sb_node *nn = (sb_node *)realloc(t->nodes, sizeof(sb_node) * (size_t)(t->nnodes + 1));
    if (!nn) return -1;
    t->nodes = nn;
    memset(&t->nodes[t->nnodes], 0, sizeof(sb_node));
    t->nodes[t->nnodes].id = id;
    return t->nnodes++;
}

static int cmp_node(const void *a, const void *b) { return ((const sb_node *)a)->id - ((const sb_node *)b)->id; }

int sb_topo_get(sb_topo *t) {
    memset(t, 0, sizeof *t);
    MEMORYSTATUSEX ms;
    ms.dwLength = sizeof ms;
    if (GlobalMemoryStatusEx(&ms)) t->ram_total = ms.ullTotalPhys;
    DWORD len;
    /* 1. Gruppen -> logische Prozessoren */
    uint8_t *g = (uint8_t *)lpi(RelationGroup, &len);
    if (g) {
        PSYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX e = (PSYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)g;
        WORD ng = e->Group.ActiveGroupCount;
        int total = 0;
        for (WORD i = 0; i < ng; i++) total += e->Group.GroupInfo[i].ActiveProcessorCount;
        t->cpus = (sb_cpu *)calloc((size_t)(total ? total : 1), sizeof(sb_cpu));
        for (WORD i = 0; t->cpus && i < ng; i++) {
            KAFFINITY m = e->Group.GroupInfo[i].ActiveProcessorMask;
            for (int b = 0; b < 64; b++)
                if (((uint64_t)m >> b) & 1 && t->ncpus < total) {
                    sb_cpu *c = &t->cpus[t->ncpus++];
                    c->group = i; c->bit = (uint8_t)b; c->index = (uint32_t)i * 64 + (uint32_t)b;
                    c->node = -1; c->core = -1;
                }
        }
        free(g);
    }
    if (!t->ncpus) {   /* Rückfall: eine Gruppe */
        DWORD n = GetActiveProcessorCount(0);
        t->cpus = (sb_cpu *)calloc(n ? n : 1, sizeof(sb_cpu));
        if (!t->cpus) return 1;
        for (DWORD b = 0; b < n && b < 64; b++) {
            sb_cpu *c = &t->cpus[t->ncpus++];
            c->bit = (uint8_t)b; c->index = b; c->node = -1; c->core = -1;
        }
    }
    qsort(t->cpus, (size_t)t->ncpus, sizeof(sb_cpu), cmp_cpu);
    /* 2. Kerne (SMT-Geschwister) */
    uint8_t *cb = (uint8_t *)lpi(RelationProcessorCore, &len);
    if (cb) {
        for (DWORD off = 0; off < len;) {
            PSYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX e = (PSYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)(cb + off);
            if (!e->Size) break;
            int smt = 0;
            for (WORD gi = 0; gi < e->Processor.GroupCount; gi++) {
                GROUP_AFFINITY *ga = &e->Processor.GroupMask[gi];
                for (int b = 0; b < 64; b++)
                    if (((uint64_t)ga->Mask >> b) & 1) {
                        sb_cpu *c = find_cpu(t, ga->Group, b);
                        if (c) { c->core = t->ncores; c->smt = smt++; }
                    }
            }
            t->ncores++;
            off += e->Size;
        }
        free(cb);
    }
    for (int i = 0; i < t->ncpus; i++) if (t->cpus[i].core < 0) t->cpus[i].core = t->ncores++;
    /* 3. NUMA-Knoten: RelationNumaNodeEx (Win11/Server 2022: Knoten über mehrere Gruppen),
     *    sonst RelationNumaNode. Rohes Layout: NodeNumber(4) Reserved(18) GroupCount(2) GroupMasks[] */
    uint8_t *nb = (uint8_t *)lpi((LOGICAL_PROCESSOR_RELATIONSHIP)6 /* RelationNumaNodeEx */, &len);
    if (!nb) nb = (uint8_t *)lpi(RelationNumaNode, &len);
    if (nb) {
        for (DWORD off = 0; off + 8 <= len;) {
            DWORD rel, size;
            memcpy(&rel, nb + off, 4);
            memcpy(&size, nb + off + 4, 4);
            if (size < 8 + 24 + 16 || off + size > len) break;
            const uint8_t *p = nb + off + 8;
            DWORD node; WORD gc;
            memcpy(&node, p, 4);
            memcpy(&gc, p + 22, 2);
            if (!gc) gc = 1;
            int k = add_node(t, (int)node);
            for (WORD gi = 0; k >= 0 && gi < gc && 24 + (gi + 1) * 16u <= size - 8; gi++) {
                uint64_t mask; WORD grp;
                memcpy(&mask, p + 24 + gi * 16, 8);
                memcpy(&grp, p + 24 + gi * 16 + 8, 2);
                for (int b = 0; b < 64; b++)
                    if ((mask >> b) & 1) { sb_cpu *c = find_cpu(t, grp, b); if (c) c->node = (int)node; }
            }
            off += size;
        }
        free(nb);
    }
    if (!t->nnodes) add_node(t, 0);
    for (int i = 0; i < t->ncpus; i++) if (t->cpus[i].node < 0) t->cpus[i].node = t->nodes[0].id;
    qsort(t->nodes, (size_t)t->nnodes, sizeof(sb_node), cmp_node);
    /* CPU-Listen und freier Speicher */
    for (int k = 0; k < t->nnodes; k++) {
        sb_node *n = &t->nodes[k];
        int c = 0;
        for (int i = 0; i < t->ncpus; i++) if (t->cpus[i].node == n->id) c++;
        n->cpus = (uint32_t *)calloc((size_t)(c ? c : 1), sizeof(uint32_t));
        for (int i = 0; n->cpus && i < t->ncpus; i++) if (t->cpus[i].node == n->id) n->cpus[n->ncpus++] = t->cpus[i].index;
        qsort(n->cpus, (size_t)n->ncpus, sizeof(uint32_t), cmp_u32);
        ULONGLONG fr = 0;
        if (GetNumaAvailableMemoryNodeEx((USHORT)n->id, &fr)) n->ram_free_bytes = fr;
    }
    /* 4. RAM pro Knoten (Issue #8) */
    if (t->nnodes == 1) {
        t->nodes[0].ram_bytes = t->ram_total;
        t->nodes[0].ram_method = SB_RAM_TOTAL;
    } else if (!ram_from_srat(t)) {
        /* Näherung: Gesamt-RAM im Verhältnis des freien Speichers, mindestens frei */
        uint64_t fsum = 0;
        for (int k = 0; k < t->nnodes; k++) fsum += t->nodes[k].ram_free_bytes;
        for (int k = 0; k < t->nnodes; k++) {
            sb_node *n = &t->nodes[k];
            uint64_t v = fsum ? (uint64_t)((double)t->ram_total * ((double)n->ram_free_bytes / (double)fsum))
                              : t->ram_total / (uint64_t)t->nnodes;
            n->ram_bytes = v > n->ram_free_bytes ? v : n->ram_free_bytes;
            n->ram_method = SB_RAM_ESTIMATE;
        }
    }
    return 0;
}

void sb_topo_free(sb_topo *t) {
    for (int k = 0; k < t->nnodes; k++) free(t->nodes[k].cpus);
    free(t->nodes);
    free(t->cpus);
    memset(t, 0, sizeof *t);
}

const sb_node *sb_topo_node(const sb_topo *t, int id) {
    for (int k = 0; k < t->nnodes; k++) if (t->nodes[k].id == id) return &t->nodes[k];
    return NULL;
}

int sb_topo_pick(const sb_topo *t, int node, int n, int *out) {
    int *ord = (int *)malloc(sizeof(int) * (size_t)(t->ncpus ? t->ncpus : 1));
    if (!ord) return 0;
    int no = 0, maxsmt = 0;
    for (int i = 0; i < t->ncpus; i++) if (t->cpus[i].smt > maxsmt) maxsmt = t->cpus[i].smt;
    /* je SMT-Ebene: Knoten reihum, innerhalb des Knotens aufsteigend */
    for (int s = 0; s <= maxsmt; s++) {
        int *pos = (int *)calloc((size_t)(t->nnodes ? t->nnodes : 1), sizeof(int));
        if (!pos) break;
        for (int more = 1; more;) {
            more = 0;
            for (int k = 0; k < t->nnodes; k++) {
                if (node >= 0 && t->nodes[k].id != node) continue;
                for (int i = pos[k]; i < t->ncpus; i++) {
                    pos[k] = i + 1;
                    if (t->cpus[i].node == t->nodes[k].id && t->cpus[i].smt == s) { ord[no++] = i; more = 1; break; }
                }
            }
        }
        free(pos);
    }
    for (int i = 0; i < n && no; i++) out[i] = ord[i % no];
    free(ord);
    return no;
}

int sb_topo_pin_thread(void *h, const sb_cpu *c) {
    GROUP_AFFINITY ga;
    memset(&ga, 0, sizeof ga);
    ga.Group = c->group;
    ga.Mask = (KAFFINITY)1 << c->bit;
    if (!SetThreadGroupAffinity((HANDLE)h, &ga, NULL)) return 1;
    PROCESSOR_NUMBER pn;
    memset(&pn, 0, sizeof pn);
    pn.Group = c->group; pn.Number = c->bit;
    SetThreadIdealProcessorEx((HANDLE)h, &pn, NULL);
    return 0;
}

/* ---- SIMD ----------------------------------------------------------------- */

const char *sb_simd_name(sb_simd s) {
    static const char *const N[] = {"auto", "scalar", "sse2", "avx2", "avx512", "neon"};
    return (unsigned)s < 6 ? N[s] : "?";
}

#ifdef SB_X64
static void cpuidex(int leaf, int sub, unsigned r[4]) {
#ifdef _MSC_VER
    int v[4];
    __cpuidex(v, leaf, sub);
    for (int i = 0; i < 4; i++) r[i] = (unsigned)v[i];
#else
    __cpuid_count((unsigned)leaf, (unsigned)sub, r[0], r[1], r[2], r[3]);
#endif
}
static uint64_t xgetbv0(void) {
#ifdef _MSC_VER
    return _xgetbv(0);
#else
    uint32_t lo, hi;
    __asm__ volatile("xgetbv" : "=a"(lo), "=d"(hi) : "c"(0));
    return ((uint64_t)hi << 32) | lo;
#endif
}
#endif

int sb_simd_supported(sb_simd s) {
    switch (s) {
    case SB_SIMD_AUTO: case SB_SIMD_SCALAR: return 1;
#ifdef SB_X64
    case SB_SIMD_SSE2: return 1;
    case SB_SIMD_AVX2:
    case SB_SIMD_AVX512: {
        unsigned r1[4], r7[4], r0[4];
        cpuidex(0, 0, r0);
        if (r0[0] < 7) return 0;
        cpuidex(1, 0, r1);
        if (!((r1[2] >> 27) & 1) || !((r1[2] >> 28) & 1)) return 0;   /* OSXSAVE, AVX */
        uint64_t x = xgetbv0();
        if ((x & 6) != 6) return 0;                                    /* XMM + YMM */
        cpuidex(7, 0, r7);
        if (s == SB_SIMD_AVX2) return (r7[1] >> 5) & 1;
        return ((x & 0xE6) == 0xE6) && ((r7[1] >> 16) & 1);           /* opmask/ZMM + AVX512F */
    }
    case SB_SIMD_NEON: return 0;
#else
    case SB_SIMD_NEON: return 1;
    default: return 0;
#endif
    }
    return 0;
}

sb_simd sb_simd_auto(void) {
#ifdef SB_X64
    return sb_simd_supported(SB_SIMD_AVX2) ? SB_SIMD_AVX2 : SB_SIMD_SSE2;
#else
    return SB_SIMD_NEON;
#endif
}

int sb_nontemporal_supported(void) {
#ifdef SB_X64
    return 1;
#else
    return 0;
#endif
}
