/* Unit-Tests des Messkerns (ohne Testframework). Exit 0 = alle bestanden. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <malloc.h>
#include "sb/hist.h"
#include "sb/rng.h"
#include "sb/job.h"
#include "sb/sha256.h"
#include "sb/util.h"
#include "sb/topo.h"
#include "sb/kernels.h"

static int g_fail = 0, g_pass = 0;
#define CHECK(cond, ...) do { if (cond) g_pass++; else { g_fail++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static int near_rel(double a, double b, double rel) { return fabs(a - b) <= rel * fabs(b); }

/* ---- Histogramm ---- */
static void test_hist_buckets(void) {
    uint32_t prev = 0;
    for (uint64_t v = 0; v < 2000000; v += (v < 4096 ? 1 : v / 97)) {
        uint32_t i = sb_hist_index(v);
        CHECK(i >= prev, "Index nicht monoton bei %llu", (unsigned long long)v);
        CHECK(v >= sb_hist_bucket_lo(i) && v <= sb_hist_bucket_hi(i), "Wert %llu nicht in Bucket %u", (unsigned long long)v, i);
        if (v >= 256) {
            double w = (double)(sb_hist_bucket_hi(i) - sb_hist_bucket_lo(i) + 1) / (double)sb_hist_bucket_lo(i);
            CHECK(w <= 1.0 / 128 + 1e-12, "Bucketbreite %.5f > 1/128 bei %llu", w, (unsigned long long)v);
        }
        prev = i;
    }
    /* 100 s liegen im Bereich */
    uint64_t big = 100ULL * 1000000000ULL;
    CHECK(sb_hist_index(big) < SB_HIST_BUCKETS - 1, "100 s außerhalb des Bereichs");
    CHECK(big <= sb_hist_bucket_hi(sb_hist_index(big)), "100 s Bucket falsch");
}

static void test_hist_uniform(void) {
    sb_hist *h = (sb_hist *)malloc(sizeof *h);
    sb_hist_init(h);
    /* 1 µs .. 1 ms gleichverteilt (deterministisch) */
    for (uint64_t v = 1000; v <= 1000000; v++) sb_hist_record(h, v);
    double n = 999001.0;
    CHECK(h->count == (uint64_t)n, "count");
    const double ps[] = {50, 90, 95, 99, 99.9, 99.99};
    for (int i = 0; i < 6; i++) {
        double exact = 1000 + ceil(ps[i] / 100 * n) - 1;
        double got = sb_hist_percentile(h, ps[i]);
        CHECK(near_rel(got, exact, 0.01), "uniform p%.2f: %.0f vs %.0f", ps[i], got, exact);
    }
    CHECK(near_rel(sb_hist_mean(h), 500500.0, 1e-9), "uniform mean %.1f", sb_hist_mean(h));
    CHECK(near_rel(sb_hist_stddev(h), 999000.0 / sqrt(12.0), 0.001), "uniform stddev %.1f", sb_hist_stddev(h));
    CHECK(h->min_ns == 1000 && h->max_ns == 1000000, "min/max");
    CHECK(sb_hist_percentile(h, 100) == 1000000.0 && sb_hist_percentile(h, 0) == 1000.0, "p0/p100");
    free(h);
}

static int cmp_u64(const void *a, const void *b) {
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return x < y ? -1 : x > y;
}

