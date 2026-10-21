/* Job-Parser und Validierung gegen sb-job/1 (Schemaregeln im Code) plus
 * semantische Regeln aus docs/cli.md. Fehler werden mit JSON Pointer gemeldet. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <math.h>
#include "sb/job.h"
#include "sb/util.h"
#include "sb/target.h"
#include "sb/topo.h"

void sb_verrs_add(sb_verrs *e, const char *pointer, const char *code, const char *fmt, ...) {
    if (e->n == e->cap) {
        int nc = e->cap ? e->cap * 2 : 16;
        sb_verr *nv = (sb_verr *)realloc(e->v, (size_t)nc * sizeof(sb_verr));
        if (!nv) return;
        e->v = nv; e->cap = nc;
    }
    sb_verr *v = &e->v[e->n++];
    snprintf(v->pointer, sizeof v->pointer, "%s", pointer);
    snprintf(v->code, sizeof v->code, "%s", code);
    va_list ap; va_start(ap, fmt); vsnprintf(v->msg, sizeof v->msg, fmt, ap); va_end(ap);
}

void sb_verrs_free(sb_verrs *e) { free(e->v); e->v = NULL; e->n = e->cap = 0; }

const char *sb_test_type_name(sb_test_type t) {
    switch (t) {
    case SB_T_STORAGE: return "storage";
    case SB_T_CPU: return "cpu";
    case SB_T_MEMORY: return "memory";
    case SB_T_NET_CLIENT: return "net_client";
    case SB_T_NET_SERVER: return "net_server";
    case SB_T_HYPERV_INFO: return "hyperv_info";
    default: return "unknown";
    }
}
const char *sb_target_type_name(sb_target_type t) {
    return t == SB_TGT_FILE ? "file" : t == SB_TGT_VOLUME ? "volume" : "physical";
}
const char *sb_cache_name(sb_cache_mode c) {
    return c == SB_CACHE_BUFFERED ? "buffered" : c == SB_CACHE_WRITETHROUGH ? "writethrough" : "unbuffered";
}

int sb_pick_spec(const uint32_t *cum, int n, uint64_t r) {
    /* cum[i] = Summe weight[0..i]; r in [0, cum[n-1]) */
    int lo = 0, hi = n - 1;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (r < cum[mid]) hi = mid; else lo = mid + 1;
    }
    return lo;
}

/* ---- kleine Schema-Helfer ------------------------------------------- */

typedef struct vctx { sb_verrs *e; char ptr[160]; } vctx;

static void P(char *out, size_t n, const char *base, const char *key) { snprintf(out, n, "%s/%s", base, key); }
static void PI(char *out, size_t n, const char *base, const char *key, int i) { snprintf(out, n, "%s/%s/%d", base, key, i); }

static void check_keys(sb_verrs *e, const cJSON *o, const char *ptr, const char *const *allowed) {
    for (const cJSON *c = o->child; c; c = c->next) {
        int ok = 0;
        for (const char *const *a = allowed; *a; a++) if (!strcmp(*a, c->string)) { ok = 1; break; }
        if (!ok) {
            char p[200]; P(p, sizeof p, ptr, c->string);
            sb_verrs_add(e, p, "E_SCHEMA", "Unbekanntes Feld '%s'.", c->string);
        }
    }
}

static const cJSON *req(sb_verrs *e, const cJSON *o, const char *ptr, const char *key) {
    const cJSON *c = cJSON_GetObjectItemCaseSensitive(o, key);
    if (!c) {
        sb_verrs_add(e, ptr, "E_SCHEMA", "Pflichtfeld '%s' fehlt.", key);
    }
    return c;
}

static int is_int(const cJSON *c) {
    if (!cJSON_IsNumber(c)) return 0;
    double d = c->valuedouble;
    return d == floor(d) && fabs(d) < 9.0e15;
}

/* Integer im Bereich; liefert 0 bei ok */
static int get_int(sb_verrs *e, const cJSON *o, const char *ptr, const char *key, int required,
                   long long lo, long long hi, long long def, long long *out) {
    const cJSON *c = cJSON_GetObjectItemCaseSensitive(o, key);
    *out = def;
    if (!c) { if (required) sb_verrs_add(e, ptr, "E_SCHEMA", "Pflichtfeld '%s' fehlt.", key); return required ? -1 : 0; }
    char p[200]; P(p, sizeof p, ptr, key);
    if (!is_int(c)) { sb_verrs_add(e, p, "E_SCHEMA", "'%s' muss eine ganze Zahl sein.", key); return -1; }
    long long v = (long long)c->valuedouble;
    if (v < lo || v > hi) {
        sb_verrs_add(e, p, "E_SCHEMA", "'%s' = %lld liegt außerhalb des erlaubten Bereichs %lld..%lld.", key, v, lo, hi);
        return -1;
    }
    *out = v;
    return 0;
}

