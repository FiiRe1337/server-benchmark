/* Befehle run / stop / validate / info. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "sb/sb.h"
#include "sb/util.h"
#include <sddl.h>
#include "sb/job.h"
#include "sb/log.h"
#include "sb/progress.h"
#include "sb/storage.h"
#include "sb/info.h"
#include "sb/target.h"
#include "sb/hist.h"
#include "sb/runs.h"
#include "sb/compute.h"

#ifndef SB_VERSION_STR
#define SB_VERSION_STR "0.1.0"
#endif

const char *sb_version(void) { return SB_VERSION_STR; }
const char *sb_data_directory(void) { return sb_data_dir(); }
int sb_set_data_directory(const char *path) { return sb_set_data_dir(path); }
void sb_free(void *p) { free(p); }

static double rnd(double v, int digits) {
    double m = pow(10.0, digits);
    return floor(v * m + 0.5) / m;
}

static int valid_id(const char *id) {
    size_t n = strlen(id);
    if (n < 1 || n > 64) return 0;
    for (size_t i = 0; i < n; i++) {
        char c = id[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-')) return 0;
    }
    return 1;
}

/* ---- JSON-Bausteine für sb-result/1 ----------------------------------------- */

static cJSON *lat_obj(const sb_hist *h) {
    cJSON *o = cJSON_CreateObject();
    int empty = h->count == 0;
    cJSON_AddNumberToObject(o, "min", empty ? 0 : rnd((double)h->min_ns / 1000.0, 3));
    cJSON_AddNumberToObject(o, "mean", rnd(sb_hist_mean(h) / 1000.0, 3));
    cJSON_AddNumberToObject(o, "max", rnd((double)h->max_ns / 1000.0, 3));
    cJSON_AddNumberToObject(o, "stddev", rnd(sb_hist_stddev(h) / 1000.0, 3));
    static const struct { const char *k; double p; } P[] = {
        {"p50", 50}, {"p90", 90}, {"p95", 95}, {"p99", 99}, {"p99_9", 99.9}, {"p99_99", 99.99}};
    for (int i = 0; i < 6; i++) cJSON_AddNumberToObject(o, P[i].k, rnd(sb_hist_percentile(h, P[i].p) / 1000.0, 3));
    return o;
}

/* Histogramm: 32 Buckets pro Zweierpotenz (je 4 interne Unter-Buckets), nur belegte */
static cJSON *hist_arr(const sb_hist *h) {
    cJSON *a = cJSON_CreateArray();
    uint64_t c = 0;
    for (uint32_t i = 0; i < 256; i++) c += h->b[i];
    if (c) {
        cJSON *o = cJSON_CreateObject();
        cJSON_AddNumberToObject(o, "le_us", 0.255);
        cJSON_AddNumberToObject(o, "count", (double)c);
        cJSON_AddItemToArray(a, o);
    }
    for (uint32_t i = 256; i < SB_HIST_BUCKETS; i += 4) {
        c = h->b[i] + h->b[i + 1] + h->b[i + 2] + h->b[i + 3];
        if (i + 4 >= SB_HIST_BUCKETS) c -= h->overflow;
        if (!c) continue;
        cJSON *o = cJSON_CreateObject();
        cJSON_AddNumberToObject(o, "le_us", (double)sb_hist_bucket_hi(i + 3) / 1000.0);
        cJSON_AddNumberToObject(o, "count", (double)c);
        cJSON_AddItemToArray(a, o);
    }
    if (h->overflow) {
        cJSON *o = cJSON_CreateObject();
        cJSON_AddNullToObject(o, "le_us");
        cJSON_AddNumberToObject(o, "count", (double)h->overflow);
        cJSON_AddItemToArray(a, o);
    }
    return a;
}

