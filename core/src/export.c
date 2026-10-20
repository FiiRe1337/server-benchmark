/* CSV-Export eines sb-result/1 im Stil "flat" oder "iometer". */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sb/sb.h"
#include "sb/util.h"
#include "cJSON.h"

typedef struct sbuf { char *p; size_t n, cap; } sbuf;

static void sb_put(sbuf *b, const char *s) {
    size_t l = strlen(s);
    if (b->n + l + 1 > b->cap) {
        size_t nc = b->cap ? b->cap * 2 : 4096;
        while (nc < b->n + l + 1) nc *= 2;
        char *np = (char *)realloc(b->p, nc);
        if (!np) return;
        b->p = np; b->cap = nc;
    }
    memcpy(b->p + b->n, s, l + 1);
    b->n += l;
}

static void put_field(sbuf *b, const char *s, int first) {
    if (!first) sb_put(b, ",");
    if (!s) return;
    if (strpbrk(s, ",\"\r\n")) {
        sb_put(b, "\"");
        for (const char *p = s; *p; p++) { char c[2] = {*p, 0}; sb_put(b, *p == '"' ? "\"\"" : c); }
        sb_put(b, "\"");
    } else sb_put(b, s);
}

static void put_num(sbuf *b, const cJSON *o, const char *key, double scale) {
    const cJSON *c = o ? cJSON_GetObjectItemCaseSensitive(o, key) : NULL;
    char t[64] = "";
    if (cJSON_IsNumber(c)) snprintf(t, sizeof t, "%.6g", c->valuedouble * scale);
    put_field(b, t, 0);
}

/* Wie put_num, aber mit mehr Stellen (z. B. ops_per_s ~ 1e9) */
static void put_num_p(sbuf *b, const cJSON *o, const char *key) {
    const cJSON *c = o ? cJSON_GetObjectItemCaseSensitive(o, key) : NULL;
    char t[64] = "";
    if (cJSON_IsNumber(c)) snprintf(t, sizeof t, "%.12g", c->valuedouble);
    put_field(b, t, 0);
}

static const cJSON *get(const cJSON *o, const char *k) { return o ? cJSON_GetObjectItemCaseSensitive(o, k) : NULL; }

static void flat_row(sbuf *b, int test, const char *type, const char *scope, const char *id, const cJSON *x,
                     int has_rw_mbps) {
    char t[32];
    snprintf(t, sizeof t, "%d", test);
    put_field(b, t, 1);
    put_field(b, type, 0);
    put_field(b, scope, 0);
    put_field(b, id, 0);
    put_num(b, x, "iops", 1);
    put_num(b, x, "mbps", 1);
    put_num(b, x, "read_iops", 1);
    put_num(b, x, "write_iops", 1);
    if (has_rw_mbps) { put_num(b, x, "read_mbps", 1); put_num(b, x, "write_mbps", 1); }
    else { put_field(b, "", 0); put_field(b, "", 0); }
    const cJSON *l = get(x, "lat_us");
    put_num(b, l, "mean", 1);
    put_num(b, l, "p99", 1);
    put_num(b, l, "max", 1);
    const cJSON *e = get(x, "errors");
    char es[32] = "";
    if (cJSON_IsNumber(e)) snprintf(es, sizeof es, "%.0f", e->valuedouble);
    else if (cJSON_IsArray(e)) snprintf(es, sizeof es, "%d", cJSON_GetArraySize(e));
    put_field(b, es, 0);
    /* cpu/memory (Vertrag 1.2), angehängt, damit bisherige Spaltenpositionen gleich bleiben */
    put_num_p(b, x, "ops_per_s");
    put_num_p(b, x, "gbps");
    put_num_p(b, x, "latency_ns");
    put_num_p(b, x, "score");
    sb_put(b, "\r\n");
}

