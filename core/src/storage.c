/* Storage-Benchmark-Engine.
 *
 * Aufbau: pro Worker ein eigener Thread, ein eigenes Handle auf sein Target
 * (Worker i -> Target i mod Anzahl Targets) und ein eigener I/O Completion Port.
 * Jeder Worker hält queue_depth I/Os ausstehend. Latenz = QPC(Completion aus
 * GetQueuedCompletionStatusEx) - QPC(direkt vor ReadFile/WriteFile).
 * Gezählt werden nur I/Os, die in der Messphase gestartet UND vor deren Ende
 * abgeschlossen wurden. Statistiken sind pro Worker (lockfrei) und werden am
 * Ende zusammengeführt. Tick-Werte: Doppelpuffer pro Worker, der Hauptthread
 * fordert einen Wechsel an, der Worker quittiert ihn beim nächsten Durchlauf. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sb/storage.h"
#include "sb/target.h"
#include "sb/rng.h"

enum { PH_INIT = 0, PH_WARMUP = 1, PH_MEASURE = 2, PH_STOP = 3 };

#define WBUF_SPAN   (4u << 20)      /* Zufallsdaten-Fenster für Schreib-I/Os */
#define MAX_CONSEC_ERRORS 10000u

typedef struct slot {
    OVERLAPPED ov;                  /* muss erstes Feld sein */
    uint64_t   t_issue;
    uint8_t   *buf;
    uint32_t   len;
    int        spec;
    int        write;
    int        phase;
} slot;

typedef struct ival { sb_io_counts c; sb_hist h; } ival;

struct engine;

typedef struct worker {
    int           id, target;
    HANDLE        h, iocp, thread;
    uint64_t      base, len, seq_next, align_unit;
    sb_rng        rng;
    slot         *slots;
    slot        **freelist; uint32_t nfree;
    uint32_t      qd, inflight;
    uint8_t      *rbufs;  size_t rbufs_len;
    uint8_t      *wbuf;   size_t wbuf_len;
    uint64_t      max_bs;
    uint32_t      cum[SB_MAX_SPECS];
    /* Statistik der Messphase (nur dieser Thread schreibt) */
    sb_io_counts  cnt;
    sb_hist      *hist;
    sb_io_counts *scnt;
    sb_hist     **shist;
    /* Tick-Doppelpuffer */
    ival         *iv;               /* [2] */
    volatile LONG iv_active, iv_req, iv_ack;
    /* Fehler */
    uint64_t      consec_err;
    DWORD         last_err;
    uint64_t      err_issue, err_complete;
    volatile LONG fatal;
    /* Burst */
    uint32_t      burst_left;
    uint64_t      next_issue;
    volatile LONG finished;
    struct engine *eng;
} worker;

typedef struct engine {
    const sb_test    *t;
    volatile LONG     phase;
    volatile LONG64   measure_end;  /* QPC-Zeitpunkt Ende der Messphase */
    HANDLE            start_evt;
    worker           *w;
    int               nw;
    uint64_t          qpf;
} engine;

/* ---- CPU-Last -------------------------------------------------------- */

static uint64_t ft64(FILETIME f) { return ((uint64_t)f.dwHighDateTime << 32) | f.dwLowDateTime; }

void sb_cpu_sample_now(sb_cpu_sample *s) {
    FILETIME i, k, u;
    if (GetSystemTimes(&i, &k, &u)) { s->idle = ft64(i); s->kernel = ft64(k); s->user = ft64(u); }
    else memset(s, 0, sizeof *s);
}

uint64_t sb_proc_cpu_now(void) {
    FILETIME c, x, k, u;
    if (!GetProcessTimes(GetCurrentProcess(), &c, &x, &k, &u)) return 0;
    return (((uint64_t)k.dwHighDateTime << 32) | k.dwLowDateTime) + (((uint64_t)u.dwHighDateTime << 32) | u.dwLowDateTime);
}

double sb_cpu_pct(const sb_cpu_sample *a, const sb_cpu_sample *b) {
    uint64_t idle = b->idle - a->idle, ker = b->kernel - a->kernel, usr = b->user - a->user;
    uint64_t tot = ker + usr;            /* Kernelzeit enthält Idle */
    if (!tot) return 0.0;
    double p = 100.0 * (double)(tot - (idle > tot ? tot : idle)) / (double)tot;
    return p < 0 ? 0 : p > 100 ? 100 : p;
}

/* ---- Ergebnis-Hilfen ------------------------------------------------- */