static cJSON *storage_test_json(int idx, const sb_test *t, const sb_storage_result *r) {
    cJSON *o = cJSON_CreateObject();
    double d = r->measured_s > 0 ? r->measured_s : 0;
    cJSON_AddNumberToObject(o, "index", idx);
    cJSON_AddStringToObject(o, "type", "storage");
    cJSON_AddItemToObject(o, "config", cJSON_Duplicate(t->raw, 1));
    cJSON *s = cJSON_AddObjectToObject(o, "summary");
#define RATE(x) (d > 0 ? (double)(x) / d : 0.0)
    const sb_io_counts *c = &r->total;
    cJSON_AddNumberToObject(s, "iops", rnd(RATE(c->ops_r + c->ops_w), 1));
    cJSON_AddNumberToObject(s, "mbps", rnd(RATE(c->bytes_r + c->bytes_w) / 1e6, 2));
    cJSON_AddNumberToObject(s, "read_iops", rnd(RATE(c->ops_r), 1));
    cJSON_AddNumberToObject(s, "write_iops", rnd(RATE(c->ops_w), 1));
    cJSON_AddNumberToObject(s, "read_mbps", rnd(RATE(c->bytes_r) / 1e6, 2));
    cJSON_AddNumberToObject(s, "write_mbps", rnd(RATE(c->bytes_w) / 1e6, 2));
    cJSON_AddItemToObject(s, "lat_us", lat_obj(r->hist));
    cJSON_AddNumberToObject(s, "errors", (double)c->errors);
    cJSON_AddNumberToObject(s, "io_count", (double)(c->ops_r + c->ops_w));
    cJSON *ta = cJSON_AddArrayToObject(o, "targets");
    for (int k = 0; k < t->ntargets; k++) {
        cJSON *x = cJSON_CreateObject();
        cJSON_AddNumberToObject(x, "index", k);
        cJSON_AddStringToObject(x, "path", t->targets[k].path ? t->targets[k].path : "");
        cJSON_AddStringToObject(x, "kind", sb_target_type_name(t->targets[k].type));
        cJSON_AddBoolToObject(x, "created", r->t_created && k < r->ntargets && r->t_created[k]);
        cJSON_AddBoolToObject(x, "deleted", r->t_deleted && k < r->ntargets && r->t_deleted[k]);
        cJSON_AddItemToArray(ta, x);
    }
    cJSON *pw = cJSON_AddArrayToObject(o, "per_worker");
    for (int i = 0; i < r->nworkers; i++) {
        const sb_io_counts *w = &r->w_counts[i];
        cJSON *x = cJSON_CreateObject();
        cJSON_AddNumberToObject(x, "id", i);
        cJSON_AddNumberToObject(x, "target", r->worker_target[i]);
        cJSON_AddNumberToObject(x, "iops", rnd(RATE(w->ops_r + w->ops_w), 1));
        cJSON_AddNumberToObject(x, "mbps", rnd(RATE(w->bytes_r + w->bytes_w) / 1e6, 2));
        cJSON_AddNumberToObject(x, "read_iops", rnd(RATE(w->ops_r), 1));
        cJSON_AddNumberToObject(x, "write_iops", rnd(RATE(w->ops_w), 1));
        cJSON_AddItemToObject(x, "lat_us", lat_obj(r->w_hist[i]));
        cJSON_AddNumberToObject(x, "io_count", (double)(w->ops_r + w->ops_w));
        cJSON_AddNumberToObject(x, "errors", (double)w->errors);
        cJSON_AddItemToArray(pw, x);
    }
    cJSON *ps = cJSON_AddArrayToObject(o, "per_spec");
    for (int k = 0; k < r->nspecs; k++) {
        const sb_io_counts *sc = &r->s_counts[k];
        const sb_spec *sp = &t->specs[k];
        cJSON *x = cJSON_CreateObject();
        cJSON_AddNumberToObject(x, "spec", k);
        cJSON_AddNumberToObject(x, "weight", sp->weight);
        cJSON_AddNumberToObject(x, "block_size_bytes", (double)sp->block_size);
        cJSON_AddNumberToObject(x, "read_pct", sp->read_pct);
        cJSON_AddNumberToObject(x, "random_pct", sp->random_pct);
        cJSON_AddNumberToObject(x, "iops", rnd(RATE(sc->ops_r + sc->ops_w), 1));
        cJSON_AddNumberToObject(x, "mbps", rnd(RATE(sc->bytes_r + sc->bytes_w) / 1e6, 2));
        cJSON_AddItemToObject(x, "lat_us", lat_obj(r->s_hist[k]));
        cJSON_AddNumberToObject(x, "io_count", (double)(sc->ops_r + sc->ops_w));
        cJSON_AddItemToArray(ps, x);
    }
#undef RATE
    cJSON_AddItemToObject(o, "histogram", hist_arr(r->hist));
    cJSON *ts = cJSON_AddArrayToObject(o, "timeseries");
    for (int i = 0; i < r->nticks; i++) {
        const sb_tick_sample *k = &r->ticks[i];
        cJSON *x = cJSON_CreateObject();
        cJSON_AddNumberToObject(x, "t", rnd(k->t, 3));
        cJSON_AddNumberToObject(x, "iops", rnd(k->iops, 1));
        cJSON_AddNumberToObject(x, "mbps", rnd(k->mbps, 2));
        cJSON *l = cJSON_AddObjectToObject(x, "lat_us");
        cJSON_AddNumberToObject(l, "mean", rnd(k->lat_mean_us, 3));
        cJSON_AddNumberToObject(l, "p99", rnd(k->lat_p99_us, 3));
        cJSON_AddNumberToObject(l, "max", rnd(k->lat_max_us, 3));
        cJSON_AddNumberToObject(x, "cpu_pct", rnd(k->cpu_pct, 1));
        cJSON_AddItemToArray(ts, x);
    }
    cJSON *er = cJSON_AddArrayToObject(o, "errors");
    for (int i = 0; i < r->nerrs; i++) {
        const sb_run_error *e = &r->errs[i];
        cJSON *x = cJSON_CreateObject();
        cJSON_AddStringToObject(x, "code", e->code);
        cJSON_AddStringToObject(x, "msg", e->msg);
        if (e->t > 0) cJSON_AddNumberToObject(x, "t", rnd(e->t, 3));
        if (e->worker >= 0) cJSON_AddNumberToObject(x, "worker", e->worker);
        if (e->target >= 0) cJSON_AddNumberToObject(x, "target", e->target);
        if (e->count > 1) cJSON_AddNumberToObject(x, "count", (double)e->count);
        cJSON_AddItemToArray(er, x);
    }
    return o;
}

/* cpu/memory-Test (M2) */
static const char *ck_key(sb_compute_kind k) { return k == SB_CK_CPU ? "ops_per_s" : k == SB_CK_MEM_BW ? "gbps" : "latency_ns"; }
static int ck_digits(sb_compute_kind k) { return k == SB_CK_CPU ? 1 : 3; }