/* Zahl; excl = 1 -> > lo, sonst >= lo */
static int get_num(sb_verrs *e, const cJSON *o, const char *ptr, const char *key, int required, double lo, int excl,
                   double def, double *out) {
    const cJSON *c = cJSON_GetObjectItemCaseSensitive(o, key);
    *out = def;
    if (!c) { if (required) sb_verrs_add(e, ptr, "E_SCHEMA", "Pflichtfeld '%s' fehlt.", key); return required ? -1 : 0; }
    char p[200]; P(p, sizeof p, ptr, key);
    if (!cJSON_IsNumber(c)) { sb_verrs_add(e, p, "E_SCHEMA", "'%s' muss eine Zahl sein.", key); return -1; }
    double v = c->valuedouble;
    if (excl ? !(v > lo) : !(v >= lo)) {
        sb_verrs_add(e, p, "E_SCHEMA", "'%s' muss %s %g sein.", key, excl ? "größer als" : "mindestens", lo);
        return -1;
    }
    *out = v;
    return 0;
}

static int get_enum(sb_verrs *e, const cJSON *o, const char *ptr, const char *key, int required,
                    const char *const *vals, int def) {
    const cJSON *c = cJSON_GetObjectItemCaseSensitive(o, key);
    if (!c) { if (required) sb_verrs_add(e, ptr, "E_SCHEMA", "Pflichtfeld '%s' fehlt.", key); return required ? -1 : def; }
    char p[200]; P(p, sizeof p, ptr, key);
    if (cJSON_IsString(c))
        for (int i = 0; vals[i]; i++) if (!strcmp(vals[i], c->valuestring)) return i;
    char list[200] = "";
    for (int i = 0; vals[i]; i++) { strcat(list, i ? ", " : ""); strcat(list, vals[i]); }
    sb_verrs_add(e, p, "E_SCHEMA", "'%s' muss einer der Werte %s sein.", key, list);
    return -1;
}

static int get_bool(sb_verrs *e, const cJSON *o, const char *ptr, const char *key) {
    const cJSON *c = cJSON_GetObjectItemCaseSensitive(o, key);
    if (!c) return 0;
    if (!cJSON_IsBool(c)) { char p[200]; P(p, sizeof p, ptr, key); sb_verrs_add(e, p, "E_SCHEMA", "'%s' muss true/false sein.", key); return -1; }
    return cJSON_IsTrue(c) ? 1 : 0;
}

static int get_str(sb_verrs *e, const cJSON *o, const char *ptr, const char *key, int required, const char **out) {
    const cJSON *c = cJSON_GetObjectItemCaseSensitive(o, key);
    *out = NULL;
    if (!c) { if (required) sb_verrs_add(e, ptr, "E_SCHEMA", "Pflichtfeld '%s' fehlt.", key); return required ? -1 : 0; }
    char p[200]; P(p, sizeof p, ptr, key);
    if (!cJSON_IsString(c) || !c->valuestring[0]) {
        sb_verrs_add(e, p, "E_SCHEMA", "'%s' muss eine nichtleere Zeichenkette sein.", key);
        return -1;
    }
    *out = c->valuestring;
    return 0;
}

/* Größe nach $defs/size: Integer >= 1 oder "^[1-9][0-9]*\s?(KiB|MiB|GiB)$" */
int sb_json_size(const cJSON *c, uint64_t *out) {
    if (is_int(c)) {
        if (c->valuedouble < 1) return -1;
        *out = (uint64_t)c->valuedouble;
        return 0;
    }
    if (!cJSON_IsString(c)) return -1;
    const char *s = c->valuestring;
    if (*s < '1' || *s > '9') return -1;
    uint64_t v = 0;
    while (*s >= '0' && *s <= '9') {
        if (v > (UINT64_MAX / 10) - 10) return -1;
        v = v * 10 + (uint64_t)(*s++ - '0');
    }
    if (*s == ' ') s++;
    uint64_t m;
    if (!strcmp(s, "KiB")) m = 1ULL << 10;
    else if (!strcmp(s, "MiB")) m = 1ULL << 20;
    else if (!strcmp(s, "GiB")) m = 1ULL << 30;
    else return -1;
    if (v > UINT64_MAX / m) return -1;
    *out = v * m;
    return 0;
}