static void add_err(sb_storage_result *r, const char *code, int worker_id, int target, double t, uint64_t count,
                    const char *fmt, ...) {
    for (int i = 0; i < r->nerrs; i++)
        if (!strcmp(r->errs[i].code, code) && r->errs[i].worker == worker_id && r->errs[i].target == target) {
            r->errs[i].count += count;
            return;
        }
    if (r->nerrs == r->cap_errs) {
        int nc = r->cap_errs ? r->cap_errs * 2 : 8;
        sb_run_error *n = (sb_run_error *)realloc(r->errs, (size_t)nc * sizeof *n);
        if (!n) return;
        r->errs = n; r->cap_errs = nc;
    }
    sb_run_error *e = &r->errs[r->nerrs++];
    memset(e, 0, sizeof *e);
    snprintf(e->code, sizeof e->code, "%s", code);
    va_list ap; va_start(ap, fmt); vsnprintf(e->msg, sizeof e->msg, fmt, ap); va_end(ap);
    e->worker = worker_id; e->target = target; e->t = t; e->count = count ? count : 1;
}

void sb_storage_result_free(sb_storage_result *r) {
    for (int i = 0; i < r->nworkers; i++) if (r->w_hist) free(r->w_hist[i]);
    for (int i = 0; i < r->nspecs; i++) if (r->s_hist) free(r->s_hist[i]);
    free(r->w_hist); free(r->s_hist); free(r->w_counts); free(r->s_counts); free(r->hist);
    free(r->worker_target);
    for (int i = 0; i < r->nticks; i++) { free(r->ticks[i].w_iops); free(r->ticks[i].w_mbps); free(r->ticks[i].w_lat_mean_us); free(r->ticks[i].w_lat_p99_us); }
    free(r->ticks); free(r->errs);
    free(r->t_created); free(r->t_deleted);
    memset(r, 0, sizeof *r);
}

/* ---- Worker ---------------------------------------------------------- */

static uint64_t ms_to_qpc(const engine *e, double ms) { return (uint64_t)(ms * (double)e->qpf / 1000.0); }

static int issue_one(worker *w, uint64_t now) {
    const sb_test *t = w->eng->t;
    slot *s = w->freelist[--w->nfree];
    int si = t->nspecs == 1 ? 0 : sb_pick_spec(w->cum, t->nspecs, sb_rng_below(&w->rng, 100));
    const sb_spec *sp = &t->specs[si];
    uint64_t bs = sp->block_size;
    int wr = sp->read_pct < 100 && sb_rng_below(&w->rng, 100) >= sp->read_pct;
    int rnd = sp->random_pct > 0 && (sp->random_pct >= 100 || sb_rng_below(&w->rng, 100) < sp->random_pct);
    uint64_t off;
    if (rnd) {
        uint64_t al = sp->align ? sp->align : bs;
        uint64_t n = (w->len - bs) / al + 1;
        off = w->base + sb_rng_below(&w->rng, n) * al;
    } else {
        off = w->seq_next;
        if (off + bs > w->base + w->len || off < w->base) off = w->base;
        w->seq_next = off + bs;
    }
    s->spec = si;
    s->write = wr;
    s->len = (uint32_t)bs;
    s->phase = (int)w->eng->phase;
    memset(&s->ov, 0, sizeof s->ov);
    s->ov.Offset = (DWORD)off;
    s->ov.OffsetHigh = (DWORD)(off >> 32);
    uint8_t *buf = s->buf;
    if (wr) {
        uint64_t k = sb_rng_below(&w->rng, WBUF_SPAN / 4096u);
        buf = w->wbuf + k * 4096u;
    }
    if (sp->delay_ms) {
        if (w->burst_left <= 1) {
            w->burst_left = sp->burst_len ? sp->burst_len : 1;
            w->next_issue = now + ms_to_qpc(w->eng, sp->delay_ms);
        } else {
            w->burst_left--;
        }
    }
    s->t_issue = sb_qpc();
    BOOL ok = wr ? WriteFile(w->h, buf, s->len, NULL, &s->ov) : ReadFile(w->h, buf, s->len, NULL, &s->ov);
    if (!ok) {
        DWORD e = GetLastError();
        if (e != ERROR_IO_PENDING) {
            w->freelist[w->nfree++] = s;
            w->last_err = e;
            w->err_issue++;
            if (s->phase == PH_MEASURE) w->cnt.errors++;
            if (++w->consec_err > MAX_CONSEC_ERRORS) InterlockedExchange(&w->fatal, 1);
            return -1;
        }
    }
    w->inflight++;
    return 0;
}

static void record(worker *w, slot *s, uint64_t lat_ns) {
    int sp = s->spec;
    ival *iv = &w->iv[w->iv_active];
    if (s->write) {
        w->cnt.ops_w++; w->cnt.bytes_w += s->len;
        w->scnt[sp].ops_w++; w->scnt[sp].bytes_w += s->len;
        iv->c.ops_w++; iv->c.bytes_w += s->len;
    } else {
        w->cnt.ops_r++; w->cnt.bytes_r += s->len;
        w->scnt[sp].ops_r++; w->scnt[sp].bytes_r += s->len;
        iv->c.ops_r++; iv->c.bytes_r += s->len;
    }
    sb_hist_record(w->hist, lat_ns);
    sb_hist_record(w->shist[sp], lat_ns);
    sb_hist_record(&iv->h, lat_ns);
}