static cJSON *compute_test_json(int idx, const sb_test *t, const sb_compute_result *r) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "index", idx);
    cJSON_AddStringToObject(o, "type", sb_test_type_name(t->type));
    cJSON_AddItemToObject(o, "config", cJSON_Duplicate(t->raw, 1));
    cJSON *s = cJSON_AddObjectToObject(o, "summary");
    const char *key = ck_key(r->kind);
    int dg = ck_digits(r->kind);
    if (r->kind == SB_CK_CPU) {
        cJSON_AddStringToObject(s, "workload", sb_cpu_workload_name((sb_cpu_workload)t->workload));
        cJSON_AddNumberToObject(s, "threads", r->nthreads > 0 ? r->nthreads : 1);
        cJSON_AddNumberToObject(s, "ops_per_s", rnd(r->value, 1));
        cJSON_AddNumberToObject(s, "score", rnd(r->score, 1));
        cJSON_AddNumberToObject(s, "ops", (double)r->units);
    } else {
        cJSON_AddStringToObject(s, "mode", t->mem_mode == 1 ? "latency" : "bandwidth");
        if (t->mem_mode == 0) {
            static const char *const OP[] = {"read", "write", "copy"};
            cJSON_AddStringToObject(s, "op", OP[t->mem_op]);
        }
        cJSON_AddNumberToObject(s, "threads", r->nthreads > 0 ? r->nthreads : 1);
        cJSON_AddNumberToObject(s, "buffer_size_bytes", (double)(r->buffer_bytes ? r->buffer_bytes : t->buffer_size));
        if (t->mem_mode == 0) {
            cJSON_AddNumberToObject(s, "gbps", rnd(r->value, 3));
            cJSON_AddStringToObject(s, "simd", sb_simd_name(r->simd));
            cJSON_AddBoolToObject(s, "nontemporal", r->nontemporal);
            cJSON_AddNumberToObject(s, "bytes", (double)r->units);
        } else {
            cJSON_AddNumberToObject(s, "latency_ns", rnd(r->value, 2));
            cJSON_AddNumberToObject(s, "loads", (double)r->units);
        }
        if (t->mem_node >= 0) cJSON_AddNumberToObject(s, "mem_node", t->mem_node);
    }
    if (t->numa_node >= 0) cJSON_AddNumberToObject(s, "numa_node", t->numa_node);
    if (r->nthreads > 0) {
        cJSON *cp = cJSON_AddArrayToObject(s, "cpus");
        for (int i = 0; i < r->nthreads; i++) cJSON_AddItemToArray(cp, cJSON_CreateNumber(r->cpus[i]));
    }
    cJSON_AddNumberToObject(s, "cpu_pct", rnd(r->cpu_pct, 1));
    cJSON *pw = cJSON_AddArrayToObject(o, "per_worker");
    for (int i = 0; i < r->nthreads; i++) {
        cJSON *x = cJSON_CreateObject();
        cJSON_AddNumberToObject(x, "id", i);
        cJSON_AddNumberToObject(x, "cpu", r->cpus[i]);
        cJSON_AddNumberToObject(x, "node", r->nodes[i]);
        if (r->kind != SB_CK_CPU) cJSON_AddNumberToObject(x, "mem_node", r->mem_nodes[i]);
        cJSON_AddNumberToObject(x, key, rnd(r->w_value[i], dg));
        cJSON_AddItemToArray(pw, x);
    }
    cJSON *ts = cJSON_AddArrayToObject(o, "timeseries");
    for (int i = 0; i < r->nticks; i++) {
        cJSON *x = cJSON_CreateObject();
        cJSON_AddNumberToObject(x, "t", rnd(r->ticks[i].t, 3));
        cJSON_AddNumberToObject(x, key, rnd(r->ticks[i].value, dg));
        cJSON_AddNumberToObject(x, "cpu_pct", rnd(r->ticks[i].cpu_pct, 1));
        cJSON_AddItemToArray(ts, x);
    }
    cJSON *er = cJSON_AddArrayToObject(o, "errors");
    for (int i = 0; i < r->nerrs; i++) {
        cJSON *x = cJSON_CreateObject();
        cJSON_AddStringToObject(x, "code", r->errs[i].code);
        cJSON_AddStringToObject(x, "msg", r->errs[i].msg);
        if (r->errs[i].worker >= 0) cJSON_AddNumberToObject(x, "worker", r->errs[i].worker);
        cJSON_AddItemToArray(er, x);
    }
    return o;
}

/* ---- Lauf-Kontext (Callbacks) --------------------------------------------- */

typedef struct runctx { sb_progress *pr; sb_log *log; } runctx;

static void ev_phase(void *c, int test, const char *name, double duration_s) {
    runctx *r = (runctx *)c;
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "ev", "phase");
    cJSON_AddNumberToObject(o, "test", test);
    cJSON_AddStringToObject(o, "name", name);
    if (duration_s >= 0) cJSON_AddNumberToObject(o, "duration_s", rnd(duration_s, 3));
    sb_progress_emit(r->pr, o);
    cJSON_Delete(o);
    sb_log_write(r->log, "INFO", "Test %d: Phase %s", test, name);
}

static void ev_progress(void *c, int test, const char *phase, double done, double total, const char *unit) {
    runctx *r = (runctx *)c;
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "ev", "progress");
    cJSON_AddNumberToObject(o, "test", test);
    cJSON_AddStringToObject(o, "phase", phase);
    cJSON_AddNumberToObject(o, "done", rnd(done, 3));
    cJSON_AddNumberToObject(o, "total", rnd(total, 3));
    cJSON_AddStringToObject(o, "unit", unit);
    sb_progress_emit(r->pr, o);
    cJSON_Delete(o);
}

