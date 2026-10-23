/* CPU- und Memory-Tests (Meilenstein 2). Ein Thread pro gewähltem logischen Prozessor,
 * gebunden per SetThreadGroupAffinity (auch über Prozessorgruppen hinweg). Jeder Thread
 * legt seinen Puffer selbst an (VirtualAllocExNuma auf dem Zielknoten, danach vollständig
 * beschrieben = First Touch) und arbeitet in Batches von ca. 1 ms; der Hauptthread liest
 * die Zähler zu Beginn/Ende der Messphase und pro Tick (QPC). Details: docs/core.md. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <malloc.h>
#include "sb/compute.h"
#include "sb/kernels.h"
#include "sb/topo.h"
#include "sb/vertrag.h"

#define BLK 4096u

/* Referenz für den CPU-Score: ops/s eines Threads auf dem Referenzsystem (GitHub-Runner
 * windows-2022/-2025 mit AMD EPYC 7763, Mittel der M2-Smoke-Läufe; docs/core.md).
 * score = ops_per_s / ref * 1000. Werte gelten ab Vertrag 1.2 unverändert. */
double sb_cpu_reference(sb_cpu_workload w) {
    switch (w) {
    case SB_CPU_INT: return 2.8e8;
    case SB_CPU_FLOAT: return 1.5e8;
    case SB_CPU_CRYPTO: return 3.4e6;
    default: return 3.8e5;
    }
}

typedef struct cworker {
    volatile uint64_t units;          /* eigene Cache-Zeile: nur dieser Thread schreibt */
    char      pad0[56];
    volatile uint64_t prep_bytes;
    struct cengine *e;
    HANDLE    th;
    int       id;
    const sb_cpu *cpu;
    int       mem_node;
    uint64_t  state[8];
    char     *buf;
    size_t    bytes;
    void     *chase;
    int       err;
    char      errmsg[256];
    char      pad1[64];
} cworker;

typedef struct cengine {
    const sb_test   *t;
    sb_compute_kind  kind;
    sb_memkern       mk;
    int              nt;
    volatile LONG    phase;            /* 0 = vorbereiten, 1 = laufen, 2 = stopp */
    volatile LONG    ready;
    HANDLE           start_evt;
    cworker         *w;
    int              nw;
} cengine;

static void wlog(cworker *w, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(w->errmsg, sizeof w->errmsg, fmt, ap);
    va_end(ap);
}

static int prepare(cworker *w) {
    cengine *e = w->e;
    const sb_test *t = e->t;
    for (int i = 0; i < 8; i++) w->state[i] = t->seed ^ ((uint64_t)w->id * 0x9E3779B97F4A7C15ULL + (uint64_t)i);
    if (e->kind == SB_CK_CPU) return 0;
    size_t bytes = (size_t)sb_mem_buffer_bytes(e->kind == SB_CK_MEM_LAT, t->buffer_size);   /* = total von preparing */
    w->bytes = bytes;
    SIZE_T sz = (SIZE_T)bytes;
    w->buf = (char *)VirtualAllocExNuma(GetCurrentProcess(), NULL, sz, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE,
                                         (DWORD)w->mem_node);
    if (!w->buf) {
        w->err = 1;
        wlog(w, "Thread %d: %llu Bytes auf NUMA-Knoten %d nicht anforderbar: %s", w->id, (unsigned long long)bytes,
             w->mem_node, sb_errtext(GetLastError()));
        return 1;
    }
    /* First Touch auf dem gebundenen Thread: physische Seiten vom Zielknoten */
    for (size_t off = 0; off < bytes && e->phase != 2; off += (size_t)1 << 20) {
        size_t n = bytes - off < ((size_t)1 << 20) ? bytes - off : ((size_t)1 << 20);
        memset(w->buf + off, (int)(0x5b ^ w->id), n);
        w->prep_bytes = off + n;
    }
    if (e->kind == SB_CK_MEM_LAT) {
        sb_chase_build(w->buf, bytes, 64, t->seed ^ 0xC4A5E);
        w->chase = w->buf;
    }
    return 0;
}