static int get_size(sb_verrs *e, const cJSON *o, const char *ptr, const char *key, int required, uint64_t def,
                    uint64_t *out) {
    const cJSON *c = cJSON_GetObjectItemCaseSensitive(o, key);
    *out = def;
    if (!c) { if (required) sb_verrs_add(e, ptr, "E_SCHEMA", "Pflichtfeld '%s' fehlt.", key); return required ? -1 : 0; }
    if (sb_json_size(c, out)) {
        char p[200]; P(p, sizeof p, ptr, key);
        sb_verrs_add(e, p, "E_SCHEMA", "'%s' ist keine gültige Größe (Integer >= 1 oder z. B. \"4KiB\", \"16 GiB\").", key);
        return -1;
    }
    return 0;
}

static int match_volume_path(const char *p) {
    return strlen(p) == 6 && p[0] == '\\' && p[1] == '\\' && p[2] == '.' && p[3] == '\\' &&
           ((p[4] >= 'A' && p[4] <= 'Z') || (p[4] >= 'a' && p[4] <= 'z')) && p[5] == ':';
}
static int match_physical_path(const char *p) {
    if (strncmp(p, "\\\\.\\PhysicalDrive", 17)) return 0;
    const char *d = p + 17;
    if (!*d) return 0;
    for (; *d; d++) if (*d < '0' || *d > '9') return 0;
    return 1;
}

/* ---- Tests ------------------------------------------------------------ */

static const char *const K_TARGET[] = {"path", "kind", "file_size", "allow_destructive", NULL};
static const char *const K_SPEC[] = {"name", "weight", "block_size", "read_pct", "random_pct", "align", "burst_len", "delay_ms", NULL};
static const char *const K_STORAGE[] = {"type", "name", "targets", "workers", "queue_depth", "warmup_s", "duration_s", "cache", "access_specs", NULL};
static const char *const K_CPU[] = {"type", "name", "threads", "workload", "warmup_s", "duration_s", "numa_node", NULL};
static const char *const K_MEMORY[] = {"type", "name", "mode", "op", "threads", "buffer_size", "warmup_s", "duration_s",
                                       "numa_node", "mem_node", "nontemporal", "simd", NULL};
static const char *const K_NETC[] = {"type", "name", "host", "port", "proto", "streams", "duration_s", "udp_rate_mbps", "packet_size", "mode", "reverse", NULL};
static const char *const K_NETS[] = {"type", "name", "bind", "port", "proto", "once", NULL};
static const char *const K_HV[] = {"type", "name", "include_perfcounters", "sample_s", NULL};
static const char *const E_TYPES[] = {"storage", "cpu", "memory", "net_client", "net_server", "hyperv_info", NULL};
static const char *const E_KIND[] = {"file", "volume", "physical", NULL};
static const char *const E_CACHE[] = {"unbuffered", "buffered", "writethrough", NULL};