static void ev_tick(void *c, int test, const sb_tick_sample *s) {
    runctx *r = (runctx *)c;
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "ev", "tick");
    cJSON_AddNumberToObject(o, "test", test);
    cJSON_AddNumberToObject(o, "t", rnd(s->t, 3));
    cJSON_AddNumberToObject(o, "iops", rnd(s->iops, 1));
    cJSON_AddNumberToObject(o, "mbps", rnd(s->mbps, 2));
    cJSON *l = cJSON_AddObjectToObject(o, "lat_us");
    cJSON_AddNumberToObject(l, "mean", rnd(s->lat_mean_us, 3));
    cJSON_AddNumberToObject(l, "p50", rnd(s->lat_p50_us, 3));
    cJSON_AddNumberToObject(l, "p99", rnd(s->lat_p99_us, 3));
    cJSON_AddNumberToObject(l, "max", rnd(s->lat_max_us, 3));
    cJSON_AddNumberToObject(o, "read_iops", rnd(s->read_iops, 1));
    cJSON_AddNumberToObject(o, "write_iops", rnd(s->write_iops, 1));
    cJSON_AddNumberToObject(o, "read_mbps", rnd(s->read_mbps, 2));
    cJSON_AddNumberToObject(o, "write_mbps", rnd(s->write_mbps, 2));
    cJSON_AddNumberToObject(o, "cpu_pct", rnd(s->cpu_pct, 1));
    cJSON_AddNumberToObject(o, "proc_cpu_pct", rnd(s->proc_cpu_pct, 2));
    cJSON *w = cJSON_AddArrayToObject(o, "workers");
    for (int i = 0; i < s->nworkers; i++) {
        cJSON *x = cJSON_CreateObject();
        cJSON_AddNumberToObject(x, "id", i);
        cJSON_AddNumberToObject(x, "iops", rnd(s->w_iops[i], 1));
        cJSON_AddNumberToObject(x, "mbps", rnd(s->w_mbps[i], 2));
        if (s->w_lat_mean_us && s->w_lat_mean_us[i] >= 0) {
            cJSON *wl = cJSON_AddObjectToObject(x, "lat_us");
            cJSON_AddNumberToObject(wl, "mean", rnd(s->w_lat_mean_us[i], 3));
            cJSON_AddNumberToObject(wl, "p99", rnd(s->w_lat_p99_us[i], 3));
        }
        cJSON_AddItemToArray(w, x);
    }
    sb_progress_emit(r->pr, o);
    cJSON_Delete(o);
}

static void ev_ctick(void *c, int test, int kind, const sb_ctick *s) {
    runctx *r = (runctx *)c;
    sb_compute_kind k = (sb_compute_kind)kind;
    const char *key = ck_key(k);
    int dg = ck_digits(k);
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "ev", "tick");
    cJSON_AddNumberToObject(o, "test", test);
    cJSON_AddNumberToObject(o, "t", rnd(s->t, 3));
    cJSON_AddNumberToObject(o, key, rnd(s->value, dg));
    cJSON_AddNumberToObject(o, "cpu_pct", rnd(s->cpu_pct, 1));
    cJSON_AddNumberToObject(o, "proc_cpu_pct", rnd(s->proc_cpu_pct, 2));
    if (k != SB_CK_MEM_LAT && s->w_value) {
        cJSON *w = cJSON_AddArrayToObject(o, "workers");
        for (int i = 0; i < s->nworkers; i++) {
            cJSON *x = cJSON_CreateObject();
            cJSON_AddNumberToObject(x, "id", i);
            cJSON_AddNumberToObject(x, key, rnd(s->w_value[i], dg));
            cJSON_AddItemToArray(w, x);
        }
    }
    sb_progress_emit(r->pr, o);
    cJSON_Delete(o);
}

static void emit_error(sb_progress *pr, const char *code, const char *msg, const char *field, int test) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "ev", "error");
    cJSON_AddStringToObject(o, "code", code);
    cJSON_AddStringToObject(o, "msg", msg);
    if (field) cJSON_AddStringToObject(o, "field", field);
    if (test >= 0) cJSON_AddNumberToObject(o, "test", test);
    sb_progress_emit(pr, o);
    cJSON_Delete(o);
}

static void ev_error(void *c, int test, const char *code, const char *msg) {
    runctx *r = (runctx *)c;
    emit_error(r->pr, code, msg, NULL, test);
}

static void ev_log(void *c, const char *lvl, const char *msg) {
    runctx *r = (runctx *)c;
    sb_log_write(r->log, lvl, "%s", msg);
}

/* ---- Stop-Event ------------------------------------------------------------ */

static HANDLE g_stop_evt;

static BOOL WINAPI ctrl_handler(DWORD type) {
    if ((type == CTRL_C_EVENT || type == CTRL_BREAK_EVENT || type == CTRL_CLOSE_EVENT) && g_stop_evt) {
        SetEvent(g_stop_evt);
        return TRUE;
    }
    return FALSE;
}

/* Event mit DACL, die auch nicht erhöhten Prozessen (GUI) das Setzen erlaubt:
 * Vollzugriff SYSTEM/Administratoren/Ersteller, MODIFY_STATE|SYNCHRONIZE für
 * authentifizierte Benutzer; Integritätslabel Low, damit eine Medium-GUI ein von
 * einem erhöhten sbench angelegtes Event setzen darf. */