static void test_hist_exponential_and_merge(void) {
    const int N = 1000000;
    uint64_t *v = (uint64_t *)malloc(sizeof(uint64_t) * N);
    sb_hist *h = (sb_hist *)malloc(sizeof *h), *a = (sb_hist *)malloc(sizeof *a), *b = (sb_hist *)malloc(sizeof *b);
    sb_hist_init(h); sb_hist_init(a); sb_hist_init(b);
    sb_rng r;
    sb_rng_seed(&r, 42);
    /* Exponentialverteilung, Mittel 100 µs (+ 5 µs Offset) */
    for (int i = 0; i < N; i++) {
        double u = sb_rng_unit(&r);
        v[i] = 5000 + (uint64_t)(-log(1.0 - u) * 100000.0);
        sb_hist_record(h, v[i]);
        sb_hist_record(i & 1 ? a : b, v[i]);   /* "zwei Worker" */
    }
    qsort(v, N, sizeof(uint64_t), cmp_u64);
    sb_hist_merge(a, b);
    const double ps[] = {50, 90, 95, 99, 99.9, 99.99};
    for (int i = 0; i < 6; i++) {
        double exact = (double)v[(size_t)ceil(ps[i] / 100 * N) - 1];
        double got = sb_hist_percentile(h, ps[i]);
        CHECK(near_rel(got, exact, 0.01), "exp p%.2f: %.0f vs %.0f", ps[i], got, exact);
        CHECK(sb_hist_percentile(a, ps[i]) == got, "merge p%.2f", ps[i]);
        /* Theorie: 5 µs + 100 µs * -ln(1-p) */
        double theo = 5000 + 100000.0 * -log(1 - ps[i] / 100);
        if (ps[i] <= 99.9) CHECK(near_rel(got, theo, 0.03), "exp p%.2f Theorie %.0f vs %.0f", ps[i], got, theo);
    }
    CHECK(a->count == h->count && a->min_ns == h->min_ns && a->max_ns == h->max_ns, "merge count/min/max");
    CHECK(near_rel(sb_hist_mean(h), 105000.0, 0.01), "exp mean %.0f", sb_hist_mean(h));
    CHECK(near_rel(sb_hist_stddev(h), 100000.0, 0.02), "exp stddev %.0f", sb_hist_stddev(h));
    free(v); free(h); free(a); free(b);
}

static void test_hist_bimodal(void) {
    sb_hist *h = (sb_hist *)malloc(sizeof *h);
    sb_hist_init(h);
    /* 99 % bei 50 µs, 1 % bei 20 ms (Cache-Hit/Miss-artig) */
    for (int i = 0; i < 99000; i++) sb_hist_record(h, 50000);
    for (int i = 0; i < 1000; i++) sb_hist_record(h, 20000000);
    CHECK(near_rel(sb_hist_percentile(h, 50), 50000, 0.005), "bimodal p50 %.0f", sb_hist_percentile(h, 50));
    CHECK(near_rel(sb_hist_percentile(h, 99), 50000, 0.005), "bimodal p99 %.0f", sb_hist_percentile(h, 99));
    CHECK(near_rel(sb_hist_percentile(h, 99.9), 20000000, 0.005), "bimodal p99.9 %.0f", sb_hist_percentile(h, 99.9));
    free(h);
}

/* ---- Zufall und Spec-Gewichtung ---- */
static void test_rng(void) {
    sb_rng a, b;
    sb_rng_seed(&a, 7); sb_rng_seed(&b, 7);
    int same = 1;
    for (int i = 0; i < 1000; i++) if (sb_rng_next(&a) != sb_rng_next(&b)) same = 0;
    CHECK(same, "xoshiro nicht reproduzierbar");
    /* Referenzwert xoshiro256** mit Zustand {1,2,3,4}: erstes Ergebnis 11520 */
    sb_rng r = {{1, 2, 3, 4}};
    CHECK(sb_rng_next(&r) == 11520ULL, "xoshiro256** Referenzwert");
    CHECK(sb_rng_next(&r) == 0ULL, "xoshiro256** Referenzwert 2");
    CHECK(sb_rng_next(&r) == 1509978240ULL, "xoshiro256** Referenzwert 3");
    sb_rng_seed(&a, 99);
    long cnt[10] = {0};
    for (int i = 0; i < 1000000; i++) cnt[sb_rng_below(&a, 10)]++;
    for (int i = 0; i < 10; i++) CHECK(labs(cnt[i] - 100000) < 1500, "rng_below Bucket %d: %ld", i, cnt[i]);
}

static void test_spec_weights(void) {
    const uint32_t w[][4] = {{60, 40, 0, 0}, {10, 30, 60, 0}, {1, 1, 1, 97}};
    const int n[] = {2, 3, 4};
    for (int c = 0; c < 3; c++) {
        uint32_t cum[4], acc = 0;
        for (int i = 0; i < n[c]; i++) { acc += w[c][i]; cum[i] = acc; }
        CHECK(acc == 100, "Testdaten");
        /* exakt: jedes r in [0,100) trifft genau weight[i] mal Spec i */
        int hits[4] = {0};
        for (uint64_t r = 0; r < 100; r++) hits[sb_pick_spec(cum, n[c], r)]++;
        for (int i = 0; i < n[c]; i++) CHECK(hits[i] == (int)w[c][i], "exakte Gewichtung Fall %d Spec %d: %d", c, i, hits[i]);
        /* statistisch mit dem Engine-RNG */
        sb_rng r; sb_rng_seed(&r, 1234 + c);
        long h2[4] = {0};
        const long N = 1000000;
        for (long k = 0; k < N; k++) h2[sb_pick_spec(cum, n[c], sb_rng_below(&r, 100))]++;
        for (int i = 0; i < n[c]; i++) {
            double frac = (double)h2[i] / N * 100;
            CHECK(fabs(frac - w[c][i]) < 0.25, "Gewichtung Fall %d Spec %d: %.3f %% statt %u %%", c, i, frac, w[c][i]);
        }
    }
}