static void export_flat(sbuf *b, const cJSON *tests) {
    sb_put(b, "test,type,scope,id,iops,mbps,read_iops,write_iops,read_mbps,write_mbps,lat_mean_us,lat_p99_us,lat_max_us,errors,ops_per_s,gbps,latency_ns,score\r\n");
    const cJSON *t;
    cJSON_ArrayForEach(t, tests) {
        const cJSON *ix = get(t, "index"), *ty = get(t, "type");
        int idx = cJSON_IsNumber(ix) ? ix->valueint : 0;
        const char *type = cJSON_IsString(ty) ? ty->valuestring : "";
        flat_row(b, idx, type, "total", "", get(t, "summary"), 1);
        const cJSON *w;
        cJSON_ArrayForEach(w, get(t, "per_worker")) {
            char id[16];
            snprintf(id, sizeof id, "%d", get(w, "id") ? get(w, "id")->valueint : 0);
            flat_row(b, idx, type, "worker", id, w, 0);
        }
        const cJSON *s;
        cJSON_ArrayForEach(s, get(t, "per_spec")) {
            char id[16];
            snprintf(id, sizeof id, "%d", get(s, "spec") ? get(s, "spec")->valueint : 0);
            flat_row(b, idx, type, "spec", id, s, 0);
        }
    }
}

/* Iometer-ähnliche Spalten; Antwortzeiten wie bei Iometer in Millisekunden */
static void export_iometer(sbuf *b, const cJSON *res) {
    sb_put(b, "'Results\r\n");
    sb_put(b, "Target Type,Target Name,Access Specification Name,# Managers,# Workers,# Disks,IOps,Read IOps,Write IOps,"
              "MBps (Binary),Read MBps (Binary),Write MBps (Binary),MBps (Decimal),Read MBps (Decimal),Write MBps (Decimal),"
              "Transactions per Second,Average Response Time,Maximum Response Time,Errors,Read Errors,Write Errors,"
              "% CPU Utilization,Test,Start Time,Host\r\n");
    const cJSON *meta = get(res, "meta");
    const char *host = cJSON_IsString(get(meta, "host")) ? get(meta, "host")->valuestring : "";
    const char *st = cJSON_IsString(get(res, "started_utc")) ? get(res, "started_utc")->valuestring : "";
    const cJSON *t;
    cJSON_ArrayForEach(t, get(res, "tests")) {
        if (!cJSON_IsString(get(t, "type")) || strcmp(get(t, "type")->valuestring, "storage")) continue;
        const cJSON *cfg = get(t, "config"), *sum = get(t, "summary");
        int idx = get(t, "index") ? get(t, "index")->valueint : 0;
        /* Spec-Name aus der Konfiguration ableiten */
        char spec[512] = "";
        const cJSON *as;
        cJSON_ArrayForEach(as, get(cfg, "access_specs")) {
            char one[128];
            const cJSON *bs = get(as, "block_size");
            char bss[32];
            if (cJSON_IsString(bs)) snprintf(bss, sizeof bss, "%s", bs->valuestring);
            else snprintf(bss, sizeof bss, "%.0f", bs ? bs->valuedouble : 0);
            snprintf(one, sizeof one, "%s%d%% %s %d%%read %d%%random", spec[0] ? "; " : "",
                     get(as, "weight") ? get(as, "weight")->valueint : 0, bss,
                     get(as, "read_pct") ? get(as, "read_pct")->valueint : 0,
                     get(as, "random_pct") ? get(as, "random_pct")->valueint : 0);
            strncat(spec, one, sizeof spec - strlen(spec) - 1);
        }
        int nworkers = cJSON_GetArraySize(get(t, "per_worker"));
        int ntargets = cJSON_GetArraySize(get(cfg, "targets"));
        double cpu = 0; int ncpu = 0;
        const cJSON *ts;
        cJSON_ArrayForEach(ts, get(t, "timeseries")) if (cJSON_IsNumber(get(ts, "cpu_pct"))) { cpu += get(ts, "cpu_pct")->valuedouble; ncpu++; }
        char cpus[32] = "";
        if (ncpu) snprintf(cpus, sizeof cpus, "%.2f", cpu / ncpu);
        char row[128];
        /* ALL-Zeile + WORKER-Zeilen */
        for (int pass = -1; pass < nworkers; pass++) {
            const cJSON *x = pass < 0 ? sum : cJSON_GetArrayItem(get(t, "per_worker"), pass);
            char name[300];
            if (pass < 0) snprintf(name, sizeof name, "All");
            else {
                int tg = get(x, "target") ? get(x, "target")->valueint : 0;
                const cJSON *tt = cJSON_GetArrayItem(get(cfg, "targets"), tg);
                snprintf(name, sizeof name, "Worker %d (%s)", pass + 1,
                         cJSON_IsString(get(tt, "path")) ? get(tt, "path")->valuestring : "?");
            }
            put_field(b, pass < 0 ? "ALL" : "WORKER", 1);
            put_field(b, name, 0);
            put_field(b, spec, 0);
            snprintf(row, sizeof row, "%d", 1); put_field(b, pass < 0 ? row : "", 0);
            snprintf(row, sizeof row, "%d", pass < 0 ? nworkers : 1); put_field(b, row, 0);
            snprintf(row, sizeof row, "%d", pass < 0 ? ntargets : 1); put_field(b, row, 0);
            put_num(b, x, "iops", 1);
            put_num(b, x, "read_iops", 1);
            put_num(b, x, "write_iops", 1);
            put_num(b, x, "mbps", 1e6 / 1048576.0);
            if (pass < 0) { put_num(b, x, "read_mbps", 1e6 / 1048576.0); put_num(b, x, "write_mbps", 1e6 / 1048576.0); }
            else { put_field(b, "", 0); put_field(b, "", 0); }
            put_num(b, x, "mbps", 1);
            if (pass < 0) { put_num(b, x, "read_mbps", 1); put_num(b, x, "write_mbps", 1); }
            else { put_field(b, "", 0); put_field(b, "", 0); }
            put_num(b, x, "iops", 1);
            const cJSON *l = get(x, "lat_us");
            put_num(b, l, "mean", 0.001);
            put_num(b, l, "max", 0.001);
            put_num(b, x, "errors", 1);
            put_field(b, "", 0);
            put_field(b, "", 0);
            put_field(b, pass < 0 ? cpus : "", 0);
            snprintf(row, sizeof row, "%d", idx); put_field(b, row, 0);
            put_field(b, st, 0);
            put_field(b, host, 0);
            sb_put(b, "\r\n");
        }
    }
    sb_put(b, "'End Results\r\n");
}