static HANDLE create_stop_event(const char *id, char *used_name, size_t n, int *exists, sb_log *log) {
    SECURITY_ATTRIBUTES sa;
    PSECURITY_DESCRIPTOR sd = NULL;
    memset(&sa, 0, sizeof sa);
    sa.nLength = sizeof sa;
    if (ConvertStringSecurityDescriptorToSecurityDescriptorW(
            L"D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;OW)(A;;0x00100002;;;AU)S:(ML;;NW;;;LW)", SDDL_REVISION_1, &sd, NULL))
        sa.lpSecurityDescriptor = sd;
    const char *pref[2] = {"Global\\", "Local\\"};
    HANDLE h = NULL;
    *exists = 0;
    for (int i = 0; i < 2 && !h; i++) {
        char nm[128];
        snprintf(nm, sizeof nm, "%ssbench-stop-%s", pref[i], id);
        wchar_t *w = sb_u8_to_w(nm);
        h = CreateEventW(sa.lpSecurityDescriptor ? &sa : NULL, TRUE, FALSE, w);
        DWORD e = GetLastError();
        if (!h && sa.lpSecurityDescriptor) {   /* Label setzen nicht erlaubt? ohne SD versuchen */
            h = CreateEventW(NULL, TRUE, FALSE, w);
            e = GetLastError();
        }
        free(w);
        if (h) {
            if (e == ERROR_ALREADY_EXISTS) *exists = 1;
            snprintf(used_name, n, "%s", nm);
        } else {
            sb_log_winerr(log, e, "Stop-Event %s nicht anlegbar", nm);
        }
    }
    if (sd) LocalFree(sd);
    return h;
}

int sb_stop(const char *id, char *msg, size_t msglen) {
    if (!id || !valid_id(id)) { snprintf(msg, msglen, "Ungültige ID."); return SB_EXIT_INVALID; }
    const char *pref[2] = {"Global\\", "Local\\"};
    DWORD last = 0;
    for (int i = 0; i < 2; i++) {
        char nm[128];
        snprintf(nm, sizeof nm, "%ssbench-stop-%s", pref[i], id);
        wchar_t *w = sb_u8_to_w(nm);
        HANDLE h = OpenEventW(EVENT_MODIFY_STATE, FALSE, w);
        free(w);
        if (h) {
            BOOL ok = SetEvent(h);
            CloseHandle(h);
            if (ok) { snprintf(msg, msglen, "Stop-Signal an %s gesendet.", nm); return SB_EXIT_OK; }
        }
        last = GetLastError();
    }
    snprintf(msg, msglen, "Kein laufender sbench-Lauf mit ID '%s' gefunden (%s).", id, sb_errtext(last));
    return SB_EXIT_ERROR;
}

/* ---- Validierung ------------------------------------------------------------- */

/* Schreibtests auf volume/physical brauchen allow_destructive im Target (prüft job.c)
 * UND --allow-destructive beim Aufruf von run. */
static void check_destructive(const sb_job *job, int allow, sb_verrs *e) {
    if (allow) return;
    for (int i = 0; i < job->ntests; i++) {
        const sb_test *t = &job->tests[i];
        if (t->type != SB_T_STORAGE || !sb_test_writes(t)) continue;
        for (int k = 0; k < t->ntargets; k++)
            if (t->targets[k].type != SB_TGT_FILE && t->targets[k].allow_destructive) {
                char p[96];
                snprintf(p, sizeof p, "/tests/%d/targets/%d/allow_destructive", i, k);
                sb_verrs_add(e, p, "E_DESTRUCTIVE_NOT_ALLOWED",
                             "Schreibtest auf %s %s zerstört Daten und ist nur mit --allow-destructive erlaubt.",
                             sb_target_type_name(t->targets[k].type), t->targets[k].path);
            }
    }
}

static void add_verrs(cJSON *o, const char *key, const sb_verrs *e) {
    cJSON *a = cJSON_AddArrayToObject(o, key);
    for (int i = 0; e && i < e->n; i++) {
        cJSON *x = cJSON_CreateObject();
        cJSON_AddStringToObject(x, "path", e->v[i].pointer);
        cJSON_AddStringToObject(x, "code", e->v[i].code);
        cJSON_AddStringToObject(x, "msg", e->v[i].msg);
        cJSON_AddItemToArray(a, x);
    }
}

int sb_validate(const char *job_path, char **json_out) {
    sb_job job;
    sb_verrs e = {0}, w = {0};
    int rc = sb_job_load_file(&job, job_path, &e, &w);
    cJSON *d = cJSON_CreateObject();
    cJSON_AddStringToObject(d, "schema", "sb-validate/1");
    cJSON_AddBoolToObject(d, "ok", e.n == 0);
    add_verrs(d, "errors", &e);
    add_verrs(d, "warnings", &w);
    *json_out = cJSON_Print(d);
    cJSON_Delete(d);
    int ret = rc == 1 ? SB_EXIT_ERROR : e.n ? SB_EXIT_INVALID : SB_EXIT_OK;
    sb_verrs_free(&e);
    sb_verrs_free(&w);
    sb_job_free(&job);
    return ret;
}

int sb_info(char **json_out) {
    cJSON *d = sb_info_document();
    *json_out = cJSON_Print(d);
    cJSON_Delete(d);
    return *json_out ? SB_EXIT_OK : SB_EXIT_ERROR;
}

/* ---- run ------------------------------------------------------------------------ */

