/* Unit-Tests des Messkerns (ohne Testframework). Exit 0 = alle bestanden. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "sb/hist.h"
#include "sb/rng.h"
#include "sb/job.h"
#include "sb/sha256.h"
#include "sb/util.h"

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
    printf("%d Prüfungen bestanden, %d fehlgeschlagen\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