static void parse_storage(sb_test *t, const cJSON *o, const char *ptr, sb_verrs *e) {
    long long v;
    double d;
    check_keys(e, o, ptr, K_STORAGE);
    char p[200], q[200];
    const cJSON *tg = req(e, o, ptr, "targets");
    if (tg) {
        P(p, sizeof p, ptr, "targets");
        if (!cJSON_IsArray(tg) || cJSON_GetArraySize(tg) < 1)
            sb_verrs_add(e, p, "E_SCHEMA", "'targets' muss ein Array mit mindestens einem Eintrag sein.");
        else if (cJSON_GetArraySize(tg) > SB_MAX_TARGETS)
            sb_verrs_add(e, p, "E_SCHEMA", "Höchstens %d Targets pro Test.", SB_MAX_TARGETS);
        else {
            int i = 0;
            for (const cJSON *x = tg->child; x; x = x->next, i++) {
                PI(q, sizeof q, ptr, "targets", i);
                if (!cJSON_IsObject(x)) { sb_verrs_add(e, q, "E_SCHEMA", "Target muss ein Objekt sein."); continue; }
                check_keys(e, x, q, K_TARGET);
                sb_target *T = &t->targets[t->ntargets++];
                const char *path;
                get_str(e, x, q, "path", 1, &path);
                T->path = path ? sb_strdup(path) : NULL;
                int k = get_enum(e, x, q, "kind", 1, E_KIND, 0);
                T->type = k < 0 ? SB_TGT_FILE : (sb_target_type)k;
                int ad = get_bool(e, x, q, "allow_destructive");
                T->allow_destructive = ad > 0;
                const cJSON *fs = cJSON_GetObjectItemCaseSensitive(x, "file_size");
                if (k == SB_TGT_FILE) {
                    get_size(e, x, q, "file_size", 1, 0, &T->file_size);
                } else if (k > 0 && fs) {
                    char r[220]; P(r, sizeof r, q, "file_size");
                    sb_verrs_add(e, r, "E_SCHEMA", "'file_size' ist nur bei kind = file erlaubt.");
                }
                if (path && k == SB_TGT_VOLUME && !match_volume_path(path)) {
                    char r[220]; P(r, sizeof r, q, "path");
                    sb_verrs_add(e, r, "E_SCHEMA", "Volume-Pfad muss die Form \\\\.\\X: haben.");
                }
                if (path && k == SB_TGT_PHYSICAL && !match_physical_path(path)) {
                    char r[220]; P(r, sizeof r, q, "path");
                    sb_verrs_add(e, r, "E_SCHEMA", "Pfad einer physischen Disk muss die Form \\\\.\\PhysicalDriveN haben.");
                }
            }
        }
    }
    get_int(e, o, ptr, "workers", 0, 1, 4096, 1, &v); t->workers = (uint32_t)v;
    get_int(e, o, ptr, "queue_depth", 0, 1, 1024, 1, &v); t->queue_depth = (uint32_t)v;
    get_num(e, o, ptr, "warmup_s", 0, 0, 0, 0, &d); t->warmup_s = d;
    get_num(e, o, ptr, "duration_s", 1, 0, 1, 0, &d); t->duration_s = d;
    int c = get_enum(e, o, ptr, "cache", 0, E_CACHE, 0);
    t->cache = c < 0 ? SB_CACHE_UNBUFFERED : (sb_cache_mode)c;
    const cJSON *as = req(e, o, ptr, "access_specs");
    if (as) {
        P(p, sizeof p, ptr, "access_specs");
        if (!cJSON_IsArray(as) || cJSON_GetArraySize(as) < 1)
            sb_verrs_add(e, p, "E_SCHEMA", "'access_specs' muss ein Array mit mindestens einem Eintrag sein.");
        else if (cJSON_GetArraySize(as) > SB_MAX_SPECS)
            sb_verrs_add(e, p, "E_SCHEMA", "Höchstens %d Access-Specs pro Test.", SB_MAX_SPECS);
        else {
            int i = 0;
            for (const cJSON *x = as->child; x; x = x->next, i++) {
                PI(q, sizeof q, ptr, "access_specs", i);
                if (!cJSON_IsObject(x)) { sb_verrs_add(e, q, "E_SCHEMA", "Access-Spec muss ein Objekt sein."); continue; }
                check_keys(e, x, q, K_SPEC);
                sb_spec *S = &t->specs[t->nspecs++];
                get_str(e, x, q, "name", 0, &S->name);
                get_int(e, x, q, "weight", 1, 1, 100, 0, &v); S->weight = (uint32_t)v;
                get_size(e, x, q, "block_size", 1, 0, &S->block_size);
                get_int(e, x, q, "read_pct", 1, 0, 100, 0, &v); S->read_pct = (uint32_t)v;
                get_int(e, x, q, "random_pct", 1, 0, 100, 0, &v); S->random_pct = (uint32_t)v;
                get_size(e, x, q, "align", 0, 0, &S->align);
                if (!S->align) S->align = S->block_size;
                get_int(e, x, q, "burst_len", 0, 1, 1LL << 31, 1, &v); S->burst_len = (uint32_t)v;
                get_int(e, x, q, "delay_ms", 0, 0, 1LL << 31, 0, &v); S->delay_ms = (uint32_t)v;
                if (S->block_size > (1ULL << 30)) {
                    char r[220]; P(r, sizeof r, q, "block_size");
                    sb_verrs_add(e, r, "E_SCHEMA", "block_size darf höchstens 1 GiB sein.");
                }
            }
        }
    }
}