static int write_json_atomic(const char *path, const cJSON *doc, sb_log *log) {
    char *txt = cJSON_Print(doc);
    if (!txt) return -1;
    char *tmp = sb_aprintf("%s.tmp", path);
    int rc = -1;
    if (tmp && sb_write_file(tmp, txt, strlen(txt)) == 0) {
        wchar_t *wt = sb_u8_to_w(tmp), *wp = sb_u8_to_w(path);
        if (wt && wp && MoveFileExW(wt, wp, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) rc = 0;
        else sb_log_winerr(log, GetLastError(), "Umbenennen %s -> %s fehlgeschlagen", tmp, path);
        free(wt); free(wp);
    } else {
        sb_log_winerr(log, GetLastError(), "Schreiben von %s fehlgeschlagen", tmp ? tmp : path);
    }
    free(tmp);
    cJSON_free(txt);
    return rc;
}

static char *full_path(const char *p) {
    wchar_t *w = sb_u8_to_w(p);
    if (!w) return sb_strdup(p);
    wchar_t buf[MAX_PATH * 4];
    DWORD n = GetFullPathNameW(w, MAX_PATH * 4, buf, NULL);
    free(w);
    if (!n || n >= MAX_PATH * 4) return sb_strdup(p);
    return sb_w_to_u8(buf);
}

static void on_pipe_broken(void *c, DWORD err) {
    sb_log_winerr((sb_log *)c, err, "Fortschritts-Pipe abgebrochen (GUI beendet?); Lauf wird fortgesetzt, Events nur noch in runs\\");
}

int sb_run(const sb_run_options *o) {
    char run_id[72], stop_name[128] = "", started[40], ended[40];
    sb_progress pr;
    sb_log log;
    char err[600];
    int rc = SB_EXIT_OK;
    sb_utc_iso_ms(started);

    if (o->id) {
        if (!valid_id(o->id)) {
            fprintf(stderr, "sbench: E_ARGS: ungültige --id (erlaubt: [A-Za-z0-9_-]{1,64})\n");
            return SB_EXIT_INVALID;
        }
        snprintf(run_id, sizeof run_id, "%s", o->id);
    } else {
        sb_new_run_id(run_id);
    }
    const char *dd = sb_data_dir();
    /* Läuft bereits ein Lauf mit dieser ID, dessen Dateien nicht anfassen */
    if (sb_runs_lock_alive(run_id)) {
        fprintf(stderr, "sbench: E_ID_IN_USE: Es läuft bereits ein sbench-Lauf mit der ID '%s'.\n", run_id);
        return SB_EXIT_INVALID;
    }
    char *log_path = o->log_path ? sb_strdup(o->log_path) : sb_aprintf("%s\\logs\\%s.log", dd, run_id);
    if (sb_log_open(&log, log_path)) fprintf(stderr, "sbench: Warnung: Log %s nicht schreibbar\n", log_path);
    sb_log_write(&log, "INFO", "sbench %s run, run_id=%s", sb_version(), run_id);
    if (o->cmdline) sb_log_write(&log, "INFO", "Aufruf: %s", o->cmdline);
    sb_log_write(&log, "INFO", "Datenverzeichnis: %s", dd);
    sb_log_write(&log, "INFO", "Job: %s, progress=%s, tick_ms=%u, keep_files=%d", o->job_path,
                 o->progress ? o->progress : "(keins)", o->tick_ms ? o->tick_ms : 1000, o->keep_files);

    char *out_path = o->out_path ? full_path(o->out_path) : sb_aprintf("%s\\results\\%s.json", dd, run_id);
    char *job_full = full_path(o->job_path);
    char *runs_file = sb_aprintf("%s\\runs\\%s.jsonl", dd, run_id);
    int pipe_failed = 0;
    if (sb_progress_open(&pr, o->progress, err, sizeof err)) {
        pipe_failed = 1;
        sb_progress_close(&pr);
        sb_progress_open(&pr, NULL, NULL, 0);
    }
    pr.on_broken = on_pipe_broken;
    pr.ctx = &log;
    {
        char merr[400];
        if (!runs_file || sb_progress_mirror(&pr, runs_file, merr, sizeof merr))
            sb_log_write(&log, "WARN", "%s", runs_file ? merr : "kein Speicher");
    }
    char *job_name = sb_runs_job_name(o->job_path);
    int locked = sb_runs_write_lock(run_id, job_name, started, job_full ? job_full : o->job_path, out_path ? out_path : "") == 0;
    if (!locked) sb_log_winerr(&log, GetLastError(), "Lock-Datei in %s\\runs nicht schreibbar", dd);

    sb_job job;
    memset(&job, 0, sizeof job);
    sb_verrs ve = {0};
    if (pipe_failed) {
        sb_log_write(&log, "ERROR", "E_PIPE_CONNECT: %s", err);
        fprintf(stderr, "sbench: E_PIPE_CONNECT: %s\n", err);
        emit_error(&pr, "E_PIPE_CONNECT", err, NULL, -1);
        rc = SB_EXIT_ERROR;
        goto out_nojob;
    }

    int jrc = sb_job_load_file(&job, o->job_path, &ve, NULL);
    if (jrc == 0) check_destructive(&job, o->allow_destructive, &ve);
    if (jrc != 0 || ve.n) {
        for (int i = 0; i < ve.n; i++) {
            emit_error(&pr, jrc == 1 ? "E_IO" : ve.v[i].code, ve.v[i].msg, jrc == 1 ? NULL : ve.v[i].pointer, -1);
            sb_log_write(&log, "ERROR", "Job ungültig: %s %s: %s", ve.v[i].code, ve.v[i].pointer, ve.v[i].msg);
            fprintf(stderr, "sbench: %s %s: %s\n", ve.v[i].code, ve.v[i].pointer, ve.v[i].msg);
        }
        rc = jrc == 1 ? SB_EXIT_ERROR : SB_EXIT_INVALID;
        goto out_nojob;
    }
    if (o->tick_ms && o->tick_ms < 100) {
        emit_error(&pr, "E_ARGS", "--tick-ms muss mindestens 100 sein.", NULL, -1);
        fprintf(stderr, "sbench: E_ARGS: --tick-ms muss mindestens 100 sein.\n");
        rc = SB_EXIT_INVALID;
        goto out_nojob;
    }

    /* Rechte vorab prüfen: volume/physical brauchen Administratorrechte */
    if (!sb_is_admin()) {
        for (int i = 0; i < job.ntests; i++) {
            const sb_test *t = &job.tests[i];
            if (t->type != SB_T_STORAGE) continue;
            for (int k = 0; k < t->ntargets; k++)
                if (t->targets[k].type != SB_TGT_FILE) {
                    char f[64];
                    snprintf(f, sizeof f, "/tests/%d/targets/%d", i, k);
                    snprintf(err, sizeof err, "Ziel %s (%s) erfordert Administratorrechte. sbench als Administrator starten.",
                             t->targets[k].path, sb_target_type_name(t->targets[k].type));
                    emit_error(&pr, "E_ACCESS_DENIED", err, f, i);
                    sb_log_write(&log, "ERROR", "%s", err);
                    fprintf(stderr, "sbench: %s\n", err);
                    rc = SB_EXIT_RIGHTS;
                }
        }
        if (rc) goto out_nojob;
    }

    /* Job-Kopie für Bundle/Nachvollziehbarkeit */
    {
        char *jp = sb_aprintf("%s\\jobs\\%s.json", dd, run_id);
        char *jt = cJSON_Print(job.root);
        if (jp && jt && sb_write_file(jp, jt, strlen(jt))) sb_log_winerr(&log, GetLastError(), "Job-Kopie %s", jp);
        free(jp);
        cJSON_free(jt);
    }

    int exists = 0;
    g_stop_evt = create_stop_event(run_id, stop_name, sizeof stop_name, &exists, &log);
    if (exists) {
        snprintf(err, sizeof err, "Es läuft bereits ein sbench-Lauf mit der ID '%s'.", run_id);
        emit_error(&pr, "E_ID_IN_USE", err, NULL, -1);
        sb_log_write(&log, "ERROR", "%s", err);
        fprintf(stderr, "sbench: E_ID_IN_USE: %s\n", err);
        CloseHandle(g_stop_evt); g_stop_evt = NULL;
        rc = SB_EXIT_INVALID;
        goto out_nojob;
    }
    if (g_stop_evt) sb_log_write(&log, "INFO", "Stop-Event: %s", stop_name);
    else sb_log_write(&log, "WARN", "Kein Stop-Event verfügbar; Abbruch nur per Strg+C");
    SetConsoleCtrlHandler(ctrl_handler, TRUE);

    sb_log_write(&log, "INFO", "Ergebnisdatei: %s", out_path);
    {
        cJSON *ev = cJSON_CreateObject();
        cJSON_AddStringToObject(ev, "ev", "start");
        cJSON_AddStringToObject(ev, "run_id", run_id);
        cJSON_AddNumberToObject(ev, "tests", job.ntests);
        sb_progress_emit(&pr, ev);
        cJSON_Delete(ev);
    }
    sb_log_write(&log, "INFO", "Admin=%d, SeManageVolumePrivilege=%d", sb_is_admin(),
                 sb_enable_privilege(L"SeManageVolumePrivilege"));
    cJSON *meta = sb_info_meta();
    cJSON *tests = cJSON_CreateArray();
    runctx ctx = {&pr, &log};
    sb_storage_cb cb = {&ctx, ev_phase, ev_progress, ev_tick, ev_error, ev_log, ev_ctick};
    sb_storage_opts so = {o->tick_ms ? o->tick_ms : 1000, g_stop_evt, o->allow_destructive, o->keep_files};
    int aborted = 0, rights = 0;
    for (int i = 0; i < job.ntests && !aborted; i++) {
        const sb_test *t = &job.tests[i];
        if (g_stop_evt && WaitForSingleObject(g_stop_evt, 0) == WAIT_OBJECT_0) { aborted = 1; break; }
        if (t->type == SB_T_CPU || t->type == SB_T_MEMORY) {
            sb_log_write(&log, "INFO", "Test %d%s%s%s: %s, threads %u, warmup %.1f s, Dauer %.1f s, numa_node %d, mem_node %d", i,
                         t->name ? " (" : "", t->name ? t->name : "", t->name ? ")" : "", sb_test_type_name(t->type),
                         t->threads, t->warmup_s, t->duration_s, t->numa_node, t->mem_node);
            sb_compute_result cr;
            int crc = sb_compute_run(i, t, &so, &cb, &cr);
            if (cr.measured || crc == 3 || crc == 1) cJSON_AddItemToArray(tests, compute_test_json(i, t, &cr));
            sb_log_write(&log, "INFO", "Test %d beendet (rc=%d): %s = %.3f, %d Thread(s), %.2f s", i, crc, ck_key(cr.kind),
                         cr.value, cr.nthreads, cr.measured_s);
            sb_compute_result_free(&cr);
            if (crc == 3) { aborted = 1; rc = SB_EXIT_ABORTED; }
            else if (crc == 1) { aborted = 1; rc = SB_EXIT_ERROR; }
            continue;
        }
        if (t->type != SB_T_STORAGE) {
            snprintf(err, sizeof err, "Testtyp '%s' ist noch nicht implementiert und wird übersprungen.",
                     sb_test_type_name(t->type));
            emit_error(&pr, "E_NOT_IMPLEMENTED", err, NULL, i);
            sb_log_write(&log, "WARN", "Test %d: %s", i, err);
            continue;
        }
        sb_log_write(&log, "INFO", "Test %d%s%s%s: storage, %d Target(s), %u Worker, QD %u, warmup %.1f s, Dauer %.1f s", i,
                     t->name ? " (" : "", t->name ? t->name : "", t->name ? ")" : "",
                     t->ntargets, t->workers, t->queue_depth, t->warmup_s, t->duration_s);
        sb_storage_result res;
        int src = sb_storage_run(i, t, &so, &cb, &res);
        if (src == 4) { rights = 1; sb_storage_result_free(&res); break; }
        if (res.measured || src == 3 || src == 1) cJSON_AddItemToArray(tests, storage_test_json(i, t, &res));
        sb_log_write(&log, "INFO", "Test %d beendet (rc=%d): %.1f IOPS, %.2f MB/s, p99 %.1f us, %llu I/Os, %llu Fehler", i, src,
                     res.measured_s > 0 ? (double)(res.total.ops_r + res.total.ops_w) / res.measured_s : 0.0,
                     res.measured_s > 0 ? (double)(res.total.bytes_r + res.total.bytes_w) / res.measured_s / 1e6 : 0.0,
                     sb_hist_percentile(res.hist, 99) / 1000.0, (unsigned long long)(res.total.ops_r + res.total.ops_w),
                     (unsigned long long)res.total.errors);
        sb_storage_result_free(&res);
        if (src == 3) { aborted = 1; rc = SB_EXIT_ABORTED; }
        else if (src == 1) { aborted = 1; rc = SB_EXIT_ERROR; }
    }
    if (rights) {
        rc = SB_EXIT_RIGHTS;
        cJSON_Delete(meta); cJSON_Delete(tests);
        sb_log_write(&log, "ERROR", "Abbruch wegen fehlender Rechte (Exit 4), kein Ergebnis geschrieben");
    } else {
        if (!aborted && g_stop_evt && WaitForSingleObject(g_stop_evt, 0) == WAIT_OBJECT_0) { aborted = 1; rc = SB_EXIT_ABORTED; }
        sb_utc_iso_ms(ended);
        cJSON *doc = cJSON_CreateObject();
        cJSON_AddStringToObject(doc, "schema", "sb-result/1");
        cJSON_AddStringToObject(doc, "kern_version", sb_version());
        cJSON_AddStringToObject(doc, "run_id", run_id);
        cJSON_AddStringToObject(doc, "started_utc", started);
        cJSON_AddStringToObject(doc, "ended_utc", ended);
        cJSON_AddBoolToObject(doc, "aborted", aborted);
        cJSON_AddItemToObject(doc, "meta", meta);
        cJSON_AddItemToObject(doc, "tests", tests);
        if (write_json_atomic(out_path, doc, &log)) {
            snprintf(err, sizeof err, "Ergebnisdatei %s nicht schreibbar: %s", out_path, sb_errtext(GetLastError()));
            emit_error(&pr, "E_IO", err, NULL, -1);
            fprintf(stderr, "sbench: %s\n", err);
            rc = SB_EXIT_ERROR;
        } else {
            cJSON *ev = cJSON_CreateObject();
            cJSON_AddStringToObject(ev, "ev", "done");
            cJSON_AddStringToObject(ev, "result", out_path);
            cJSON_AddBoolToObject(ev, "aborted", aborted);
            sb_progress_emit(&pr, ev);
            cJSON_Delete(ev);
        }
        cJSON_Delete(doc);
        sb_log_write(&log, "INFO", "Ende: aborted=%d, Exit %d", aborted, rc);
    }
    SetConsoleCtrlHandler(ctrl_handler, FALSE);
    if (g_stop_evt) { CloseHandle(g_stop_evt); g_stop_evt = NULL; }
    goto out;

out_nojob:
    sb_log_write(&log, "INFO", "Ende ohne Messung, Exit %d", rc);
out:
    sb_utc_iso_ms(ended);
    if (sb_runs_index_append(run_id, started, ended, out_path ? out_path : "", rc))
        sb_log_winerr(&log, GetLastError(), "runs\\index.jsonl nicht schreibbar");
    if (locked) sb_runs_remove_lock(run_id);
    sb_verrs_free(&ve);
    sb_job_free(&job);
    sb_progress_close(&pr);
    sb_log_close(&log);
    free(log_path); free(out_path); free(job_full); free(runs_file); free(job_name);
    return rc;
}

/* sb-runs/1: laufende Läufe */
int sb_list_running(char **json_out) {
    cJSON *d = sb_runs_running();
    *json_out = cJSON_Print(d);
    cJSON_Delete(d);
    return *json_out ? SB_EXIT_OK : SB_EXIT_ERROR;
}