static void check_swap(worker *w) {
    LONG req = w->iv_req;
    if (req != w->iv_ack) {
        w->iv_active = 1 - w->iv_active;
        MemoryBarrier();
        w->iv_ack = req;
    }
}

static DWORD WINAPI worker_main(LPVOID arg) {
    worker *w = (worker *)arg;
    engine *e = w->eng;
    OVERLAPPED_ENTRY ents[64];
    WaitForSingleObject(e->start_evt, INFINITE);
    const double ns_per_tick = 1e9 / (double)e->qpf;
    for (;;) {
        LONG ph = e->phase;
        if (ph == PH_STOP || w->fatal) break;
        uint64_t now = sb_qpc();
        while (w->nfree && now >= w->next_issue && !w->fatal) {
            if (issue_one(w, now) == 0) w->consec_err = 0;
            if (w->next_issue > now) break;
        }
        DWORD timeout = 50;
        if (w->next_issue > now) {
            double ms = (double)(w->next_issue - now) * 1000.0 / (double)e->qpf;
            if (ms < timeout) timeout = (DWORD)(ms < 1 ? 1 : ms);
        }
        if (!w->inflight && w->nfree && now >= w->next_issue) timeout = 0;  /* nur Issue-Fehler: weiter versuchen */
        ULONG n = 0;
        if (w->inflight && GetQueuedCompletionStatusEx(w->iocp, ents, 64, &n, timeout, FALSE)) {
            uint64_t tc = sb_qpc();
            uint64_t mend = (uint64_t)e->measure_end;
            for (ULONG i = 0; i < n; i++) {
                slot *s = (slot *)ents[i].lpOverlapped;
                w->inflight--;
                /* OVERLAPPED.Internal enthält den NTSTATUS der Operation */
                int failed = s->ov.Internal != 0 || ents[i].dwNumberOfBytesTransferred != s->len;
                if (failed) {
                    w->err_complete++;
                    w->last_err = (DWORD)s->ov.Internal;
                    if (s->phase == PH_MEASURE && tc <= mend) w->cnt.errors++;
                    if (++w->consec_err > MAX_CONSEC_ERRORS) InterlockedExchange(&w->fatal, 1);
                } else {
                    w->consec_err = 0;
                    if (s->phase == PH_MEASURE && tc <= mend)
                        record(w, s, (uint64_t)((double)(tc - s->t_issue) * ns_per_tick + 0.5));
                }
                w->freelist[w->nfree++] = s;
            }
        } else if (!w->inflight && timeout) {
            Sleep(timeout);
        }
        check_swap(w);
    }
    /* Ausstehende I/Os abbrechen und abwarten (max. 30 s) */
    if (w->inflight) CancelIoEx(w->h, NULL);
    DWORD t0 = GetTickCount();
    while (w->inflight && GetTickCount() - t0 < 30000) {
        ULONG n = 0;
        if (GetQueuedCompletionStatusEx(w->iocp, ents, 64, &n, 100, FALSE)) {
            for (ULONG i = 0; i < n; i++) { w->inflight--; w->freelist[w->nfree++] = (slot *)ents[i].lpOverlapped; }
        }
        check_swap(w);
    }
    check_swap(w);
    InterlockedExchange(&w->finished, 1);
    return 0;
}

/* ---- Hauptablauf ----------------------------------------------------- */

typedef struct fillctx {
    const sb_storage_cb *cb; HANDLE stop; uint64_t last_log; const char *path;
    int ti; uint64_t base, all; ULONGLONG last_ev;
} fillctx;

static int fill_cb(void *c, uint64_t done, uint64_t total) {
    fillctx *f = (fillctx *)c;
    if (f->stop && WaitForSingleObject(f->stop, 0) == WAIT_OBJECT_0) return 1;
    ULONGLONG nowms = GetTickCount64();
    if (f->cb->progress && (nowms - f->last_ev >= 1000 || done == total)) {
        f->last_ev = nowms;
        f->cb->progress(f->cb->ctx, f->ti, "preparing", (double)(f->base + done), (double)f->all, "bytes");
    }
    if (done - f->last_log >= (1ULL << 30) || done == total) {
        f->last_log = done;
        char m[300];
        snprintf(m, sizeof m, "Testdatei %s: %llu von %llu MiB geschrieben", f->path,
                 (unsigned long long)(done >> 20), (unsigned long long)(total >> 20));
        if (f->cb->log) f->cb->log(f->cb->ctx, "INFO", m);
    }
    return 0;
}

static void logf_(const sb_storage_cb *cb, const char *lvl, const char *fmt, ...) {
    if (!cb->log) return;
    char m[1024];
    va_list ap; va_start(ap, fmt); vsnprintf(m, sizeof m, fmt, ap); va_end(ap);
    cb->log(cb->ctx, lvl, m);
}