static void parse_other(sb_test *t, const cJSON *o, const char *ptr, sb_verrs *e) {
    long long v; double d; const char *s;
    switch (t->type) {
    case SB_T_CPU: {
        static const char *const W[] = {"int", "float", "crypto", "mixed", NULL};
        check_keys(e, o, ptr, K_CPU);
        get_int(e, o, ptr, "threads", 0, 0, 1 << 20, 0, &v);
        t->threads = (uint32_t)v;
        int wl = get_enum(e, o, ptr, "workload", 0, W, 3);
        t->workload = wl < 0 ? 3 : wl;
        get_num(e, o, ptr, "warmup_s", 0, 0, 0, 1, &t->warmup_s);
        get_num(e, o, ptr, "duration_s", 1, 0, 1, 0, &t->duration_s);
        get_int(e, o, ptr, "numa_node", 0, 0, 1023, -1, &v);
        t->numa_node = (int)v;
        t->mem_node = -1;
        break;
    }
    case SB_T_MEMORY: {
        static const char *const M[] = {"bandwidth", "latency", NULL};
        static const char *const OP[] = {"read", "write", "copy", NULL};
        static const char *const SI[] = {"auto", "scalar", "sse2", "avx2", "avx512", "neon", NULL};
        check_keys(e, o, ptr, K_MEMORY);
        int m = get_enum(e, o, ptr, "mode", 1, M, 0);
        t->mem_mode = m < 0 ? 0 : m;
        int op = get_enum(e, o, ptr, "op", 0, OP, 0);
        t->mem_op = op < 0 ? 0 : op;
        get_int(e, o, ptr, "threads", 0, 0, 1 << 20, 0, &v);
        t->threads = (uint32_t)v;
        get_size(e, o, ptr, "buffer_size", 1, 0, &t->buffer_size);
        get_num(e, o, ptr, "warmup_s", 0, 0, 0, 1, &t->warmup_s);
        get_num(e, o, ptr, "duration_s", 1, 0, 1, 0, &t->duration_s);
        get_int(e, o, ptr, "numa_node", 0, 0, 1023, -1, &v);
        t->numa_node = (int)v;
        get_int(e, o, ptr, "mem_node", 0, 0, 1023, -1, &v);
        t->mem_node = (int)v;
        int nt = get_bool(e, o, ptr, "nontemporal");
        t->nontemporal = nt > 0;
        int si = get_enum(e, o, ptr, "simd", 0, SI, 0);
        t->simd = si < 0 ? 0 : si;
        break;
    }
    case SB_T_NET_CLIENT: {
        static const char *const PR[] = {"tcp", "udp", NULL};
        static const char *const MO[] = {"throughput", "latency", NULL};
        uint64_t sz;
        check_keys(e, o, ptr, K_NETC);
        get_str(e, o, ptr, "host", 1, &s);
        get_int(e, o, ptr, "port", 0, 1, 65535, 5201, &v);
        int proto = get_enum(e, o, ptr, "proto", 0, PR, 0);
        get_int(e, o, ptr, "streams", 0, 1, 128, 1, &v);
        get_num(e, o, ptr, "duration_s", 1, 0, 1, 0, &d);
        double rate;
        int has_rate = cJSON_GetObjectItemCaseSensitive(o, "udp_rate_mbps") != NULL;
        get_num(e, o, ptr, "udp_rate_mbps", 0, 0, 1, 0, &rate);
        get_size(e, o, ptr, "packet_size", 0, 0, &sz);
        int mode = get_enum(e, o, ptr, "mode", 0, MO, 0);
        get_bool(e, o, ptr, "reverse");
        char p[200]; P(p, sizeof p, ptr, "udp_rate_mbps");
        if (proto == 0 && has_rate)
            sb_verrs_add(e, p, "E_UDP_RATE", "udp_rate_mbps ist bei proto = tcp nicht erlaubt.");
        if (proto == 1 && mode == 0 && !has_rate)
            sb_verrs_add(e, p, "E_UDP_RATE", "udp_rate_mbps ist beim UDP-Durchsatztest erforderlich.");
        break;
    }
    case SB_T_NET_SERVER: {
        static const char *const PR[] = {"tcp", "udp", NULL};
        check_keys(e, o, ptr, K_NETS);
        const cJSON *b = cJSON_GetObjectItemCaseSensitive(o, "bind");
        if (b && !cJSON_IsString(b)) { char p[200]; P(p, sizeof p, ptr, "bind"); sb_verrs_add(e, p, "E_SCHEMA", "'bind' muss eine Zeichenkette sein."); }
        get_int(e, o, ptr, "port", 0, 1, 65535, 5201, &v);
        get_enum(e, o, ptr, "proto", 0, PR, 0);
        get_bool(e, o, ptr, "once");
        break;
    }
    case SB_T_HYPERV_INFO:
        check_keys(e, o, ptr, K_HV);
        get_bool(e, o, ptr, "include_perfcounters");
        get_num(e, o, ptr, "sample_s", 0, 0, 1, 5, &d);
        break;
    default: break;
    }
}

/* Semantische Regeln für Storage (Gewichte, Sektorgröße, Ziele) */
int sb_test_writes(const sb_test *t) {
    for (int i = 0; i < t->nspecs; i++) if (t->specs[i].read_pct < 100) return 1;
    return 0;
}