/* ---- SHA-256 ---- */
static void test_sha256(void) {
    char hex[65];
    sb_sha256_hex("abc", 3, hex);
    CHECK(!strcmp(hex, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"), "sha256(abc) = %s", hex);
    sb_sha256_hex("", 0, hex);
    CHECK(!strcmp(hex, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"), "sha256('') = %s", hex);
    char *m = (char *)malloc(1000000);
    memset(m, 'a', 1000000);
    sb_sha256_hex(m, 1000000, hex);
    CHECK(!strcmp(hex, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"), "sha256(1M a) = %s", hex);
    free(m);
}

/* ---- Job-Parsing und Validierung ---- */
static char g_tmpdir[MAX_PATH];

static char *json_escape_path(const char *p) {
    char *o = (char *)malloc(strlen(p) * 2 + 1), *q = o;
    for (; *p; p++) { if (*p == '\\') *q++ = '\\'; *q++ = *p; }
    *q = 0;
    return o;
}

static int has_err(const sb_verrs *e, const char *ptr, const char *code) {
    for (int i = 0; i < e->n; i++) if (!strcmp(e->v[i].pointer, ptr) && !strcmp(e->v[i].code, code)) return 1;
    return 0;
}

static void dump(const sb_verrs *e) {
    for (int i = 0; i < e->n; i++) printf("    %s %s: %s\n", e->v[i].code, e->v[i].pointer, e->v[i].msg);
}

static int load(const char *fmt, sb_job *job, sb_verrs *e) {
    char *dir = json_escape_path(g_tmpdir);
    char *txt = sb_aprintf(fmt, dir);
    memset(e, 0, sizeof *e);
    int rc = sb_job_load_text(job, txt, e, NULL);
    free(txt); free(dir);
    return rc;
}

static void test_job_ok(void) {
    sb_job j; sb_verrs e;
    int rc = load("{\"schema\":\"sb-job/1\",\"name\":\"t\",\"tests\":[{\"type\":\"storage\","
                  "\"targets\":[{\"path\":\"%ssbtest.dat\",\"kind\":\"file\",\"file_size\":\"256MiB\"}],"
                  "\"workers\":2,\"queue_depth\":8,\"warmup_s\":1,\"duration_s\":5,"
                  "\"access_specs\":[{\"weight\":100,\"block_size\":\"4KiB\",\"read_pct\":70,\"random_pct\":100}]},"
                  "{\"type\":\"cpu\",\"duration_s\":3}]}", &j, &e);
    CHECK(rc == 0, "gültiger Job abgelehnt (rc %d)", rc);
    if (rc) dump(&e);
    CHECK(j.ntests == 2 && j.tests[0].type == SB_T_STORAGE && j.tests[1].type == SB_T_CPU, "Testtypen");
    sb_test *t = &j.tests[0];
    CHECK(t->ntargets == 1 && t->targets[0].type == SB_TGT_FILE && t->targets[0].file_size == 256ULL << 20, "Target");
    CHECK(t->workers == 2 && t->queue_depth == 8 && t->duration_s == 5 && t->warmup_s == 1, "Parameter");
    CHECK(t->cache == SB_CACHE_UNBUFFERED, "Standard-Cache");
    CHECK(t->nspecs == 1 && t->specs[0].block_size == 4096 && t->specs[0].align == 4096 && t->specs[0].read_pct == 70 &&
          t->specs[0].burst_len == 1 && t->specs[0].delay_ms == 0, "Spec + Standardwerte");
    sb_job_free(&j); sb_verrs_free(&e);

    /* Größenformate */
    rc = load("{\"schema\":\"sb-job/1\",\"name\":\"t\",\"tests\":[{\"type\":\"storage\","
              "\"targets\":[{\"path\":\"%sx.dat\",\"kind\":\"file\",\"file_size\":\"1 GiB\"}],\"duration_s\":1,\"cache\":\"buffered\","
              "\"access_specs\":[{\"weight\":50,\"block_size\":65536,\"read_pct\":0,\"random_pct\":0,\"align\":\"512 KiB\"},"
              "{\"weight\":50,\"block_size\":\"8KiB\",\"read_pct\":100,\"random_pct\":100,\"burst_len\":4,\"delay_ms\":10}]}]}", &j, &e);
    CHECK(rc == 0, "Größenformate abgelehnt");
    if (rc) dump(&e);
    CHECK(j.tests[0].targets[0].file_size == 1ULL << 30 && j.tests[0].specs[0].block_size == 65536 &&
          j.tests[0].specs[0].align == 512 * 1024 && j.tests[0].specs[1].burst_len == 4 && j.tests[0].specs[1].delay_ms == 10 &&
          j.tests[0].cache == SB_CACHE_BUFFERED, "Größenwerte");
    sb_job_free(&j); sb_verrs_free(&e);
}

static void test_job_invalid(void) {
    sb_job j; sb_verrs e;
    /* Gewichtssumme 90, unbekanntes Feld, falsche Größe, Blockgröße 1000 bei unbuffered */
    int rc = load("{\"schema\":\"sb-job/1\",\"name\":\"t\",\"tests\":[{\"type\":\"storage\","
                  "\"targets\":[{\"path\":\"%sx.dat\",\"kind\":\"file\",\"file_size\":\"4KB\"}],\"duration_s\":1,\"foo\":1,"
                  "\"access_specs\":[{\"weight\":60,\"block_size\":\"4KiB\",\"read_pct\":100,\"random_pct\":100},"
                  "{\"weight\":30,\"block_size\":1000,\"read_pct\":100,\"random_pct\":100}]}]}", &j, &e);
    CHECK(rc == 2, "ungültiger Job nicht erkannt");
    CHECK(has_err(&e, "/tests/0/access_specs", "E_WEIGHT_SUM"), "E_WEIGHT_SUM fehlt");
    CHECK(has_err(&e, "/tests/0/foo", "E_SCHEMA"), "unbekanntes Feld nicht gemeldet");
    CHECK(has_err(&e, "/tests/0/targets/0/file_size", "E_SCHEMA"), "Größe 4KB nicht gemeldet");
    sb_job_free(&j); sb_verrs_free(&e);

    rc = load("{\"schema\":\"sb-job/1\",\"name\":\"t\",\"tests\":[{\"type\":\"storage\","
              "\"targets\":[{\"path\":\"%sx.dat\",\"kind\":\"file\",\"file_size\":\"1MiB\"}],\"duration_s\":1,"
              "\"access_specs\":[{\"weight\":100,\"block_size\":1000,\"read_pct\":100,\"random_pct\":100}]}]}", &j, &e);
    CHECK(rc == 2 && has_err(&e, "/tests/0/access_specs/0/block_size", "E_BLOCK_ALIGN"), "E_BLOCK_ALIGN fehlt");
    sb_job_free(&j); sb_verrs_free(&e);

    rc = load("{\"schema\":\"sb-job/1\",\"name\":\"t\",\"tests\":[{\"type\":\"storage\","
              "\"targets\":[{\"path\":\"%sx.dat\",\"kind\":\"file\",\"file_size\":\"4KiB\"}],\"duration_s\":1,"
              "\"access_specs\":[{\"weight\":100,\"block_size\":\"64KiB\",\"read_pct\":100,\"random_pct\":100}]}]}", &j, &e);
    CHECK(rc == 2 && has_err(&e, "/tests/0/targets/0/file_size", "E_TARGET_SIZE"), "E_TARGET_SIZE fehlt");
    sb_job_free(&j); sb_verrs_free(&e);

    rc = load("{\"schema\":\"sb-job/1\",\"name\":\"t\",\"tests\":[{\"type\":\"storage\","
              "\"targets\":[{\"path\":\"Q:\\\\gibtsnicht\\\\x.dat\",\"kind\":\"file\",\"file_size\":\"1MiB\"},"
              "{\"path\":\"\\\\\\\\.\\\\PhysicalDrive99\",\"kind\":\"physical\"}],\"duration_s\":1,"
              "\"access_specs\":[{\"weight\":100,\"block_size\":\"4KiB\",\"read_pct\":100,\"random_pct\":100}]}]}", &j, &e);
    CHECK(rc == 2 && has_err(&e, "/tests/0/targets/0/path", "E_TARGET_NOT_FOUND") &&
          has_err(&e, "/tests/0/targets/1/path", "E_TARGET_NOT_FOUND"), "E_TARGET_NOT_FOUND fehlt");
    if (!has_err(&e, "/tests/0/targets/1/path", "E_TARGET_NOT_FOUND")) dump(&e);
    sb_job_free(&j); sb_verrs_free(&e);

    /* Pflichtfelder, Wertebereiche, falscher Typ, physical mit file_size */
    rc = load("{\"schema\":\"sb-job/2\",\"tests\":[{\"type\":\"storage\","
              "\"targets\":[{\"path\":\"\\\\\\\\.\\\\PhysicalDrive0\",\"kind\":\"physical\",\"file_size\":1}],"
              "\"queue_depth\":2000,\"access_specs\":[{\"weight\":100,\"block_size\":\"4KiB\",\"read_pct\":101,\"random_pct\":\"x\"}]},"
              "{\"type\":\"gpu\"},{\"type\":\"net_client\",\"host\":\"h\",\"proto\":\"udp\",\"duration_s\":1}]}", &j, &e);
    CHECK(rc == 2, "rc");
    CHECK(has_err(&e, "/schema", "E_SCHEMA"), "schema-Version");
    CHECK(has_err(&e, "", "E_SCHEMA"), "name fehlt");
    CHECK(has_err(&e, "/tests/0", "E_SCHEMA"), "duration_s fehlt");
    CHECK(has_err(&e, "/tests/0/queue_depth", "E_SCHEMA"), "queue_depth Bereich");
    CHECK(has_err(&e, "/tests/0/access_specs/0/read_pct", "E_SCHEMA"), "read_pct Bereich");
    CHECK(has_err(&e, "/tests/0/access_specs/0/random_pct", "E_SCHEMA"), "random_pct Typ");
    CHECK(has_err(&e, "/tests/0/targets/0/file_size", "E_SCHEMA"), "file_size bei physical");
    CHECK(has_err(&e, "/tests/1/type", "E_SCHEMA"), "unbekannter Typ");
    CHECK(has_err(&e, "/tests/2/udp_rate_mbps", "E_UDP_RATE"), "E_UDP_RATE");
    sb_job_free(&j); sb_verrs_free(&e);

    /* kaputtes JSON */
    memset(&e, 0, sizeof e);
    rc = sb_job_load_text(&j, "{\"schema\": ", &e, NULL);
    CHECK(rc == 2 && e.n == 1 && !strcmp(e.v[0].pointer, ""), "JSON-Syntaxfehler");
    sb_job_free(&j); sb_verrs_free(&e);
}

static void test_examples(void) {
    /* Beispiel-Jobs aus dem Vertrag: dürfen höchstens an nicht vorhandenen Zielen scheitern */
    const char *files[] = {"schema/examples/job-storage-mix.json", "schema/examples/job-full.json"};
    for (int f = 0; f < 2; f++) {
        sb_job j; sb_verrs e = {0};
        int rc = sb_job_load_file(&j, files[f], &e, NULL);
        CHECK(rc != 1, "%s nicht lesbar (Arbeitsverzeichnis = Repo-Wurzel?)", files[f]);
        int other = 0;
        for (int i = 0; i < e.n; i++)
            if (strcmp(e.v[i].code, "E_TARGET_NOT_FOUND") && strcmp(e.v[i].code, "E_BLOCK_ALIGN") &&
                strcmp(e.v[i].code, "E_TARGET_SIZE")) other++;
        CHECK(other == 0, "%s: unerwartete Fehler", files[f]);
        if (other) dump(&e);
        sb_job_free(&j); sb_verrs_free(&e);
    }
    sb_job j; sb_verrs e = {0};
    int rc = sb_job_load_file(&j, "schema/examples/job-full.json", &e, NULL);
    (void)rc;
    CHECK(j.ntests == 9, "job-full: 9 Tests, gefunden %d", j.ntests);
    if (j.ntests == 9) {
        CHECK(j.tests[0].type == SB_T_HYPERV_INFO && j.tests[1].type == SB_T_CPU && j.tests[4].type == SB_T_STORAGE &&
              j.tests[5].type == SB_T_NET_CLIENT && j.tests[8].type == SB_T_NET_SERVER, "job-full Typen");
        CHECK(j.tests[4].specs[0].block_size == 1048576 && j.tests[4].specs[0].burst_len == 4, "job-full Storage");
    }
    sb_job_free(&j); sb_verrs_free(&e);
}

static void test_parse_size(void) {
    uint64_t v;
    CHECK(sb_parse_size("4K", &v) == 0 && v == 4096, "4K");
    CHECK(sb_parse_size("8 GiB", &v) == 0 && v == 8ULL << 30, "8 GiB");
    CHECK(sb_parse_size("12x", &v) != 0, "12x");
}

/* ---- M2: SRAT, Topologie-Auswahl, Kernel ---- */
static void srat_mem(uint8_t *p, uint32_t pd, uint64_t base, uint64_t len, uint32_t flags) {
    memset(p, 0, 40);
    p[0] = 1; p[1] = 40;
    memcpy(p + 2, &pd, 4);
    uint32_t lo = (uint32_t)base, hi = (uint32_t)(base >> 32);
    memcpy(p + 8, &lo, 4); memcpy(p + 12, &hi, 4);
    lo = (uint32_t)len; hi = (uint32_t)(len >> 32);
    memcpy(p + 16, &lo, 4); memcpy(p + 20, &hi, 4);
    memcpy(p + 28, &flags, 4);
}

static void test_srat(void) {
    uint8_t t[48 + 16 + 6 * 40];
    memset(t, 0, sizeof t);
    memcpy(t, "SRAT", 4);
    uint32_t len = sizeof t;
    memcpy(t + 4, &len, 4);
    uint8_t *p = t + 48;
    /* Typ 0 (Prozessor-Affinität, 16 Byte) wird übersprungen */
    p[0] = 0; p[1] = 16; p += 16;
    srat_mem(p, 0, 0, 0xA0000ULL, 1); p += 40;                       /* Knoten 0: 640 KiB */
    srat_mem(p, 0, 1ULL << 32, 63ULL << 30, 1); p += 40;             /* Knoten 0: 63 GiB */
    srat_mem(p, 1, 64ULL << 30, 64ULL << 30, 1); p += 40;            /* Knoten 1: 64 GiB */
    srat_mem(p, 1, 128ULL << 30, 256ULL << 30, 1 | 2); p += 40;      /* Hotplug -> ignoriert */
    srat_mem(p, 2, 384ULL << 30, 16ULL << 30, 1 | 4); p += 40;       /* nichtflüchtig -> ignoriert */
    srat_mem(p, 3, 400ULL << 30, 8ULL << 30, 0);                     /* nicht aktiv -> ignoriert */
    uint32_t dom[8]; uint64_t by[8];
    int n = sb_srat_parse(t, sizeof t, dom, by, 8);
    CHECK(n == 2, "SRAT: 2 Domänen erwartet, %d", n);
    if (n == 2) {
        CHECK(dom[0] == 0 && by[0] == 0xA0000ULL + (63ULL << 30), "SRAT Knoten 0: %llu", (unsigned long long)by[0]);
        CHECK(dom[1] == 1 && by[1] == 64ULL << 30, "SRAT Knoten 1: %llu", (unsigned long long)by[1]);
    }
    CHECK(sb_srat_parse(t, 40, dom, by, 8) < 0, "SRAT zu kurz");
    t[0] = 'X';
    CHECK(sb_srat_parse(t, sizeof t, dom, by, 8) < 0, "SRAT falsche Signatur");
}

static void test_topo_pick(void) {
    /* 2 Knoten x 2 Kerne x 2 SMT, Knoten 1 in Prozessorgruppe 1 (Index 64..67) */
    sb_cpu c[8];
    sb_node nd[2];
    uint32_t c0[4] = {0, 1, 2, 3}, c1[4] = {64, 65, 66, 67};
    for (int i = 0; i < 8; i++) {
        c[i].group = (uint16_t)(i / 4); c[i].bit = (uint8_t)(i % 4);
        c[i].index = c[i].group * 64u + c[i].bit;
        c[i].node = i / 4; c[i].core = i / 2; c[i].smt = i % 2;
    }
    memset(nd, 0, sizeof nd);
    nd[0].id = 0; nd[0].cpus = c0; nd[0].ncpus = 4;
    nd[1].id = 1; nd[1].cpus = c1; nd[1].ncpus = 4;
    sb_topo t = {c, 8, nd, 2, 4, 0};
    int out[10];
    int no = sb_topo_pick(&t, -1, 10, out);
    /* erst ein Thread pro Kern, Knoten abwechselnd: 0, 64, 2, 66, dann SMT: 1, 65, 3, 67, dann von vorn */
    static const int want[10] = {0, 4, 2, 6, 1, 5, 3, 7, 0, 4};
    int ok = no == 8;
    for (int i = 0; i < 10; i++) ok &= out[i] == want[i];
    CHECK(ok, "pick alle: %d | %d %d %d %d %d %d %d %d", no, out[0], out[1], out[2], out[3], out[4], out[5], out[6], out[7]);
    no = sb_topo_pick(&t, 1, 3, out);
    CHECK(no == 4 && out[0] == 4 && out[1] == 6 && out[2] == 5, "pick Knoten 1: %d | %d %d %d", no, out[0], out[1], out[2]);
    CHECK(sb_topo_pick(&t, 7, 1, out) == 0, "pick unbekannter Knoten");
}

static void test_chase(void) {
    const size_t line = 64, n = 4096;
    char *b = (char *)malloc(n * line);
    sb_chase_build(b, n * line, line, 12345);
    unsigned char *seen = (unsigned char *)calloc(n, 1);
    void *p = b;
    size_t steps = 0, dup = 0;
    for (; steps < n; steps++) {
        size_t k = (size_t)((char *)p - b) / line;
        if (k >= n || seen[k]) { dup++; break; }
        seen[k] = 1;
        p = *(void **)p;
    }
    CHECK(steps == n && dup == 0 && p == (void *)b, "Kette: ein Zyklus über alle %zu Zeilen (Schritte %zu)", n, steps);
    /* nicht einfach sequentiell */
    size_t seq = 0;
    p = b;
    for (size_t i = 0; i < n; i++) { void *q = *(void **)p; if ((char *)q == (char *)p + line) seq++; p = q; }
    CHECK(seq < n / 16, "Kette zu sequentiell (%zu)", seq);
    void *pos = b;
    sb_chase(&pos, n);
    CHECK(pos == (void *)b, "sb_chase: nach n Sprüngen zurück am Start");
    free(seen); free(b);
}

static void test_memkern(void) {
    const size_t bytes = 1 << 20;
    uint8_t *a = (uint8_t *)_aligned_malloc(bytes, 64), *d = (uint8_t *)_aligned_malloc(bytes, 64);
    for (int isa = SB_SIMD_SCALAR; isa <= SB_SIMD_NEON; isa++) {
        sb_memkern k;
        if (!sb_simd_supported((sb_simd)isa) || sb_memkern_get((sb_simd)isa, &k)) continue;
        for (int nt = 0; nt < 2; nt++) {
            for (size_t i = 0; i < bytes; i++) a[i] = (uint8_t)(i * 31 + 7);
            memset(d, 0, bytes);
            k.copy(d, a, bytes, nt);
            CHECK(!memcmp(a, d, bytes), "copy %s nt=%d", sb_simd_name((sb_simd)isa), nt);
            memset(d, 0, bytes);
            k.write(d, bytes, nt);
            size_t zero_lines = 0;
            for (size_t off = 0; off < bytes; off += 64) {
                int z = 1;
                for (int b = 0; b < 64 && z; b++) z = d[off + (size_t)b] == 0;
                zero_lines += (size_t)z;
            }
            CHECK(zero_lines == 0, "write %s nt=%d: %zu von %zu Zeilen nicht beschrieben", sb_simd_name((sb_simd)isa), nt,
                  zero_lines, bytes / 64);
        }
        (void)k.read(a, bytes);
        g_pass++;
    }
    CHECK(sb_simd_supported(SB_SIMD_SCALAR), "scalar immer unterstützt");
    CHECK(sb_simd_supported(sb_simd_auto()), "auto unterstützt");
    _aligned_free(a); _aligned_free(d);
}

static void test_cpu_kernels(void) {
    for (int w = 0; w < 4; w++) {
        uint64_t s1[8] = {1, 2, 3, 4, 5, 6, 7, 8}, s2[8] = {1, 2, 3, 4, 5, 6, 7, 8};
        sb_cpu_kernel((sb_cpu_workload)w, s1, 100);
        sb_cpu_kernel((sb_cpu_workload)w, s2, 100);
        CHECK(!memcmp(s1, s2, sizeof s1), "%s deterministisch", sb_cpu_workload_name((sb_cpu_workload)w));
        uint64_t s0[8] = {1, 2, 3, 4, 5, 6, 7, 8};
        CHECK(memcmp(s0, s1, sizeof s1) != 0, "%s verändert den Zustand", sb_cpu_workload_name((sb_cpu_workload)w));
    }
}

static void test_job_m2(void) {
    sb_job j; sb_verrs e = {0};
    int rc = load("{\"schema\":\"sb-job/1\",\"name\":\"m2\",\"tests\":["
                  "{\"type\":\"cpu\",\"workload\":\"crypto\",\"threads\":2,\"duration_s\":3},"
                  "{\"type\":\"memory\",\"mode\":\"bandwidth\",\"op\":\"copy\",\"buffer_size\":\"1MiB\",\"duration_s\":2,"
                  "\"nontemporal\":true,\"simd\":\"scalar\",\"warmup_s\":0},"
                  "{\"type\":\"memory\",\"mode\":\"latency\",\"buffer_size\":\"64KiB\",\"duration_s\":1}]}", &j, &e);
    CHECK(rc == 0 && e.n == 0, "M2-Job gültig (rc %d, %d Fehler: %s)", rc, e.n, e.n ? e.v[0].code : "");
    if (rc == 0) {
        CHECK(j.tests[0].workload == 2 && j.tests[0].threads == 2 && j.tests[0].warmup_s == 1 && j.tests[0].numa_node == -1,
              "cpu-Felder");
        CHECK(j.tests[1].mem_mode == 0 && j.tests[1].mem_op == 2 && j.tests[1].buffer_size == 1 << 20 && j.tests[1].nontemporal == 1 &&
              j.tests[1].simd == SB_SIMD_SCALAR && j.tests[1].warmup_s == 0 && j.tests[1].mem_node == -1, "memory-Felder");
        CHECK(j.tests[2].mem_mode == 1 && j.tests[2].mem_op == 0 && j.tests[2].simd == SB_SIMD_AUTO, "latency-Felder");
    }
    sb_job_free(&j); sb_verrs_free(&e);
    rc = load("{\"schema\":\"sb-job/1\",\"name\":\"m2\",\"tests\":[{\"type\":\"memory\",\"mode\":\"bandwidth\",\"buffer_size\":4096,\"duration_s\":1}]}", &j, &e);
    CHECK(rc != 0 && e.n >= 1 && !strcmp(e.v[0].code, "E_BUFFER_SIZE"), "E_BUFFER_SIZE (%d: %s)", e.n, e.n ? e.v[0].code : "");
    sb_job_free(&j); sb_verrs_free(&e);
    rc = load("{\"schema\":\"sb-job/1\",\"name\":\"m2\",\"tests\":[{\"type\":\"cpu\",\"numa_node\":500,\"duration_s\":1}]}", &j, &e);
    CHECK(rc != 0 && e.n >= 1 && !strcmp(e.v[0].code, "E_NUMA_NODE"), "E_NUMA_NODE (%d: %s)", e.n, e.n ? e.v[0].code : "");
    sb_job_free(&j); sb_verrs_free(&e);
}

int main(void) {
    DWORD n = GetTempPathA(MAX_PATH, g_tmpdir);
    if (!n) strcpy(g_tmpdir, ".\\");
    test_hist_buckets();
    test_hist_uniform();
    test_hist_exponential_and_merge();
    test_hist_bimodal();
    test_rng();
    test_spec_weights();
    test_sha256();
    test_parse_size();
    test_job_ok();
    test_job_invalid();
    test_examples();
    test_srat();
    test_topo_pick();
    test_chase();
    test_memkern();
    test_cpu_kernels();
    test_job_m2();
    printf("%d Prüfungen bestanden, %d fehlgeschlagen\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