static int alloc_result(sb_storage_result *r, int nw, int ns) {
    r->nworkers = nw; r->nspecs = ns;
    r->worker_target = (int *)calloc((size_t)nw, sizeof(int));
    r->w_counts = (sb_io_counts *)calloc((size_t)nw, sizeof(sb_io_counts));
    r->w_hist = (sb_hist **)calloc((size_t)nw, sizeof(sb_hist *));
    r->s_counts = (sb_io_counts *)calloc((size_t)ns, sizeof(sb_io_counts));
    r->s_hist = (sb_hist **)calloc((size_t)ns, sizeof(sb_hist *));
    r->hist = (sb_hist *)malloc(sizeof(sb_hist));
    if (!r->worker_target || !r->w_counts || !r->w_hist || !r->s_counts || !r->s_hist || !r->hist) return -1;
    sb_hist_init(r->hist);
    for (int i = 0; i < nw; i++) { r->w_hist[i] = (sb_hist *)malloc(sizeof(sb_hist)); if (!r->w_hist[i]) return -1; sb_hist_init(r->w_hist[i]); }
    for (int i = 0; i < ns; i++) { r->s_hist[i] = (sb_hist *)malloc(sizeof(sb_hist)); if (!r->s_hist[i]) return -1; sb_hist_init(r->s_hist[i]); }
    return 0;
}

static void free_worker(worker *w) {
    if (w->thread) CloseHandle(w->thread);
    if (w->iocp) CloseHandle(w->iocp);
    if (w->h && w->h != INVALID_HANDLE_VALUE) CloseHandle(w->h);
    if (w->rbufs) VirtualFree(w->rbufs, 0, MEM_RELEASE);
    if (w->wbuf) VirtualFree(w->wbuf, 0, MEM_RELEASE);
    free(w->slots); free(w->freelist);
    if (w->shist) for (int i = 0; i < w->eng->t->nspecs; i++) free(w->shist[i]);
    free(w->shist); free(w->scnt); free(w->hist); free(w->iv);
}

/* Tick-Intervall einsammeln; 0 = ok */
static void collect_tick(engine *e, sb_storage_result *r, double t, double dt, double cpu, double pcpu,
                         const sb_storage_cb *cb, int test_index) {
    /* Wechsel anfordern */
    for (int i = 0; i < e->nw; i++) {
        worker *w = &e->w[i];
        if (w->iv_ack == w->iv_req) InterlockedIncrement(&w->iv_req);
    }
    DWORD t0 = GetTickCount();
    for (;;) {
        int all = 1;
        for (int i = 0; i < e->nw; i++) if (e->w[i].iv_ack != e->w[i].iv_req && !e->w[i].finished) all = 0;
        if (all || GetTickCount() - t0 > 250) break;
        Sleep(1);
    }
    sb_tick_sample s;
    memset(&s, 0, sizeof s);
    s.t = t; s.cpu_pct = cpu; s.proc_cpu_pct = pcpu; s.nworkers = e->nw;
    s.w_iops = (double *)calloc((size_t)e->nw, sizeof(double));
    s.w_mbps = (double *)calloc((size_t)e->nw, sizeof(double));
    s.w_lat_mean_us = (double *)calloc((size_t)e->nw, sizeof(double));
    s.w_lat_p99_us = (double *)calloc((size_t)e->nw, sizeof(double));
    sb_hist *h = (sb_hist *)malloc(sizeof(sb_hist));
    if (!h || !s.w_iops || !s.w_mbps || !s.w_lat_mean_us || !s.w_lat_p99_us) {
        free(h); free(s.w_iops); free(s.w_mbps); free(s.w_lat_mean_us); free(s.w_lat_p99_us); return;
    }
    sb_hist_init(h);
    uint64_t ops = 0, bytes = 0, or_ = 0, ow = 0, br = 0, bw = 0;
    for (int i = 0; i < e->nw; i++) {
        worker *w = &e->w[i];
        s.w_lat_mean_us[i] = s.w_lat_p99_us[i] = -1;
        if (w->iv_ack != w->iv_req) continue;          /* noch nicht quittiert: im nächsten Tick */
        MemoryBarrier();
        ival *iv = &w->iv[1 - w->iv_active];
        uint64_t o = iv->c.ops_r + iv->c.ops_w, b = iv->c.bytes_r + iv->c.bytes_w;
        ops += o; bytes += b;
        or_ += iv->c.ops_r; ow += iv->c.ops_w; br += iv->c.bytes_r; bw += iv->c.bytes_w;
        if (iv->h.count) {
            s.w_lat_mean_us[i] = sb_hist_mean(&iv->h) / 1000.0;
            s.w_lat_p99_us[i] = sb_hist_percentile(&iv->h, 99) / 1000.0;
        }
        s.w_iops[i] = dt > 0 ? (double)o / dt : 0;
        s.w_mbps[i] = dt > 0 ? (double)b / dt / 1e6 : 0;
        sb_hist_merge(h, &iv->h);
        memset(&iv->c, 0, sizeof iv->c);
        sb_hist_init(&iv->h);
    }
    s.iops = dt > 0 ? (double)ops / dt : 0;
    s.mbps = dt > 0 ? (double)bytes / dt / 1e6 : 0;
    s.read_iops = dt > 0 ? (double)or_ / dt : 0;
    s.write_iops = dt > 0 ? (double)ow / dt : 0;
    s.read_mbps = dt > 0 ? (double)br / dt / 1e6 : 0;
    s.write_mbps = dt > 0 ? (double)bw / dt / 1e6 : 0;
    s.lat_mean_us = sb_hist_mean(h) / 1000.0;
    s.lat_p50_us = sb_hist_percentile(h, 50) / 1000.0;
    s.lat_p99_us = sb_hist_percentile(h, 99) / 1000.0;
    s.lat_max_us = (double)h->max_ns / 1000.0;
    free(h);
    if (r->nticks == r->cap_ticks) {
        int nc = r->cap_ticks ? r->cap_ticks * 2 : 64;
        sb_tick_sample *nt = (sb_tick_sample *)realloc(r->ticks, (size_t)nc * sizeof *nt);
        if (!nt) { free(s.w_iops); free(s.w_mbps); free(s.w_lat_mean_us); free(s.w_lat_p99_us); return; }
        r->ticks = nt; r->cap_ticks = nc;
    }
    r->ticks[r->nticks++] = s;
    if (cb->tick) cb->tick(cb->ctx, test_index, &s);
}