int sb_export_csv(const char *result_path, const char *csv_path, const char *style, char *err, size_t errlen) {
    char *txt = NULL;
    if (sb_read_file(result_path, &txt, NULL)) {
        snprintf(err, errlen, "Ergebnisdatei '%s' nicht lesbar: %s", result_path, sb_errtext(GetLastError()));
        return SB_EXIT_ERROR;
    }
    const char *p = txt;
    if ((unsigned char)p[0] == 0xEF && (unsigned char)p[1] == 0xBB && (unsigned char)p[2] == 0xBF) p += 3;
    cJSON *res = cJSON_Parse(p);
    free(txt);
    const cJSON *sc = get(res, "schema");
    if (!res || !cJSON_IsString(sc) || strcmp(sc->valuestring, "sb-result/1")) {
        snprintf(err, errlen, "'%s' ist kein sb-result/1-Dokument.", result_path);
        cJSON_Delete(res);
        return SB_EXIT_INVALID;
    }
    sbuf b = {0};
    if (!style || !strcmp(style, "flat")) export_flat(&b, get(res, "tests"));
    else if (!strcmp(style, "iometer")) export_iometer(&b, res);
    else {
        snprintf(err, errlen, "Unbekannter --style '%s' (flat|iometer).", style);
        cJSON_Delete(res);
        return SB_EXIT_INVALID;
    }
    cJSON_Delete(res);
    int rc = SB_EXIT_OK;
    if (!b.p || sb_write_file(csv_path, b.p, b.n)) {
        snprintf(err, errlen, "CSV '%s' nicht schreibbar: %s", csv_path, sb_errtext(GetLastError()));
        rc = SB_EXIT_ERROR;
    }
    free(b.p);
    return rc;
}