static DWORD WINAPI worker_main(LPVOID arg) {
    cworker *w = (cworker *)arg;
    cengine *e = w->e;
    if (prepare(w)) { InterlockedIncrement(&e->ready); return 1; }
    InterlockedIncrement(&e->ready);
    WaitForSingleObject(e->start_evt, INFINITE);
    uint64_t qpf = sb_qpf(), lo = qpf / 2000, hi = qpf / 500;   /* Ziel: Batch 0,5–2 ms */
    uint64_t batch = e->kind == SB_CK_CPU ? 256 : e->kind == SB_CK_MEM_LAT ? 1024 : 16;
    uint64_t local = 0;
    size_t pos = 0, half = w->bytes / 2;
    sb_cpu_workload wl = (sb_cpu_workload)e->t->workload;
    int op = e->t->mem_op;
    while (e->phase == 1) {
        uint64_t t0 = sb_qpc();
        uint64_t done = 0;
        switch (e->kind) {
        case SB_CK_CPU:
            sb_cpu_kernel(wl, w->state, batch);
            done = batch;
            break;
        case SB_CK_MEM_LAT:
            done = sb_chase(&w->chase, batch);
            break;
        case SB_CK_MEM_BW: {
            size_t want = (size_t)batch * BLK;
            while (want) {
                if (op == 2) {
                    size_t n = half - pos < want ? half - pos : want;
                    e->mk.copy(w->buf + half + pos, w->buf + pos, n, e->nt);
                    done += 2 * (uint64_t)n;   /* gelesen + geschrieben (STREAM-Zählweise) */
                    pos += n; want -= n;
                    if (pos >= half) pos = 0;
                } else {
                    size_t n = w->bytes - pos < want ? w->bytes - pos : want;
                    if (op == 0) w->state[0] ^= e->mk.read(w->buf + pos, n);
                    else e->mk.write(w->buf + pos, n, e->nt);
                    done += n;
                    pos += n; want -= n;
                    if (pos >= w->bytes) pos = 0;
                }
            }
            break;
        }
        }
        local += done;
        w->units = local;
        uint64_t dt = sb_qpc() - t0;
        if (dt < lo && batch < (1ULL << 40)) batch *= 2;
        else if (dt > hi && batch > 1) batch /= 2;
    }
    return 0;
}

/* ---- Ergebnis -------------------------------------------------------------- */

static void add_err(sb_compute_result *r, const char *code, int worker, double t, const char *fmt, ...) {
    if (r->nerrs == r->cap_errs) {
        int nc = r->cap_errs ? r->cap_errs * 2 : 8;
        sb_run_error *ne = (sb_run_error *)realloc(r->errs, sizeof(sb_run_error) * (size_t)nc);
        if (!ne) return;
        r->errs = ne; r->cap_errs = nc;
    }
    sb_run_error *x = &r->errs[r->nerrs++];
    memset(x, 0, sizeof *x);
    /* Vertrag 1.3.1: jeder add_err bei cpu/memory beendet den Test; fatal trägt der erste */
    x->fatal = r->nerrs == 1;
    snprintf(x->code, sizeof x->code, "%s", code);
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(x->msg, sizeof x->msg, fmt, ap);
    va_end(ap);
    x->t = t; x->worker = worker; x->target = -1; x->count = 1;
}

void sb_compute_result_free(sb_compute_result *r) {
    cJSON_Delete(r->cpus_json);
    free(r->cpus); free(r->nodes); free(r->mem_nodes); free(r->w_value); free(r->w_units);
    for (int i = 0; i < r->nticks; i++) free(r->ticks[i].w_value);
    free(r->ticks); free(r->errs);
    memset(r, 0, sizeof *r);
}

static void logf_(const sb_storage_cb *cb, const char *lvl, const char *fmt, ...) {
    if (!cb->log) return;
    char m[600];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(m, sizeof m, fmt, ap);
    va_end(ap);
    cb->log(cb->ctx, lvl, m);
}