static double proc_pct(uint64_t a, uint64_t b, double dt, DWORD ncpu) {
    if (dt <= 0 || b < a) return 0;
    double v = (double)(b - a) / 1e7 / (dt * (double)ncpu) * 100.0;
    return v > 100.0 ? 100.0 : v;
}

/* wartet bis deadline (QPC) oder Stop; 1 = Stop */
static int wait_until(HANDLE stop, uint64_t deadline) {
    for (;;) {
        uint64_t now = sb_qpc();
        if (now >= deadline) return 0;
        double ms = (double)(deadline - now) * 1000.0 / (double)sb_qpf();
        DWORD d = (DWORD)(ms + 0.999);
        if (d > 1000) d = 1000;
        if (stop) {
            if (WaitForSingleObject(stop, d) == WAIT_OBJECT_0) return 1;
        } else Sleep(d);
    }
}

int sb_storage_run(int ti, const sb_test *t, const sb_storage_opts *o, const sb_storage_cb *cb, sb_storage_result *r) {
    memset(r, 0, sizeof *r);
    int nw = (int)t->workers, rc = 0;
    r->ntargets = t->ntargets;
    r->t_created = (int *)calloc((size_t)(t->ntargets ? t->ntargets : 1), sizeof(int));
    r->t_deleted = (int *)calloc((size_t)(t->ntargets ? t->ntargets : 1), sizeof(int));
    if (!r->t_created || !r->t_deleted) { add_err(r, "E_INTERNAL", -1, -1, 0, 1, "kein Speicher"); return 1; }
    if (alloc_result(r, nw, t->nspecs)) { add_err(r, "E_INTERNAL", -1, -1, 0, 1, "kein Speicher"); return 1; }
    engine E;
    memset(&E, 0, sizeof E);
    E.t = t; E.nw = nw; E.qpf = sb_qpf();
    E.measure_end = (LONG64)INT64_MAX;
    E.w = (worker *)calloc((size_t)nw, sizeof(worker));
    E.start_evt = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!E.w || !E.start_evt) { add_err(r, "E_INTERNAL", -1, -1, 0, 1, "kein Speicher"); free(E.w); return 1; }
    char err[600];
    int need_write = 0;
    uint64_t max_bs = 0, max_al = 0;
    for (int i = 0; i < t->nspecs; i++) {
        if (t->specs[i].read_pct < 100) need_write = 1;
        if (t->specs[i].block_size > max_bs) max_bs = t->specs[i].block_size;
        if (t->specs[i].align > max_al) max_al = t->specs[i].align;
    }

    /* 1. Vorbereitung: Testdateien anlegen */
    if (cb->phase) cb->phase(cb->ctx, ti, "preparing", -1);
    uint64_t prep_all = 0, prep_base = 0;
    for (int k = 0; k < t->ntargets; k++) if (t->targets[k].type == SB_TGT_FILE) prep_all += t->targets[k].file_size;
    for (int k = 0; k < t->ntargets; k++) {
        const sb_target *T = &t->targets[k];
        if (T->type != SB_TGT_FILE) continue;
        int vdl = 0, filled = 0, created = 0;
        fillctx fc = {cb, o->stop_event, 0, T->path, ti, prep_base, prep_all, 0};
        logf_(cb, "INFO", "Testdatei %s vorbereiten (%llu Bytes)", T->path, (unsigned long long)T->file_size);
        int pr = sb_target_prepare_file(T, &created, &vdl, &filled, fill_cb, &fc, err, sizeof err);
        if (created) {
            /* gleicher Pfad mehrfach im Test: nur der erste Eintrag gilt als angelegt */
            r->t_created[k] = 1;
        }
        if (pr == SB_TERR_STOPPED) { r->aborted = 1; rc = 3; goto out; }
        if (pr != SB_TOK) {
            add_err(r, pr == SB_TERR_ACCESS ? "E_ACCESS_DENIED" : "E_IO", -1, k, 0, 1, "%s", err);
            if (cb->error) cb->error(cb->ctx, ti, pr == SB_TERR_ACCESS ? "E_ACCESS_DENIED" : "E_IO", err);
            logf_(cb, "ERROR", "%s", err);
            rc = pr == SB_TERR_ACCESS ? 4 : 1;
            goto out;
        }
        if (vdl) logf_(cb, "INFO", "Testdatei %s per SetFileValidData angelegt (SeManageVolumePrivilege)", T->path);
        else if (filled) logf_(cb, "INFO", "Testdatei %s mit Zufallsdaten gefüllt (kein SeManageVolumePrivilege)", T->path);
        else logf_(cb, "INFO", "Vorhandene Testdatei %s wird weiterverwendet", T->path);
        prep_base += T->file_size;
        if (cb->progress && !filled)
            cb->progress(cb->ctx, ti, "preparing", (double)prep_base, (double)prep_all, "bytes");
    }

    /* 2. Worker einrichten: Handle, IOCP, Puffer */
    for (int i = 0; i < nw; i++) {
        worker *w = &E.w[i];
        w->id = i; w->eng = &E; w->target = i % t->ntargets;
        r->worker_target[i] = w->target;
        const sb_target *T = &t->targets[w->target];
        w->h = INVALID_HANDLE_VALUE;
        int orc = sb_target_open(T, t->cache, need_write, &w->h, err, sizeof err);
        if (orc != SB_TOK) {
            const char *code = orc == SB_TERR_ACCESS ? "E_ACCESS_DENIED" : orc == SB_TERR_NOTFOUND ? "E_TARGET_NOT_FOUND" : "E_IO";
            if (orc == SB_TERR_ACCESS && T->type != SB_TGT_FILE)
                snprintf(err + strlen(err), sizeof err - strlen(err), " – für %s sind Administratorrechte nötig",
                         sb_target_type_name(T->type));
            add_err(r, code, i, w->target, 0, 1, "%s", err);
            if (cb->error) cb->error(cb->ctx, ti, code, err);
            logf_(cb, "ERROR", "%s", err);
            rc = orc == SB_TERR_ACCESS ? 4 : 1;
            goto out;
        }
        uint64_t size = sb_target_handle_size(T, w->h);
        if (T->type == SB_TGT_FILE && T->file_size && size > T->file_size) size = T->file_size;
        uint64_t unit = max_al > 4096 ? 4096 : 4096;  /* Bereich auf 4 KiB abrunden */
        size -= size % unit;
        if (size < max_bs) {
            snprintf(err, sizeof err, "Ziel %s ist mit %llu Bytes kleiner als block_size %llu", T->path,
                     (unsigned long long)size, (unsigned long long)max_bs);
            add_err(r, "E_TARGET_SIZE", i, w->target, 0, 1, "%s", err);
            if (cb->error) cb->error(cb->ctx, ti, "E_TARGET_SIZE", err);
            rc = 1;
            goto out;
        }
        w->base = 0; w->len = size; w->max_bs = max_bs;
        /* sequenzielle Startoffsets der Worker eines Targets gleichmäßig verteilen */
        int share = 0, idx = 0;
        for (int j = 0; j < nw; j++) if (j % t->ntargets == w->target) { if (j < i) idx++; share++; }
        uint64_t st = share > 1 ? (size / (uint64_t)share) * (uint64_t)idx : 0;
        uint64_t gran = max_al ? max_al : max_bs;
        st -= st % gran;
        w->seq_next = st;
        sb_rng_seed(&w->rng, t->seed ^ ((uint64_t)(i + 1) * 0x9e3779b97f4a7c15ULL));
        uint32_t acc = 0;
        for (int k = 0; k < t->nspecs; k++) { acc += t->specs[k].weight; w->cum[k] = acc; }
        w->qd = t->queue_depth;
        w->iocp = CreateIoCompletionPort(w->h, NULL, (ULONG_PTR)i, 1);
        if (!w->iocp) {
            snprintf(err, sizeof err, "CreateIoCompletionPort: %s", sb_errtext(GetLastError()));
            add_err(r, "E_INTERNAL", i, w->target, 0, 1, "%s", err);
            rc = 1; goto out;
        }
        w->slots = (slot *)calloc(w->qd, sizeof(slot));
        w->freelist = (slot **)calloc(w->qd, sizeof(slot *));
        size_t bs_al = (size_t)((max_bs + 4095u) & ~(uint64_t)4095u);
        w->rbufs_len = bs_al * w->qd;
        w->rbufs = (uint8_t *)VirtualAlloc(NULL, w->rbufs_len, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        if (need_write) {
            w->wbuf_len = WBUF_SPAN + bs_al;
            w->wbuf = (uint8_t *)VirtualAlloc(NULL, w->wbuf_len, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        }
        w->hist = (sb_hist *)malloc(sizeof(sb_hist));
        w->iv = (ival *)malloc(2 * sizeof(ival));
        w->scnt = (sb_io_counts *)calloc((size_t)t->nspecs, sizeof(sb_io_counts));
        w->shist = (sb_hist **)calloc((size_t)t->nspecs, sizeof(sb_hist *));
        int ok = w->slots && w->freelist && w->rbufs && (!need_write || w->wbuf) && w->hist && w->iv && w->scnt && w->shist;
        for (int k = 0; ok && k < t->nspecs; k++) { w->shist[k] = (sb_hist *)malloc(sizeof(sb_hist)); if (!w->shist[k]) ok = 0; else sb_hist_init(w->shist[k]); }
        if (!ok) {
            snprintf(err, sizeof err, "Speicher für Worker %d nicht verfügbar (QD %u, Blockgröße %llu)", i, w->qd,
                     (unsigned long long)max_bs);
            add_err(r, "E_INTERNAL", i, w->target, 0, 1, "%s", err);
            if (cb->error) cb->error(cb->ctx, ti, "E_INTERNAL", err);
            rc = 1; goto out;
        }
        sb_hist_init(w->hist);
        memset(w->iv, 0, 2 * sizeof(ival));
        sb_hist_init(&w->iv[0].h); sb_hist_init(&w->iv[1].h);
        /* Schreibdaten: Zufall, nicht komprimierbar */
        if (w->wbuf) sb_rng_fill(&w->rng, w->wbuf, w->wbuf_len);
        for (uint32_t q = 0; q < w->qd; q++) {
            w->slots[q].buf = w->rbufs + (size_t)q * bs_al;
            w->freelist[w->nfree++] = &w->slots[q];
        }
        w->thread = CreateThread(NULL, 256 * 1024, worker_main, w, 0, NULL);
        if (!w->thread) {
            snprintf(err, sizeof err, "CreateThread: %s", sb_errtext(GetLastError()));
            add_err(r, "E_INTERNAL", i, w->target, 0, 1, "%s", err);
            rc = 1; goto out;
        }
    }
    logf_(cb, "INFO", "%d Worker gestartet, QD %u pro Worker, %d Access-Spec(s), cache=%s", nw, t->queue_depth,
          t->nspecs, sb_cache_name(t->cache));

    /* 3. Warmup */
    uint64_t now = sb_qpc();
    if (t->warmup_s > 0) {
        if (cb->phase) cb->phase(cb->ctx, ti, "warmup", t->warmup_s);
        E.phase = PH_WARMUP;
        SetEvent(E.start_evt);
        uint64_t wend = now + (uint64_t)(t->warmup_s * (double)E.qpf);
        for (uint64_t step = now + E.qpf;; step += E.qpf) {
            uint64_t dl = step < wend ? step : wend;
            if (wait_until(o->stop_event, dl)) {
                r->aborted = 1;
                logf_(cb, "WARN", "Stop während Warmup");
                break;
            }
            if (cb->progress) {
                double done = (double)(sb_qpc() - now) / (double)E.qpf;
                if (done > t->warmup_s) done = t->warmup_s;
                cb->progress(cb->ctx, ti, "warmup", done, t->warmup_s, "s");
            }
            if (dl >= wend) break;
        }
    }
    /* 4. Messung */
    if (!r->aborted) {
        if (cb->phase) cb->phase(cb->ctx, ti, "measure", t->duration_s);
        sb_cpu_sample c0, cprev, cnow;
        sb_cpu_sample_now(&c0);
        cprev = c0;
        DWORD ncpu = GetActiveProcessorCount(ALL_PROCESSOR_GROUPS);
        if (!ncpu) ncpu = 1;
        uint64_t pprev = sb_proc_cpu_now(), pnow;
        uint64_t mstart = sb_qpc();
        E.phase = PH_MEASURE;
        SetEvent(E.start_evt);
        r->measured = 1;
        uint64_t mlen = (uint64_t)(t->duration_s * (double)E.qpf);
        uint64_t tick = ms_to_qpc(&E, o->tick_ms ? o->tick_ms : 1000);
        uint64_t next = mstart + tick, last = mstart;
        for (;;) {
            uint64_t deadline = next < mstart + mlen ? next : mstart + mlen;
            if (wait_until(o->stop_event, deadline)) { r->aborted = 1; break; }
            int fatal = 0;
            for (int i = 0; i < nw; i++) if (E.w[i].fatal) fatal = 1;
            if (fatal) break;
            now = sb_qpc();
            if (now >= mstart + mlen) break;
            sb_cpu_sample_now(&cnow);
            pnow = sb_proc_cpu_now();
            double dt = (double)(now - last) / (double)E.qpf;
            collect_tick(&E, r, (double)(now - mstart) / (double)E.qpf, dt, sb_cpu_pct(&cprev, &cnow),
                         proc_pct(pprev, pnow, dt, ncpu), cb, ti);
            cprev = cnow; pprev = pnow; last = now;
            next += tick;
        }
        uint64_t mend = sb_qpc();
        E.measure_end = (LONG64)mend;
        MemoryBarrier();
        sb_cpu_sample_now(&cnow);
        pnow = sb_proc_cpu_now();
        /* letztes (Teil-)Intervall */
        double rest = (double)(mend - last) / (double)E.qpf;
        if (rest >= 0.1 * (double)(o->tick_ms ? o->tick_ms : 1000) / 1000.0)
            collect_tick(&E, r, (double)(mend - mstart) / (double)E.qpf, rest, sb_cpu_pct(&cprev, &cnow),
                         proc_pct(pprev, pnow, rest, ncpu), cb, ti);
        r->measured_s = (double)(mend - mstart) / (double)E.qpf;
        r->cpu_pct = sb_cpu_pct(&c0, &cnow);
    }
    E.phase = PH_STOP;
    SetEvent(E.start_evt);

out:
    E.phase = PH_STOP;
    SetEvent(E.start_evt);
    if (cb->phase && rc != 4) cb->phase(cb->ctx, ti, "cleanup", -1);
    for (int i = 0; i < nw; i++) if (E.w[i].thread) WaitForSingleObject(E.w[i].thread, 60000);
    /* 5. Zusammenführen */
    for (int i = 0; i < nw; i++) {
        worker *w = &E.w[i];
        if (w->hist) {
            r->w_counts[i] = w->cnt;
            sb_hist_merge(r->w_hist[i], w->hist);
            sb_hist_merge(r->hist, w->hist);
            r->total.ops_r += w->cnt.ops_r; r->total.ops_w += w->cnt.ops_w;
            r->total.bytes_r += w->cnt.bytes_r; r->total.bytes_w += w->cnt.bytes_w;
            r->total.errors += w->cnt.errors;
            for (int k = 0; k < t->nspecs; k++) {
                r->s_counts[k].ops_r += w->scnt[k].ops_r; r->s_counts[k].ops_w += w->scnt[k].ops_w;
                r->s_counts[k].bytes_r += w->scnt[k].bytes_r; r->s_counts[k].bytes_w += w->scnt[k].bytes_w;
                sb_hist_merge(r->s_hist[k], w->shist[k]);
            }
        }
        uint64_t ne = w->err_issue + w->err_complete;
        if (ne) {
            char m[400];
            snprintf(m, sizeof m, "Worker %d: %llu fehlgeschlagene I/O(s) auf %s, letzter Fehler: %s", i,
                     (unsigned long long)ne, t->targets[w->target].path,
                     w->err_complete && w->last_err > 0xC0000000u ? "NTSTATUS" : sb_errtext(w->last_err));
            if (w->err_complete && w->last_err > 0xC0000000u)
                snprintf(m + strlen(m), sizeof m - strlen(m), " 0x%08lX", (unsigned long)w->last_err);
            add_err(r, "E_IO", i, w->target, 0, ne, "%s", m);
            logf_(cb, "ERROR", "%s", m);
            if (cb->error) cb->error(cb->ctx, ti, "E_IO", m);
        }
        if (w->fatal && rc == 0) rc = 1;
        if (w->eng) free_worker(w);
    }
    CloseHandle(E.start_evt);
    free(E.w);
    /* 6. Aufräumen: selbst angelegte Testdateien löschen (nach Ende, Stop und Fehler) */
    for (int k = 0; k < t->ntargets; k++) {
        if (!r->t_created[k]) continue;
        const char *path = t->targets[k].path;
        if (o->keep_files) { logf_(cb, "INFO", "Testdatei %s bleibt erhalten (--keep-files)", path); continue; }
        wchar_t *wp = sb_u8_to_w(path);
        BOOL ok = wp && DeleteFileW(wp);
        DWORD de = GetLastError();
        free(wp);
        if (ok) { r->t_deleted[k] = 1; logf_(cb, "INFO", "Testdatei %s gelöscht (von sbench angelegt)", path); }
        else logf_(cb, "WARN", "Testdatei %s konnte nicht gelöscht werden: %s", path, sb_errtext(de));
    }
    if (rc == 0 && r->aborted) rc = 3;
    return rc;
}