static void semantic_storage(sb_test *t, const char *ptr, sb_verrs *e, sb_verrs *w, int weight_done) {
    char p[200], q[220];
    int writes = sb_test_writes(t);
    int admin = sb_is_admin();
    if (t->nspecs && !weight_done) {
        uint32_t sum = 0;
        for (int i = 0; i < t->nspecs; i++) sum += t->specs[i].weight;
        if (sum != 100) {
            P(p, sizeof p, ptr, "access_specs");
            sb_verrs_add(e, p, "E_WEIGHT_SUM", "Summe der Gewichte ist %u, erwartet 100.", sum);
        }
    }
    uint64_t max_bs = 0;
    for (int i = 0; i < t->nspecs; i++) if (t->specs[i].block_size > max_bs) max_bs = t->specs[i].block_size;
    for (int k = 0; k < t->ntargets; k++) {
        sb_target *T = &t->targets[k];
        if (!T->path) continue;
        PI(p, sizeof p, ptr, "targets", k);
        if (T->type != SB_TGT_FILE) {
            if (writes && !T->allow_destructive) {
                snprintf(q, sizeof q, "%s/allow_destructive", p);
                sb_verrs_add(e, q, "E_DESTRUCTIVE_NOT_ALLOWED",
                             "Schreibtest auf %s %s ohne allow_destructive: true. Die Daten auf dem Ziel würden zerstört.",
                             sb_target_type_name(T->type), T->path);
            } else if (writes && w) {
                snprintf(q, sizeof q, "%s/allow_destructive", p);
                sb_verrs_add(w, q, "W_DESTRUCTIVE",
                             "Schreibtest auf %s %s: alle Daten auf dem Ziel werden zerstört; sbench run braucht zusätzlich --allow-destructive.",
                             sb_target_type_name(T->type), T->path);
            }
            if (!admin && w)
                sb_verrs_add(w, p, "W_NEEDS_ADMIN", "%s %s erfordert Administratorrechte; run würde mit Exit-Code 4 enden.",
                             T->type == SB_TGT_VOLUME ? "Volume" : "Physische Disk", T->path);
        }
        sb_target_probe pr;
        sb_target_probe_path(T, &pr);
        if (!pr.exists) {
            snprintf(q, sizeof q, "%s/path", p);
            sb_verrs_add(e, q, "E_TARGET_NOT_FOUND", "%s", pr.msg[0] ? pr.msg : "Ziel existiert nicht.");
            continue;
        }
        if (T->type == SB_TGT_FILE && w) {
            uint64_t need = T->file_size > pr.size_bytes ? T->file_size - pr.size_bytes : 0;
            if (need && !admin)
                sb_verrs_add(w, p, "W_NEEDS_ADMIN",
                             "Ohne Administratorrechte kann die Testdatei nicht per SetFileValidData angelegt werden; sie wird vollständig beschrieben (langsamer).");
            if (pr.free_bytes || pr.total_bytes) {
                uint64_t reserve = pr.total_bytes / 20;
                if (reserve < (1ULL << 30)) reserve = 1ULL << 30;
                snprintf(q, sizeof q, "%s/file_size", p);
                if (need > pr.free_bytes)
                    sb_verrs_add(w, q, "W_LOW_DISK_SPACE", "Für die Testdatei fehlen %.1f GiB freier Platz (frei: %.1f GiB).",
                                 (double)(need - pr.free_bytes) / 1073741824.0, (double)pr.free_bytes / 1073741824.0);
                else if (pr.free_bytes - need < reserve)
                    sb_verrs_add(w, q, "W_LOW_DISK_SPACE", "Nach dem Anlegen der Testdatei bleiben nur %.1f GiB frei.",
                                 (double)(pr.free_bytes - need) / 1073741824.0);
            }
        }
        uint64_t sz = T->type == SB_TGT_FILE ? T->file_size : pr.size_bytes;
        if (sz && max_bs && sz < max_bs) {
            snprintf(q, sizeof q, "%s/%s", p, T->type == SB_TGT_FILE ? "file_size" : "path");
            sb_verrs_add(e, q, "E_TARGET_SIZE", "Zielgröße %llu Bytes ist kleiner als block_size %llu Bytes.",
                         (unsigned long long)sz, (unsigned long long)max_bs);
        }
        if (t->cache == SB_CACHE_UNBUFFERED) {
            uint32_t sec = pr.sector_logical ? pr.sector_logical : 512;
            for (int i = 0; i < t->nspecs; i++) {
                sb_spec *S = &t->specs[i];
                if (S->block_size % sec) {
                    snprintf(q, sizeof q, "%s/access_specs/%d/block_size", ptr, i);
                    sb_verrs_add(e, q, "E_BLOCK_ALIGN",
                                 "block_size %llu ist kein Vielfaches der logischen Sektorgröße (%u Bytes) von %s; bei cache = unbuffered erforderlich.",
                                 (unsigned long long)S->block_size, sec, T->path);
                }
                if (S->align % sec) {
                    snprintf(q, sizeof q, "%s/access_specs/%d/align", ptr, i);
                    sb_verrs_add(e, q, "E_BLOCK_ALIGN",
                                 "align %llu ist kein Vielfaches der logischen Sektorgröße (%u Bytes) von %s; bei cache = unbuffered erforderlich.",
                                 (unsigned long long)S->align, sec, T->path);
                }
            }
        }
    }
}