/* units/dt -> Kennzahl */
static double to_value(sb_compute_kind k, uint64_t units, double dt) {
    if (dt <= 0) return 0;
    if (k == SB_CK_CPU) return (double)units / dt;
    if (k == SB_CK_MEM_BW) return (double)units / dt / 1e9;
    return units ? dt * 1e9 / (double)units : 0;   /* ns pro Sprung */
}

static int wait_until(HANDLE stop, uint64_t deadline) {
    for (;;) {
        uint64_t now = sb_qpc();
        if (now >= deadline) return 0;
        double ms = (double)(deadline - now) * 1000.0 / (double)sb_qpf();
        DWORD d = (DWORD)(ms + 0.999);
        if (d > 250) d = 250;
        if (stop && WaitForSingleObject(stop, d) == WAIT_OBJECT_0) return 1;
        if (!stop) Sleep(d);
    }
}

int sb_compute_run(int ti, const sb_test *t, const sb_storage_opts *o, const sb_storage_cb *cb, sb_compute_result *r) {
    memset(r, 0, sizeof *r);
    cengine E;
    memset(&E, 0, sizeof E);
    E.t = t;
    E.kind = t->type == SB_T_CPU ? SB_CK_CPU : t->mem_mode == 1 ? SB_CK_MEM_LAT : SB_CK_MEM_BW;
    r->kind = E.kind;
    r->simd = SB_SIMD_SCALAR;
    r->stop_phase = SB_STOP_PREPARING;   /* Fehler vor der Messung: Teilergebnis aus preparing */
    if (E.kind == SB_CK_MEM_BW) {
        sb_simd isa = t->simd == SB_SIMD_AUTO ? sb_simd_auto() : (sb_simd)t->simd;
        if (sb_memkern_get(isa, &E.mk)) {
            add_err(r, "E_SIMD_UNSUPPORTED", -1, 0, "SIMD '%s' wird von CPU/Betriebssystem nicht unterstützt.", sb_simd_name(isa));
            if (cb->error) cb->error(cb->ctx, ti, "E_SIMD_UNSUPPORTED", r->errs[r->nerrs - 1].msg);
            return 1;
        }
        r->simd = isa;
        E.nt = t->nontemporal && t->mem_op != 0 && sb_nontemporal_supported();
        r->nontemporal = E.nt;
    }
    sb_topo topo;
    if (sb_topo_get(&topo) || !topo.ncpus) {
        add_err(r, "E_INTERNAL", -1, 0, "CPU-Topologie nicht lesbar.");
        return 1;
    }
    int avail_probe = 0;
    int nthreads = E.kind == SB_CK_MEM_LAT ? 1 : (int)t->threads;
    if (nthreads <= 0) {
        for (int i = 0; i < topo.ncpus; i++) if (t->numa_node < 0 || topo.cpus[i].node == t->numa_node) avail_probe++;
        nthreads = avail_probe;
    }
    int *pick = (int *)calloc((size_t)(nthreads > 0 ? nthreads : 1), sizeof(int));
    int avail = pick ? sb_topo_pick(&topo, t->numa_node, nthreads, pick) : 0;
    if (!avail || nthreads <= 0) {
        add_err(r, "E_NUMA_NODE", -1, 0, "NUMA-Knoten %d hat keine logischen Prozessoren.", t->numa_node);
        if (cb->error) cb->error(cb->ctx, ti, "E_NUMA_NODE", r->errs[r->nerrs - 1].msg);
        free(pick); sb_topo_free(&topo);
        return 1;
    }
    r->nthreads = nthreads;
    r->cpus = (uint32_t *)calloc((size_t)nthreads, sizeof(uint32_t));
    r->nodes = (int *)calloc((size_t)nthreads, sizeof(int));
    r->mem_nodes = (int *)calloc((size_t)nthreads, sizeof(int));
    r->w_value = (double *)calloc((size_t)nthreads, sizeof(double));
    r->w_units = (uint64_t *)calloc((size_t)nthreads, sizeof(uint64_t));
    E.w = (cworker *)_aligned_malloc(sizeof(cworker) * (size_t)nthreads, 64);
    E.start_evt = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!r->cpus || !r->nodes || !r->mem_nodes || !r->w_value || !r->w_units || !E.w || !E.start_evt) {
        add_err(r, "E_INTERNAL", -1, 0, "kein Speicher");
        if (E.w) _aligned_free(E.w);
        if (E.start_evt) CloseHandle(E.start_evt);
        free(pick); sb_topo_free(&topo);
        return 1;
    }
    memset(E.w, 0, sizeof(cworker) * (size_t)nthreads);
    E.nw = nthreads;
    if (nthreads > avail)
        logf_(cb, "WARN", "%d Threads auf %d logischen Prozessoren: CPUs werden mehrfach belegt.", nthreads, avail);
    int rc = 0;
    double total_prep = 0;
    uint64_t qpf = sb_qpf();
    /* Prioritäten: Die Worker rechnen ohne Pause auf allen gewählten Prozessoren. Bei gleicher
     * Priorität hängt alles andere (dieser Steuer-Thread mit Ticks und Stop-Event, die GUI,
     * `sbench stop`, Defender, Runner-Agent) davon ab, dass der Scheduler Zeitscheiben
     * fair verteilt. Auf manchen VMs passiert das nicht: auf GitHub-Runnern mit 4 vCPUs liefen
     * Ticks nur noch im 4-8-s-Takt des Balance-Set-Managers, eine Messung von 2 s dauerte 9 s
     * und `sbench stop` brauchte 20-30 s. Deshalb: Worker BELOW_NORMAL (geben jeder normal
     * priorisierten Arbeit sofort Vorrang, auf einem ruhigen System ohne Einfluss auf die
     * Messwerte), Steuer-Thread HIGHEST (wartet fast nur, verdrängt die Worker nur kurz). */
    HANDLE self = GetCurrentThread();
    int prio_old = GetThreadPriority(self);
    SetThreadPriority(self, THREAD_PRIORITY_HIGHEST);
    /* 1. Threads anlegen, binden, starten (preparing) */
    /* Vertrag 1.3.1: dieselbe CPU-Liste steht auch im Ergebnis (tests[].cpus) */
    r->cpus_json = sb_phase_cpus_json(&topo, pick, nthreads);
    if (cb->phase) {
        /* Vertrag 1.3: Thread-Anzahl und gebundene CPUs (mit SMT-Geschwistern) beim Teststart */
        cJSON *ex = cJSON_CreateObject();
        cJSON_AddNumberToObject(ex, "threads", nthreads);
        cJSON_AddItemToObject(ex, "cpus", r->cpus_json ? cJSON_Duplicate(r->cpus_json, 1) : sb_phase_cpus_json(&topo, pick, nthreads));
        cb->phase(cb->ctx, ti, "preparing", -1, ex);
        cJSON_Delete(ex);
    }
    int ph = SB_STOP_PREPARING;
    for (int i = 0; i < nthreads; i++) {
        cworker *w = &E.w[i];
        w->e = &E; w->id = i;
        w->cpu = &topo.cpus[pick[i]];
        w->mem_node = t->mem_node >= 0 ? t->mem_node : w->cpu->node;
        r->cpus[i] = w->cpu->index;
        r->nodes[i] = w->cpu->node;
        r->mem_nodes[i] = E.kind == SB_CK_CPU ? -1 : w->mem_node;
        if (E.kind != SB_CK_CPU) total_prep += (double)sb_mem_buffer_bytes(E.kind == SB_CK_MEM_LAT, t->buffer_size);
        w->th = CreateThread(NULL, 0, worker_main, w, CREATE_SUSPENDED, NULL);
        if (!w->th) {
            add_err(r, "E_INTERNAL", i, 0, "Thread %d nicht anlegbar: %s", i, sb_errtext(GetLastError()));
            rc = 1;
            break;
        }
        SetThreadPriority(w->th, THREAD_PRIORITY_BELOW_NORMAL);
        if (sb_topo_pin_thread(w->th, w->cpu))
            logf_(cb, "WARN", "Thread %d: Bindung an CPU %u fehlgeschlagen: %s", i, w->cpu->index, sb_errtext(GetLastError()));
        ResumeThread(w->th);
    }
    logf_(cb, "INFO", "%s: %d Thread(s), CPUs %u..%u, numa_node=%d, mem_node=%d", sb_test_type_name(t->type), nthreads,
          r->cpus[0], r->cpus[nthreads - 1], t->numa_node, t->mem_node);
    /* warten, bis alle vorbereitet sind */
    uint64_t last = 0;
    while (rc == 0 && E.ready < nthreads) {
        if (o->stop_event && WaitForSingleObject(o->stop_event, 100) == WAIT_OBJECT_0) { r->aborted = 1; rc = 3; break; }
        if (!o->stop_event) Sleep(100);
        uint64_t now = sb_qpc();
        if (cb->progress && total_prep > 0 && now - last > qpf / 4) {
            double done = 0;
            for (int i = 0; i < nthreads; i++) done += (double)E.w[i].prep_bytes;
            cb->progress(cb->ctx, ti, "preparing", done > total_prep ? total_prep : done, total_prep, "bytes");
            last = now;
        }
    }
    for (int i = 0; i < nthreads && rc == 0; i++)
        if (E.w[i].err) {
            add_err(r, "E_OUT_OF_MEMORY", i, 0, "%s", E.w[i].errmsg);
            if (cb->error) cb->error(cb->ctx, ti, "E_OUT_OF_MEMORY", E.w[i].errmsg);
            logf_(cb, "ERROR", "%s", E.w[i].errmsg);
            rc = 1;
        }
    if (rc == 0) {
        if (cb->progress && total_prep > 0) cb->progress(cb->ctx, ti, "preparing", total_prep, total_prep, "bytes");
        r->buffer_bytes = E.kind == SB_CK_CPU ? 0 : E.w[0].bytes;
        /* 2. Warmup */
        E.phase = 1;
        SetEvent(E.start_evt);
        if (t->warmup_s > 0) {
            ph = SB_STOP_WARMUP;
            if (cb->phase) cb->phase(cb->ctx, ti, "warmup", t->warmup_s, NULL);
            uint64_t ws = sb_qpc(), end = ws + (uint64_t)(t->warmup_s * (double)qpf);
            for (;;) {
                uint64_t step = sb_qpc() + qpf / 2;
                if (step > end) step = end;
                if (wait_until(o->stop_event, step)) { r->aborted = 1; rc = 3; break; }
                double done = (double)(sb_qpc() - ws) / (double)qpf;
                if (cb->progress) cb->progress(cb->ctx, ti, "warmup", done > t->warmup_s ? t->warmup_s : done, t->warmup_s, "s");
                if (sb_qpc() >= end) break;
            }
        }
    }
    if (rc == 0) {
        /* 3. Messung */
        ph = SB_STOP_MEASURE;
        if (cb->phase) cb->phase(cb->ctx, ti, "measure", t->duration_s, NULL);
        r->measured = 1;
        uint64_t *u0 = (uint64_t *)calloc((size_t)nthreads, sizeof(uint64_t));
        uint64_t *up = (uint64_t *)calloc((size_t)nthreads, sizeof(uint64_t));
        if (!u0 || !up) { free(u0); free(up); add_err(r, "E_INTERNAL", -1, 0, "kein Speicher"); rc = 1; }
        else {
            sb_cpu_sample c0, cp, cn;
            sb_cpu_sample_now(&c0);
            cp = c0;
            uint64_t p0 = sb_proc_cpu_now(), pp = p0;
            uint64_t q0 = sb_qpc();
            for (int i = 0; i < nthreads; i++) up[i] = u0[i] = E.w[i].units;
            uint64_t qp = q0, end = q0 + (uint64_t)(t->duration_s * (double)qpf);
            uint64_t tick = (uint64_t)((double)(o->tick_ms ? o->tick_ms : 1000) * (double)qpf / 1000.0);
            DWORD ncpu = (DWORD)topo.ncpus;
            for (;;) {
                uint64_t nx = qp + tick;
                if (nx > end) nx = end;
                int stopped = wait_until(o->stop_event, nx);
                uint64_t qn = sb_qpc();
                double dt = (double)(qn - qp) / (double)qpf;
                sb_ctick k;
                memset(&k, 0, sizeof k);
                k.t = (double)(qn - q0) / (double)qpf;
                k.nworkers = nthreads;
                k.w_value = (double *)calloc((size_t)nthreads, sizeof(double));
                uint64_t sum = 0;
                for (int i = 0; i < nthreads; i++) {
                    uint64_t u = E.w[i].units, d = u - up[i];
                    sum += d;
                    if (k.w_value) k.w_value[i] = to_value(E.kind, d, dt);
                    up[i] = u;
                }
                k.value = to_value(E.kind, sum, dt);
                k.score = E.kind == SB_CK_CPU ? sb_cpu_score((sb_cpu_workload)t->workload, k.value) : -1;
                sb_cpu_sample_now(&cn);
                k.cpu_pct = sb_cpu_pct(&cp, &cn);
                uint64_t pn = sb_proc_cpu_now();
                k.proc_cpu_pct = dt > 0 && pn >= pp ? (double)(pn - pp) / 1e7 / (dt * (double)ncpu) * 100.0 : 0;
                if (k.proc_cpu_pct > 100) k.proc_cpu_pct = 100;
                cp = cn; pp = pn; qp = qn;
                if (dt > 0.01 || qn >= end) {
                    if (r->nticks == r->cap_ticks) {
                        int nc = r->cap_ticks ? r->cap_ticks * 2 : 64;
                        sb_ctick *nt2 = (sb_ctick *)realloc(r->ticks, sizeof(sb_ctick) * (size_t)nc);
                        if (nt2) { r->ticks = nt2; r->cap_ticks = nc; }
                    }
                    if (r->nticks < r->cap_ticks) {
                        r->ticks[r->nticks++] = k;
                        if (cb->ctick) cb->ctick(cb->ctx, ti, (int)E.kind, &k);
                    } else free(k.w_value);
                } else free(k.w_value);
                if (stopped) { r->aborted = 1; rc = 3; break; }
                if (qn >= end) break;
            }
            uint64_t q1 = sb_qpc();
            r->measured_s = (double)(q1 - q0) / (double)qpf;
            sb_cpu_sample_now(&cn);
            r->cpu_pct = sb_cpu_pct(&c0, &cn);
            uint64_t p1 = sb_proc_cpu_now();
            r->proc_cpu_pct = r->measured_s > 0 && p1 >= p0 ? (double)(p1 - p0) / 1e7 / (r->measured_s * (double)ncpu) * 100.0 : 0;
            if (r->proc_cpu_pct > 100) r->proc_cpu_pct = 100;
            for (int i = 0; i < nthreads; i++) {
                r->w_units[i] = E.w[i].units - u0[i];
                r->units += r->w_units[i];
                r->w_value[i] = to_value(E.kind, r->w_units[i], r->measured_s);
            }
            r->value = to_value(E.kind, r->units, r->measured_s);
            if (E.kind == SB_CK_CPU) r->score = sb_cpu_score((sb_cpu_workload)t->workload, r->value);
            free(u0); free(up);
        }
    }
    /* 4. Aufräumen */
    E.phase = 2;
    SetEvent(E.start_evt);
    if (cb->phase) cb->phase(cb->ctx, ti, "cleanup", -1, NULL);
    for (int i = 0; i < nthreads; i++) {
        cworker *w = &E.w[i];
        if (w->th) { WaitForSingleObject(w->th, 60000); CloseHandle(w->th); }
        if (w->buf) VirtualFree(w->buf, 0, MEM_RELEASE);
    }
    CloseHandle(E.start_evt);
    _aligned_free(E.w);
    free(pick);
    sb_topo_free(&topo);
    if (prio_old != THREAD_PRIORITY_ERROR_RETURN) SetThreadPriority(self, prio_old);
    if (rc == 0 && r->aborted) rc = 3;
    r->stop_phase = rc == 1 || rc == 3 ? ph : SB_STOP_NONE;
    return rc;
}