/* Semantische Regeln für cpu/memory (M2): NUMA-Knoten, SIMD, Puffergröße, freier RAM */
static void semantic_compute(sb_test *t, const char *ptr, sb_verrs *e, sb_verrs *w) {
    char p[200];
    sb_topo topo;
    if (sb_topo_get(&topo)) return;
    int avail = 0;
    const sb_node *cn = NULL;
    if (t->numa_node >= 0) {
        cn = sb_topo_node(&topo, t->numa_node);
        P(p, sizeof p, ptr, "numa_node");
        if (!cn || !cn->ncpus) {
            sb_verrs_add(e, p, "E_NUMA_NODE", "NUMA-Knoten %d existiert nicht oder hat keine logischen Prozessoren (Knoten: %d).",
                         t->numa_node, topo.nnodes);
            sb_topo_free(&topo);
            return;
        }
        avail = cn->ncpus;
    } else avail = topo.ncpus;
    int latency = t->type == SB_T_MEMORY && t->mem_mode == 1;
    int threads = latency ? 1 : t->threads ? (int)t->threads : avail;
    if (latency && t->threads > 1 && w) {
        P(p, sizeof p, ptr, "threads");
        sb_verrs_add(w, p, "W_THREADS_IGNORED", "Bei mode = latency misst sbench immer mit einem Thread; threads = %u wird ignoriert.", t->threads);
    } else if (!latency && t->threads > (uint32_t)avail && w) {
        P(p, sizeof p, ptr, "threads");
        sb_verrs_add(w, p, "W_OVERSUBSCRIBED", "%u Threads, aber nur %d logische Prozessoren%s: Prozessoren werden mehrfach belegt.",
                     t->threads, avail, cn ? " im Knoten" : "");
    }
    if (t->type == SB_T_MEMORY) {
        if (t->mem_node >= 0 && !sb_topo_node(&topo, t->mem_node)) {
            P(p, sizeof p, ptr, "mem_node");
            sb_verrs_add(e, p, "E_NUMA_NODE", "NUMA-Knoten %d existiert nicht (Knoten: %d).", t->mem_node, topo.nnodes);
        }
        P(p, sizeof p, ptr, "buffer_size");
        if (t->buffer_size < 8192)
            sb_verrs_add(e, p, "E_BUFFER_SIZE", "buffer_size muss mindestens 8 KiB betragen (ist %llu Bytes).", (unsigned long long)t->buffer_size);
        if (!latency) {
            sb_simd isa = (sb_simd)t->simd;
            if (isa != SB_SIMD_AUTO && !sb_simd_supported(isa)) {
                P(p, sizeof p, ptr, "simd");
                sb_verrs_add(e, p, "E_SIMD_UNSUPPORTED", "simd = %s wird von dieser CPU bzw. diesem Betriebssystem nicht unterstützt.", sb_simd_name(isa));
            }
            if (t->nontemporal && w) {
                P(p, sizeof p, ptr, "nontemporal");
                if (t->mem_op == 0)
                    sb_verrs_add(w, p, "W_NONTEMPORAL_IGNORED", "nontemporal wirkt nur bei op = write und copy.");
                else if (!sb_nontemporal_supported())
                    sb_verrs_add(w, p, "W_NONTEMPORAL_UNSUPPORTED", "Non-temporal Stores sind auf dieser Architektur nicht umgesetzt; es werden normale Stores genutzt.");
            }
        }
        /* RAM: alle Puffer zusammen gegen freien Speicher (des Zielknotens) */
        double need = (double)t->buffer_size * threads, freeb = 0;
        const sb_node *mn = t->mem_node >= 0 ? sb_topo_node(&topo, t->mem_node) : NULL;
        if (mn) freeb = (double)mn->ram_free_bytes;
        else {
            MEMORYSTATUSEX ms;
            ms.dwLength = sizeof ms;
            if (GlobalMemoryStatusEx(&ms)) freeb = (double)ms.ullAvailPhys;
        }
        P(p, sizeof p, ptr, "buffer_size");
        if (freeb > 0 && need > freeb * 0.9)
            sb_verrs_add(e, p, "E_OUT_OF_MEMORY", "Die Puffer brauchen %.2f GiB (%d x buffer_size), frei sind nur %.2f GiB%s.",
                         need / 1073741824.0, threads, freeb / 1073741824.0, mn ? " auf dem Knoten" : "");
        else if (freeb > 0 && need > freeb * 0.5 && w)
            sb_verrs_add(w, p, "W_LOW_MEMORY", "Die Puffer belegen %.2f GiB von %.2f GiB freiem Speicher%s.",
                         need / 1073741824.0, freeb / 1073741824.0, mn ? " des Knotens" : "");
    }
    sb_topo_free(&topo);
}

static void free_test(sb_test *t) {
    for (int k = 0; k < t->ntargets; k++) free(t->targets[k].path);
    free(t->type_str);
}

void sb_job_free(sb_job *job) {
    if (!job) return;
    for (int i = 0; i < job->ntests; i++) free_test(&job->tests[i]);
    free(job->tests);
    free(job->name);
    if (job->root) cJSON_Delete(job->root);
    memset(job, 0, sizeof *job);
}

int sb_job_load_text(sb_job *job, const char *text, sb_verrs *e, sb_verrs *warns) {
    memset(job, 0, sizeof *job);
    /* UTF-8-BOM tolerieren */
    if ((unsigned char)text[0] == 0xEF && (unsigned char)text[1] == 0xBB && (unsigned char)text[2] == 0xBF) text += 3;
    job->root = cJSON_ParseWithOpts(text, NULL, 1);
    if (!job->root) {
        const char *ep = cJSON_GetErrorPtr();
        long off = ep ? (long)(ep - text) : -1;
        sb_verrs_add(e, "", "E_SCHEMA", "Job-Datei ist kein gültiges JSON (Fehler nahe Zeichen %ld).", off);
        return 2;
    }
    const cJSON *r = job->root;
    if (!cJSON_IsObject(r)) { sb_verrs_add(e, "", "E_SCHEMA", "Wurzel muss ein JSON-Objekt sein."); return 2; }
    static const char *const K_ROOT[] = {"schema", "name", "tests", NULL};
    check_keys(e, r, "", K_ROOT);
    const cJSON *sc = req(e, r, "", "schema");
    if (sc && !(cJSON_IsString(sc) && !strcmp(sc->valuestring, "sb-job/1")))
        sb_verrs_add(e, "/schema", "E_SCHEMA", "'schema' muss \"sb-job/1\" sein.");
    const char *name;
    get_str(e, r, "", "name", 1, &name);
    job->name = name ? sb_strdup(name) : NULL;
    const cJSON *ts = req(e, r, "", "tests");
    if (ts) {
        if (!cJSON_IsArray(ts) || cJSON_GetArraySize(ts) < 1) {
            sb_verrs_add(e, "/tests", "E_SCHEMA", "'tests' muss ein Array mit mindestens einem Eintrag sein.");
        } else {
            int n = cJSON_GetArraySize(ts);
            job->tests = (sb_test *)calloc((size_t)n, sizeof(sb_test));
            if (!job->tests) return 1;
            int i = 0;
            for (cJSON *x = ts->child; x; x = x->next, i++) {
                char p[64]; snprintf(p, sizeof p, "/tests/%d", i);
                sb_test *t = &job->tests[job->ntests++];
                t->raw = x;
                t->type = SB_T_UNKNOWN;
                if (!cJSON_IsObject(x)) { sb_verrs_add(e, p, "E_SCHEMA", "Test muss ein Objekt sein."); continue; }
                int ty = get_enum(e, x, p, "type", 1, E_TYPES, -1);
                if (ty < 0) continue;
                t->type = (sb_test_type)ty;
                get_str(e, x, p, "name", 0, &t->name);
                t->type_str = sb_strdup(E_TYPES[ty]);
                t->seed = 0x5be7c0de00000000ULL ^ (uint64_t)i;
                if (t->type == SB_T_STORAGE) {
                    parse_storage(t, x, p, e);
                    semantic_storage(t, p, e, warns, 0);
                } else {
                    t->numa_node = t->mem_node = -1;
                    int before = e->n;
                    parse_other(t, x, p, e);
                    if ((t->type == SB_T_CPU || t->type == SB_T_MEMORY) && e->n == before) semantic_compute(t, p, e, warns);
                }
            }
        }
    }
    return e->n ? 2 : 0;
}

int sb_job_load_file(sb_job *job, const char *path, sb_verrs *e, sb_verrs *warns) {
    char *txt = NULL;
    memset(job, 0, sizeof *job);
    if (sb_read_file(path, &txt, NULL)) {
        sb_verrs_add(e, "", "E_IO", "Job-Datei '%s' nicht lesbar: %s", path, sb_errtext(GetLastError()));
        return 1;
    }
    int rc = sb_job_load_text(job, txt, e, warns);
    free(txt);
    return rc;
}
