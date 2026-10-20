/* Iometer-.icf -> sb-job/1.
 *
 * Aufbau einer ICF-Datei (Iometer 1.1.0 / 2006.07.27): erste Zeile "Version ...", danach Abschnitte
 * "'TEST SETUP ====", "'RESULTS DISPLAY ====", "'ACCESS SPECIFICATIONS ====", "'MANAGER LIST ====",
 * jeweils mit "'END ..." abgeschlossen. Kommentarzeilen beginnen mit ', Datenzeilen sind meist mit
 * Tab eingerückt; der Schlüssel einer Datenzeile ist die letzte Kommentarzeile davor (eingerückte
 * Unterüberschriften wie "'\thours minutes seconds" ändern den Schlüssel nicht).
 *
 * Abbildung: jede zugewiesene Access Specification (Iometer führt sie nacheinander aus) wird ein
 * storage-Test, ihre Zeilen werden gewichtete access_specs. Jedes Paar (Disk-Worker, Target) wird
 * ein sb-Worker mit queue_depth = Outstanding I/Os (bei Iometer pro Worker und Target). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <stdint.h>
#include "sb/icf.h"
#include "sb/util.h"

#define ICF_MAX_SPECS   256
#define ICF_MAX_LINES   SB_MAX_SPECS
#define ICF_MAX_MGR     16
#define ICF_MAX_WORKERS 256
#define ICF_MAX_TGT     SB_MAX_TARGETS
#define ICF_MAX_ASSIGN  256
#define ICF_DEFAULT_FILE_SIZE (8ULL << 30)
#define ICF_DEFAULT_DURATION 60

typedef struct { long long size, pct, rd, rnd, delay, burst, align, reply; int line; } icf_line;
typedef struct { char name[128]; icf_line l[ICF_MAX_LINES]; int n, line; } icf_spec;
typedef struct { char name[200]; char type[32]; int line; } icf_target;
typedef struct {
    char name[128], type[64];
    int line;
    long long qd; int has_qd;
    char conn_rate[16], fixed_seed[16];
    long long max_size, start_sector, pattern; int has_disk, has_pattern, disk_line;
    int assigned[ICF_MAX_ASSIGN]; int nassigned;
    icf_target t[ICF_MAX_TGT]; int nt;
} icf_worker;
typedef struct { char name[128]; int line, first_worker, nworkers; } icf_mgr;

typedef struct {
    char version[64], desc[256], test_type[64];
    long long run_s; int has_run, run_line;
    long long ramp_s;
    icf_spec *specs; int nspecs;
    icf_mgr mgr[ICF_MAX_MGR]; int nmgr;
    icf_worker *w; int nw;
} icf_doc;

/* ---- Hilfen ------------------------------------------------------------ */

static char *trim(char *s) {
    while (*s == ' ' || *s == '\t') s++;
    size_t n = strlen(s);
    while (n && (s[n - 1] == ' ' || s[n - 1] == '\t' || s[n - 1] == '\r')) s[--n] = 0;
    return s;
}

static int ieq_prefix(const char *s, const char *pre) {
    for (; *pre; s++, pre++)
        if (tolower((unsigned char)*s) != tolower((unsigned char)*pre)) return 0;
    return 1;
}

static int ieq(const char *a, const char *b) {
    for (; *a && *b; a++, b++)
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
    return !*a && !*b;
}

static void cpy(char *d, size_t n, const char *s) {
    size_t l = strlen(s);
    if (l >= n) l = n - 1;
    memcpy(d, s, l);
    d[l] = 0;
}

static int parse_ll(const char *s, long long *v) {
    char *end;
    while (*s == ' ' || *s == '\t') s++;
    if (!*s) return 0;
    long long x = strtoll(s, &end, 10);
    while (*end == ' ' || *end == '\t') end++;
    if (*end) return 0;
    *v = x;
    return 1;
}

/* Kommagetrennte Felder (getrimmt) in f[0..max) */
static int split(char *s, char **f, int max) {
    int n = 0;
    char *p = s;
    while (n < max) {
        char *c = strchr(p, ',');
        if (c) *c = 0;
        f[n++] = trim(p);
        if (!c) break;
        p = c + 1;
    }
    return n;
}

/* Gültiges UTF-8? Sonst wird der Text als Windows-1252/Latin-1 gelesen (Iometer schreibt ANSI). */
static int utf8_valid(const unsigned char *s, size_t n) {
    for (size_t i = 0; i < n;) {
        unsigned c = s[i];
        int k = c < 0x80 ? 0 : (c & 0xE0) == 0xC0 ? 1 : (c & 0xF0) == 0xE0 ? 2 : (c & 0xF8) == 0xF0 ? 3 : -1;
        if (k < 0 || (k == 1 && c < 0xC2)) return 0;
        if (i + (size_t)k >= n && k) return 0;
        for (int j = 1; j <= k; j++) if ((s[i + j] & 0xC0) != 0x80) return 0;
        i += (size_t)k + 1;
    }
    return 1;
}

static char *to_utf8(const char *text, size_t len) {
    const unsigned char *s = (const unsigned char *)text;
    if (len >= 3 && s[0] == 0xEF && s[1] == 0xBB && s[2] == 0xBF) { s += 3; len -= 3; }
    int ok = utf8_valid(s, len);
    char *o = (char *)malloc(len * 2 + 1), *d = o;
    if (!o) return NULL;
    for (size_t i = 0; i < len; i++) {
        unsigned c = s[i];
        if (!c) c = ' ';
        if (ok || c < 0x80) { *d++ = (char)c; continue; }
        *d++ = (char)(0xC0 | (c >> 6));
        *d++ = (char)(0x80 | (c & 0x3F));
    }
    *d = 0;
    return o;
}

/* Größe hübsch: "8KiB", "1MiB", sonst Bytes als Zahl */
static void add_size(cJSON *o, const char *key, unsigned long long v) {
    static const char *u[] = {"GiB", "MiB", "KiB"};
    static const int sh[] = {30, 20, 10};
    for (int i = 0; i < 3; i++) {
        unsigned long long m = 1ULL << sh[i];
        if (v >= m && v % m == 0) {
            char b[40];
            snprintf(b, sizeof b, "%llu%s", v / m, u[i]);
            cJSON_AddStringToObject(o, key, b);
            return;
        }
    }
    cJSON_AddNumberToObject(o, key, (double)v);
}

/* ---- Parser ------------------------------------------------------------ */

enum { SEC_NONE, SEC_SETUP, SEC_RESULTS, SEC_SPECS, SEC_MANAGERS, SEC_OTHER };

static int find_spec(const icf_doc *d, const char *name) {
    for (int i = 0; i < d->nspecs; i++) if (!strcmp(d->specs[i].name, name)) return i;
    return -1;
}

static int parse_doc(icf_doc *d, char *text, sb_verrs *e, sb_verrs *w) {
    char key[256] = "";
    int sec = SEC_NONE, sec_line = 0, saw_specs = 0, lineno = 0, saw_version = 0;
    char secname[64] = "";
    icf_spec *cs = NULL;
    icf_worker *cw = NULL;
    int nerr0 = e->n;
    char *p = text;
    while (p && *p) {
        char *nl = strchr(p, '\n');
        if (nl) *nl = 0;
        lineno++;
        char *raw = p;
        p = nl ? nl + 1 : NULL;
        size_t rl = strlen(raw);
        if (rl && raw[rl - 1] == '\r') raw[--rl] = 0;
        char *s = trim(raw);
        if (!saw_version) {
            if (!*s) continue;
            if (!ieq_prefix(s, "Version")) {
                sb_verrs_add(e, "", "E_ICF_FORMAT", "Zeile %d: keine Iometer-Konfigurationsdatei (erwartet \"Version ...\" am Anfang).", lineno);
                return 2;
            }
            cpy(d->version, sizeof d->version, trim(s + 7));
            saw_version = 1;
            continue;
        }
        if (!*s) continue;
        if (*s == '\'') {
            char *c = s + 1;
            if (*c == ' ' || *c == '\t') continue; /* Unterüberschrift: Schlüssel bleibt */
            c = trim(c);
            if (strstr(c, "====")) {
                char nm[64];
                size_t k = 0;
                while (c[k] && c[k] != '=' && k < sizeof nm - 1) { nm[k] = c[k]; k++; }
                nm[k] = 0;
                char *n = trim(nm);
                if (sec == SEC_SPECS || sec == SEC_MANAGERS) {
                    sb_verrs_add(e, "", "E_ICF_FORMAT", "Zeile %d: Abschnitt '%s' (ab Zeile %d) ist nicht mit 'END ...' abgeschlossen.", lineno, secname, sec_line);
                    return 2;
                }
                cpy(secname, sizeof secname, n);
                sec_line = lineno;
                sec = ieq(n, "TEST SETUP") ? SEC_SETUP : ieq(n, "RESULTS DISPLAY") ? SEC_RESULTS
                    : ieq(n, "ACCESS SPECIFICATIONS") ? SEC_SPECS : ieq(n, "MANAGER LIST") ? SEC_MANAGERS : SEC_OTHER;
                if (sec == SEC_SPECS) saw_specs = 1;
                if (sec == SEC_OTHER)
                    sb_verrs_add(w, "", "W_ICF_UNSUPPORTED", "Zeile %d: Abschnitt '%s' ist unbekannt und wird ignoriert.", lineno, n);
                key[0] = 0;
                continue;
            }
            if (ieq_prefix(c, "END ") && sec != SEC_NONE && ieq(trim(c + 4), secname)) {
                sec = SEC_NONE;
                key[0] = 0;
                continue;
            }
            cpy(key, sizeof key, c);
            if (sec == SEC_MANAGERS && cw && ieq(key, "End worker")) cw = NULL;
            continue;
        }
        /* Datenzeile */
        if (sec == SEC_NONE || sec == SEC_RESULTS || sec == SEC_OTHER) continue; /* auch "Version ..." am Ende */
        char buf[1024];
        cpy(buf, sizeof buf, s);
        if (sec == SEC_SETUP) {
            if (ieq(key, "Test Description")) cpy(d->desc, sizeof d->desc, s);
            else if (ieq(key, "Run Time")) {
                long long h, m, sx;
                char *t1 = strtok(buf, " \t"), *t2 = strtok(NULL, " \t"), *t3 = strtok(NULL, " \t");
                if (!t1 || !t2 || !t3 || !parse_ll(t1, &h) || !parse_ll(t2, &m) || !parse_ll(t3, &sx) || h < 0 || m < 0 || sx < 0) {
                    sb_verrs_add(e, "", "E_ICF_VALUE", "Zeile %d: Run Time muss 'Stunden Minuten Sekunden' sein, gelesen: '%s'.", lineno, s);
                    continue;
                }
                d->run_s = h * 3600 + m * 60 + sx;
                d->has_run = 1;
                d->run_line = lineno;
            } else if (ieq_prefix(key, "Ramp Up Time")) {
                if (!parse_ll(s, &d->ramp_s) || d->ramp_s < 0)
                    sb_verrs_add(e, "", "E_ICF_VALUE", "Zeile %d: Ramp Up Time muss eine Zahl >= 0 sein, gelesen: '%s'.", lineno, s);
            } else if (ieq(key, "Test Type")) cpy(d->test_type, sizeof d->test_type, s);
            continue;
        }
        if (sec == SEC_SPECS) {
            if (ieq_prefix(key, "Access specification name")) {
                if (d->nspecs >= ICF_MAX_SPECS) {
                    sb_verrs_add(e, "", "E_ICF_VALUE", "Zeile %d: mehr als %d Access Specifications.", lineno, ICF_MAX_SPECS);
                    return 2;
                }
                char *c = strrchr(buf, ',');
                if (c) *c = 0;
                char *n = trim(buf);
                if (!*n) { sb_verrs_add(e, "", "E_ICF_PARSE", "Zeile %d: Access Specification ohne Namen.", lineno); cs = NULL; continue; }
                if (find_spec(d, n) >= 0) {
                    sb_verrs_add(e, "", "E_ICF_SPEC", "Zeile %d: Access Specification '%s' ist doppelt definiert.", lineno, n);
                    cs = NULL;
                    continue;
                }
                cs = &d->specs[d->nspecs++];
                memset(cs, 0, sizeof *cs);
                cpy(cs->name, sizeof cs->name, n);
                cs->line = lineno;
            } else if (ieq_prefix(key, "size,% of size")) {
                if (!cs) { sb_verrs_add(e, "", "E_ICF_PARSE", "Zeile %d: Spec-Zeile ohne vorherigen Access-Specification-Namen.", lineno); continue; }
                char *f[16];
                int n = split(buf, f, 16);
                long long v[8];
                int ok = n == 8;
                for (int i = 0; ok && i < 8; i++) ok = parse_ll(f[i], &v[i]);
                if (!ok) {
                    sb_verrs_add(e, "", "E_ICF_PARSE", "Zeile %d: erwartet 8 Zahlen (size,%% of size,%% reads,%% random,delay,burst,align,reply), gelesen: '%s'.", lineno, s);
                    continue;
                }
                if (cs->n >= ICF_MAX_LINES) {
                    sb_verrs_add(e, "", "E_ICF_VALUE", "Zeile %d: '%s' hat mehr als %d Zeilen.", lineno, cs->name, ICF_MAX_LINES);
                    continue;
                }
                const char *bad = v[0] < 1 || v[0] > (1LL << 30) ? "size (1 Byte bis 1 GiB)"
                                : v[1] < 0 || v[1] > 100 ? "% of size (0-100)"
                                : v[2] < 0 || v[2] > 100 ? "% reads (0-100)"
                                : v[3] < 0 || v[3] > 100 ? "% random (0-100)"
                                : v[4] < 0 || v[4] > 0x7fffffffLL ? "delay (>= 0)"
                                : v[5] < 0 || v[5] > 0x7fffffffLL ? "burst (>= 0)"
                                : v[6] < 0 || v[6] > (1LL << 30) ? "align (>= 0)"
                                : v[7] < 0 ? "reply (>= 0)" : NULL;
                if (bad) {
                    sb_verrs_add(e, "", "E_ICF_VALUE", "Zeile %d: Wert für %s außerhalb des gültigen Bereichs: '%s'.", lineno, bad, s);
                    continue;
                }
                icf_line *L = &cs->l[cs->n++];
                L->size = v[0]; L->pct = v[1]; L->rd = v[2]; L->rnd = v[3];
                L->delay = v[4]; L->burst = v[5]; L->align = v[6]; L->reply = v[7];
                L->line = lineno;
            }
            continue;
        }
        /* SEC_MANAGERS */
        if (ieq_prefix(key, "Manager ID")) {
            if (d->nmgr >= ICF_MAX_MGR) { sb_verrs_add(e, "", "E_ICF_VALUE", "Zeile %d: mehr als %d Manager.", lineno, ICF_MAX_MGR); return 2; }
            icf_mgr *M = &d->mgr[d->nmgr++];
            char *c = strchr(buf, ',');
            cpy(M->name, sizeof M->name, c ? trim(c + 1) : trim(buf));
            M->line = lineno;
            M->first_worker = d->nw;
            M->nworkers = 0;
            cw = NULL;
        } else if (ieq(key, "Worker")) {
            if (!d->nmgr) { sb_verrs_add(e, "", "E_ICF_PARSE", "Zeile %d: Worker ohne Manager.", lineno); continue; }
            if (d->nw >= ICF_MAX_WORKERS) { sb_verrs_add(e, "", "E_ICF_VALUE", "Zeile %d: mehr als %d Worker.", lineno, ICF_MAX_WORKERS); return 2; }
            cw = &d->w[d->nw++];
            memset(cw, 0, sizeof *cw);
            cpy(cw->name, sizeof cw->name, s);
            cw->line = lineno;
            cw->qd = 1;
            d->mgr[d->nmgr - 1].nworkers++;
        } else if (!cw) {
            continue; /* Manager-Zeilen wie die Netzwerkadresse */
        } else if (ieq(key, "Worker type")) {
            cpy(cw->type, sizeof cw->type, s);
        } else if (ieq_prefix(key, "Number of outstanding IOs")) {
            char hk[256], *hf[16], *f[16];
            cpy(hk, sizeof hk, key);
            int nh = split(hk, hf, 16), n = split(buf, f, 16);
            for (int i = 0; i < nh && i < n; i++) {
                long long v;
                if (ieq(hf[i], "Number of outstanding IOs")) {
                    if (!parse_ll(f[i], &v) || v < 1 || v > 1024) {
                        sb_verrs_add(e, "", "E_ICF_VALUE", "Zeile %d: Outstanding I/Os muss 1-1024 sein, gelesen: '%s'.", lineno, f[i]);
                        continue;
                    }
                    cw->qd = v; cw->has_qd = 1;
                } else if (ieq(hf[i], "test connection rate")) cpy(cw->conn_rate, sizeof cw->conn_rate, f[i]);
                else if (ieq(hf[i], "use fixed seed")) cpy(cw->fixed_seed, sizeof cw->fixed_seed, f[i]);
            }
        } else if (ieq_prefix(key, "Disk maximum size")) {
            char hk[256], *hf[16], *f[16];
            cpy(hk, sizeof hk, key);
            int nh = split(hk, hf, 16), n = split(buf, f, 16);
            cw->has_disk = 1;
            cw->disk_line = lineno;
            for (int i = 0; i < nh && i < n; i++) {
                long long v;
                if (!parse_ll(f[i], &v) || v < 0) {
                    sb_verrs_add(e, "", "E_ICF_VALUE", "Zeile %d: '%s' muss eine Zahl >= 0 sein, gelesen: '%s'.", lineno, hf[i], f[i]);
                    continue;
                }
                if (ieq(hf[i], "Disk maximum size")) cw->max_size = v;
                else if (ieq(hf[i], "starting sector")) cw->start_sector = v;
                else if (ieq(hf[i], "Data pattern")) { cw->pattern = v; cw->has_pattern = 1; }
            }
        } else if (ieq(key, "Assigned access specs")) {
            int k = find_spec(d, s);
            if (k < 0) {
                sb_verrs_add(e, "", "E_ICF_SPEC", "Zeile %d: Worker '%s' verweist auf unbekannte Access Specification '%s'.", lineno, cw->name, s);
                continue;
            }
            if (cw->nassigned >= ICF_MAX_ASSIGN) { sb_verrs_add(e, "", "E_ICF_VALUE", "Zeile %d: zu viele zugewiesene Access Specifications.", lineno); continue; }
            cw->assigned[cw->nassigned++] = k;
        } else if (ieq(key, "Target")) {
            if (cw->nt >= ICF_MAX_TGT) { sb_verrs_add(e, "", "E_ICF_VALUE", "Zeile %d: Worker '%s' hat mehr als %d Targets.", lineno, cw->name, ICF_MAX_TGT); continue; }
            icf_target *T = &cw->t[cw->nt++];
            memset(T, 0, sizeof *T);
            cpy(T->name, sizeof T->name, s);
            T->line = lineno;
        } else if (ieq(key, "Target type")) {
            if (cw->nt) cpy(cw->t[cw->nt - 1].type, sizeof cw->t[0].type, s);
        }
    }
    if (!saw_version) {
        sb_verrs_add(e, "", "E_ICF_FORMAT", "Datei ist leer oder keine Iometer-Konfigurationsdatei.");
        return 2;
    }
    if (sec == SEC_SPECS || sec == SEC_MANAGERS) {
        sb_verrs_add(e, "", "E_ICF_FORMAT", "Datei endet mitten im Abschnitt '%s' (ab Zeile %d); abgeschnitten?", secname, sec_line);
        return 2;
    }
    if (!saw_specs || !d->nspecs) {
        sb_verrs_add(e, "", "E_ICF_FORMAT", "Keine Access Specifications gefunden (Abschnitt 'ACCESS SPECIFICATIONS' fehlt oder ist leer).");
        return 2;
    }
    for (int i = 0; i < d->nspecs; i++) {
        icf_spec *S = &d->specs[i];
        long long sum = 0;
        for (int j = 0; j < S->n; j++) sum += S->l[j].pct;
        if (!S->n)
            sb_verrs_add(e, "", "E_ICF_SPEC", "Zeile %d: Access Specification '%s' hat keine Zeilen.", S->line, S->name);
        else if (sum != 100)
            sb_verrs_add(e, "", "E_ICF_SPEC", "Zeile %d: Access Specification '%s': Summe von '%% of size' ist %lld, erwartet 100.", S->line, S->name, sum);
    }
    return e->n > nerr0 ? 2 : 0;
}

/* ---- Abbildung --------------------------------------------------------- */

typedef struct { char path[260]; int kind; unsigned long long file_size; int count; const icf_worker *w; const icf_target *t; } map_target;

static int is_disk_worker(const icf_worker *w) { return !w->type[0] || ieq_prefix(w->type, "DISK"); }

/* ICF-Target -> sb-Pfad. 1 = ok, 0 = nicht abbildbar */
static int map_icf_target(const char *name, char *path, size_t n, int *kind) {
    if (isalpha((unsigned char)name[0]) && name[1] == ':' && (!name[2] || name[2] == ' ' || name[2] == '\t' || name[2] == '\\')) {
        snprintf(path, n, "%c:\\iobw.tst", toupper((unsigned char)name[0]));
        *kind = 0;
        return 1;
    }
    if (ieq_prefix(name, "PHYSICALDRIVE:")) {
        const char *q = name + 14;
        char num[16];
        size_t k = 0;
        while (isdigit((unsigned char)q[k]) && k < sizeof num - 1) { num[k] = q[k]; k++; }
        num[k] = 0;
        if (!k) return 0;
        snprintf(path, n, "\\\\.\\PhysicalDrive%s", num);
        *kind = 2;
        return 1;
    }
    return 0;
}

static int kind_of_path(const char *p) {
    if (ieq_prefix(p, "\\\\.\\PhysicalDrive")) return 2;
    if (p[0] == '\\' && p[1] == '\\' && p[2] == '.' && p[3] == '\\' && isalpha((unsigned char)p[4]) && p[5] == ':' && !p[6]) return 1;
    return 0;
}

static const char *const KIND_NAMES[] = {"file", "volume", "physical"};

int sb_icf_convert(const char *text, size_t len, const sb_icf_opts *o, cJSON **job, sb_verrs *e, sb_verrs *w) {
    *job = NULL;
    sb_icf_opts none = {0};
    if (!o) o = &none;
    char *t = to_utf8(text, len);
    icf_doc *d = (icf_doc *)calloc(1, sizeof *d);
    if (d) {
        d->specs = (icf_spec *)calloc(ICF_MAX_SPECS, sizeof(icf_spec));
        d->w = (icf_worker *)calloc(ICF_MAX_WORKERS, sizeof(icf_worker));
    }
    map_target *mt = (map_target *)calloc(ICF_MAX_TGT, sizeof *mt);
    int rc = 2;
    if (!t || !d || !d->specs || !d->w || !mt) {
        sb_verrs_add(e, "", "E_OUT_OF_MEMORY", "Zu wenig Speicher für den ICF-Import.");
        rc = 1;
        goto out;
    }
    if (parse_doc(d, t, e, w)) goto out;

    /* Manager / Worker */
    if (d->nmgr > 1)
        sb_verrs_add(w, "", "W_ICF_MANAGERS", "Das ICF enthält %d Manager; übernommen werden nur die Worker von '%s' (Zeile %d), sbench misst lokal.",
                     d->nmgr, d->mgr[0].name, d->mgr[0].line);
    int w0 = d->nmgr ? d->mgr[0].first_worker : 0, wn = d->nmgr ? d->mgr[0].nworkers : 0;
    const icf_worker *act[ICF_MAX_WORKERS];
    int nact = 0;
    const icf_worker *with_specs = NULL;
    for (int i = w0; i < w0 + wn; i++) {
        const icf_worker *W = &d->w[i];
        if (!is_disk_worker(W)) {
            sb_verrs_add(w, "", "W_ICF_NETWORK_WORKER", "Zeile %d: Worker '%s' (Typ %s) wird ignoriert; Netzwerktests bitte als net_client/net_server anlegen.",
                         W->line, W->name, W->type);
            continue;
        }
        if (W->nassigned && !with_specs) with_specs = W;
        if (W->nt) act[nact++] = W;
    }
    /* Reihenfolge der Tests: Access Specs des ersten aktiven Disk-Workers */
    const icf_worker *ref = NULL;
    for (int i = 0; i < nact; i++) if (act[i]->nassigned) { ref = act[i]; break; }
    if (!ref) ref = with_specs;
    int seq[ICF_MAX_ASSIGN], nseq = 0;
    if (ref) {
        for (int i = 0; i < ref->nassigned; i++) seq[nseq++] = ref->assigned[i];
        for (int i = 0; i < nact; i++) {
            const icf_worker *W = act[i];
            if (W == ref || !W->nassigned) continue;
            int same = W->nassigned == ref->nassigned;
            for (int k = 0; same && k < W->nassigned; k++) same = W->assigned[k] == ref->assigned[k];
            if (!same)
                sb_verrs_add(w, "", "W_ICF_SPEC_MIXED", "Zeile %d: Worker '%s' hat andere Access Specs als '%s'; sbench nutzt für alle Worker die von '%s'.",
                             W->line, W->name, ref->name, ref->name);
        }
    } else {
        for (int i = 0; i < d->nspecs && nseq < ICF_MAX_ASSIGN; i++) seq[nseq++] = i;
        if (wn)
            sb_verrs_add(w, "", "W_ICF_NO_ASSIGNMENT", "Kein Worker hat zugewiesene Access Specs; alle %d Access Specifications werden als eigene Tests übernommen.", d->nspecs);
    }

    /* Targets und Worker-Paare */
    int nmt = 0, npairs = 0, max_tpw = 0;
    long long qd = 1;
    int qd_set = 0;
    for (int i = 0; i < nact; i++) {
        const icf_worker *W = act[i];
        if (!qd_set) { qd = W->qd; qd_set = 1; }
        else if (W->qd != qd)
            sb_verrs_add(w, "/tests", "W_ICF_QD_MIXED", "Zeile %d: Worker '%s' hat %lld Outstanding I/Os, übernommen wird %lld (queue_depth gilt in sbench für alle Worker).",
                         W->line, W->name, W->qd, qd);
        if (W->nt > max_tpw) max_tpw = W->nt;
        if (W->start_sector)
            sb_verrs_add(w, "", "W_ICF_UNSUPPORTED", "Zeile %d: Starting Sector %lld (Worker '%s') hat keine Entsprechung; sbench nutzt das ganze Ziel ab Offset 0.",
                         W->disk_line, W->start_sector, W->name);
        if (ieq(W->fixed_seed, "ENABLED"))
            sb_verrs_add(w, "", "W_ICF_UNSUPPORTED", "Worker '%s': 'use fixed seed' wird ignoriert (sbench nutzt eigene Startwerte).", W->name);
        if (ieq(W->conn_rate, "ENABLED"))
            sb_verrs_add(w, "", "W_ICF_UNSUPPORTED", "Worker '%s': 'test connection rate' hat keine Entsprechung und wird ignoriert.", W->name);
        for (int k = 0; k < W->nt; k++) {
            const icf_target *T = &W->t[k];
            char path[260];
            int kind;
            if (!map_icf_target(T->name, path, sizeof path, &kind)) {
                sb_verrs_add(w, "", "W_ICF_UNSUPPORTED", "Zeile %d: Target '%s' ist weder Laufwerk (X:) noch PHYSICALDRIVE:N und wird ignoriert.", T->line, T->name);
                continue;
            }
            int m = 0;
            while (m < nmt && !ieq(mt[m].path, path)) m++;
            if (m == nmt) {
                if (nmt >= ICF_MAX_TGT) continue;
                cpy(mt[m].path, sizeof mt[m].path, path);
                mt[m].kind = kind;
                mt[m].w = W;
                mt[m].t = T;
                if (kind == 0) {
                    if (W->max_size > 0) mt[m].file_size = (unsigned long long)W->max_size * 512ULL;
                    else {
                        mt[m].file_size = ICF_DEFAULT_FILE_SIZE;
                    }
                } else if (W->max_size > 0)
                    sb_verrs_add(w, "", "W_ICF_DISK_SIZE", "Zeile %d: Disk maximum size %lld Sektoren gilt nicht für physische Disks; sbench testet %s vollständig.",
                                 T->line, W->max_size, path);
                nmt++;
            }
            mt[m].count++;
            npairs++;
        }
    }
    if (max_tpw > 1)
        sb_verrs_add(w, "/tests", "W_ICF_WORKER_SPLIT", "Ein Iometer-Worker mit mehreren Targets wird zu einem sbench-Worker je Target (je %lld Outstanding I/Os); die Summe der ausstehenden I/Os bleibt gleich.", qd);
    for (int m = 1; m < nmt; m++)
        if (mt[m].count != mt[0].count) {
            sb_verrs_add(w, "/tests", "W_ICF_TARGET_LAYOUT", "Die Targets haben unterschiedlich viele Worker; sbench verteilt %d Worker reihum auf %d Targets.", npairs, nmt);
            break;
        }

    /* --target ersetzt */
    int placeholder = 0;
    if (o->ntargets) {
        if (nmt && o->ntargets != nmt) {
            sb_verrs_add(e, "", "E_ICF_TARGET_COUNT", "Das ICF hat %d Target(s), --target wurde %d-mal angegeben; die Anzahl muss gleich sein.", nmt, o->ntargets);
            goto out;
        }
        if (o->ntargets > ICF_MAX_TGT) {
            sb_verrs_add(e, "", "E_ICF_TARGET_COUNT", "Höchstens %d Targets.", ICF_MAX_TGT);
            goto out;
        }
        int had = nmt;
        for (int m = 0; m < o->ntargets; m++) {
            int kind = kind_of_path(o->targets[m]);
            unsigned long long fs = m < had && mt[m].kind == 0 ? mt[m].file_size : ICF_DEFAULT_FILE_SIZE;
            if (m < had && mt[m].kind == 0 && mt[m].w && mt[m].w->max_size <= 0 && kind == 0)
                sb_verrs_add(w, "", "W_ICF_DISK_SIZE", "Disk maximum size 0 (ganzes Laufwerk) bei %s: Testdatei wird mit 8 GiB angelegt, bitte file_size prüfen.", o->targets[m]);
            else if (m >= had && kind == 0)
                sb_verrs_add(w, "", "W_ICF_DISK_SIZE", "Keine Größe im ICF für %s: Testdatei wird mit 8 GiB angelegt, bitte file_size prüfen.", o->targets[m]);
            cpy(mt[m].path, sizeof mt[m].path, o->targets[m]);
            mt[m].kind = kind;
            mt[m].file_size = fs;
            if (m >= had) mt[m].count = 1;
        }
        if (!had) { nmt = o->ntargets; npairs = nmt; }
    } else {
        for (int m = 0; m < nmt; m++)
            if (mt[m].kind == 0 && mt[m].w->max_size <= 0)
                sb_verrs_add(w, "", "W_ICF_DISK_SIZE", "Zeile %d: Disk maximum size 0 (Iometer füllt das Laufwerk): Testdatei %s wird mit 8 GiB angelegt, bitte file_size prüfen.",
                             mt[m].t->line, mt[m].path);
        if (!nmt) {
            placeholder = 1;
            cpy(mt[0].path, sizeof mt[0].path, "D:\\sbtest.dat");
            mt[0].kind = 0;
            mt[0].file_size = ICF_DEFAULT_FILE_SIZE;
            nmt = 1;
            npairs = 1;
            sb_verrs_add(w, "", "W_ICF_NO_TARGET", "Das ICF enthält keinen Disk-Worker mit Target; Platzhalter D:\\sbtest.dat (8 GiB) eingesetzt, bitte mit --target ein Ziel angeben.");
        }
    }
    (void)placeholder;
    if (!nact && wn == 0 && d->nmgr == 0)
        sb_verrs_add(w, "", "W_ICF_NO_WORKERS", "Das ICF enthält keine Manager-Liste; workers und queue_depth werden mit 1 je Target angenommen.");

    /* Test Setup */
    long long dur = d->run_s;
    if (!d->has_run || dur <= 0) {
        dur = ICF_DEFAULT_DURATION;
        sb_verrs_add(w, "/tests", "W_ICF_RUN_TIME", d->has_run ? "Zeile %d: Run Time 0 (unbegrenzt) ist nicht abbildbar; duration_s = %d." : "Keine Run Time im ICF; duration_s = %d.",
                     d->has_run ? d->run_line : ICF_DEFAULT_DURATION, ICF_DEFAULT_DURATION);
    }
    if (d->test_type[0] && !ieq(d->test_type, "NORMAL"))
        sb_verrs_add(w, "/tests", "W_ICF_CYCLING", "Test Type '%s' (Cycling) wird nicht nachgebildet; es läuft ein Durchgang mit den eingestellten Startwerten.", d->test_type);

    int writes = 0, align0 = 0;
    for (int i = 0; i < nseq; i++)
        for (int j = 0; j < d->specs[seq[i]].n; j++) {
            const icf_line *L = &d->specs[seq[i]].l[j];
            if (L->rd < 100 && L->pct) writes = 1;
            if (!L->align && L->rnd && L->size > 512 && L->pct) align0 = 1;
        }
    if (writes) {
        for (int i = 0; i < nact; i++)
            if (act[i]->has_pattern && act[i]->pattern != 2) {
                sb_verrs_add(w, "", "W_ICF_DATA_PATTERN", "Zeile %d: Data pattern %lld (%s) wird nicht nachgebildet; sbench schreibt immer zufällige Daten (Iometer 'Full random').",
                             act[i]->disk_line, act[i]->pattern, act[i]->pattern == 0 ? "Repeating bytes" : "Pseudo random");
                break;
            }
    }
    if (align0)
        sb_verrs_add(w, "", "W_ICF_ALIGN", "Align 0 (Iometer: Sektorgrenzen) wird ohne 'align' übernommen; sbench richtet zufällige Offsets dann an der block_size aus.");

    cJSON *J = cJSON_CreateObject();
    cJSON_AddStringToObject(J, "schema", "sb-job/1");
    cJSON_AddStringToObject(J, "name", d->desc[0] ? d->desc : o->job_name && *o->job_name ? o->job_name : "Iometer-Import");
    cJSON *tests = cJSON_AddArrayToObject(J, "tests");
    int workers = npairs < 1 ? 1 : npairs;
    if (workers > 4096) workers = 4096;
    for (int i = 0; i < nseq; i++) {
        const icf_spec *S = &d->specs[seq[i]];
        cJSON *T = cJSON_CreateObject();
        cJSON_AddStringToObject(T, "type", "storage");
        cJSON_AddStringToObject(T, "name", S->name);
        cJSON *ta = cJSON_AddArrayToObject(T, "targets");
        for (int m = 0; m < nmt; m++) {
            cJSON *x = cJSON_CreateObject();
            cJSON_AddStringToObject(x, "path", mt[m].path);
            cJSON_AddStringToObject(x, "kind", KIND_NAMES[mt[m].kind]);
            if (mt[m].kind == 0) add_size(x, "file_size", mt[m].file_size);
            cJSON_AddItemToArray(ta, x);
        }
        cJSON_AddNumberToObject(T, "workers", workers);
        cJSON_AddNumberToObject(T, "queue_depth", (double)qd);
        if (d->ramp_s > 0) cJSON_AddNumberToObject(T, "warmup_s", (double)d->ramp_s);
        cJSON_AddNumberToObject(T, "duration_s", (double)dur);
        cJSON_AddStringToObject(T, "cache", "unbuffered");
        cJSON *as = cJSON_AddArrayToObject(T, "access_specs");
        int ti = i, k = 0;
        for (int j = 0; j < S->n; j++) {
            const icf_line *L = &S->l[j];
            char ptr[96];
            if (!L->pct) {
                sb_verrs_add(w, "", "W_ICF_ZERO_WEIGHT", "Zeile %d: Zeile mit 0 %% of size in '%s' wird weggelassen.", L->line, S->name);
                continue;
            }
            snprintf(ptr, sizeof ptr, "/tests/%d/access_specs/%d", ti, k);
            cJSON *x = cJSON_CreateObject();
            cJSON_AddNumberToObject(x, "weight", (double)L->pct);
            add_size(x, "block_size", (unsigned long long)L->size);
            cJSON_AddNumberToObject(x, "read_pct", (double)L->rd);
            cJSON_AddNumberToObject(x, "random_pct", (double)L->rnd);
            if (L->align) add_size(x, "align", (unsigned long long)L->align);
            if (L->delay) {
                cJSON_AddNumberToObject(x, "delay_ms", (double)L->delay);
                cJSON_AddNumberToObject(x, "burst_len", (double)(L->burst < 1 ? 1 : L->burst));
            } else if (L->burst > 1)
                sb_verrs_add(w, ptr, "W_ICF_UNSUPPORTED", "Zeile %d: Burst %lld ohne Delay hat keine Wirkung und wird weggelassen.", L->line, L->burst);
            if (L->size % 512)
                sb_verrs_add(w, ptr, "W_ICF_SECTOR", "Zeile %d: size %lld ist kein Vielfaches von 512; ungepuffert (cache = unbuffered) nicht ausführbar.", L->line, L->size);
            if (L->align % 512)
                sb_verrs_add(w, ptr, "W_ICF_SECTOR", "Zeile %d: align %lld ist kein Vielfaches von 512; ungepuffert (cache = unbuffered) nicht ausführbar.", L->line, L->align);
            if (L->reply)
                sb_verrs_add(w, ptr, "W_ICF_REPLY", "Zeile %d: Reply %lld (nur für Netzwerk-Worker) hat keine Entsprechung und wird ignoriert.", L->line, L->reply);
            cJSON_AddItemToArray(as, x);
            k++;
        }
        cJSON_AddItemToArray(tests, T);
    }
    *job = J;
    rc = 0;
out:
    free(mt);
    if (d) { free(d->specs); free(d->w); }
    free(d);
    free(t);
    return rc;
}

/* ---- Öffentliche API: lesen, umwandeln, erzeugten Job prüfen ------------- */
#include "sb/sb.h"

/* Diese Prüffehler hängen von der Umgebung ab (Ziel fehlt hier, Sektorgröße, Schreibschutz)
 * und werden beim Import nur gewarnt; der Job ist trotzdem gültig erzeugt. */
static int env_dependent(const char *code) {
    static const char *const C[] = {"E_TARGET_NOT_FOUND", "E_DESTRUCTIVE_NOT_ALLOWED", "E_TARGET_SIZE", "E_BLOCK_ALIGN", "E_IO", NULL};
    for (int i = 0; C[i]; i++) if (!strcmp(code, C[i])) return 1;
    return 0;
}

static void add_list(cJSON *o, const char *key, const sb_verrs *e) {
    cJSON *a = cJSON_AddArrayToObject(o, key);
    for (int i = 0; e && i < e->n; i++) {
        cJSON *x = cJSON_CreateObject();
        cJSON_AddStringToObject(x, "path", e->v[i].pointer);
        cJSON_AddStringToObject(x, "code", e->v[i].code);
        cJSON_AddStringToObject(x, "msg", e->v[i].msg);
        cJSON_AddItemToArray(a, x);
    }
}

static const char *base_noext(const char *p, char *b, size_t n) {
    const char *s = p;
    for (const char *q = p; *q; q++) if (*q == '\\' || *q == '/' || *q == ':') s = q + 1;
    cpy(b, n, s);
    char *dot = strrchr(b, '.');
    if (dot && dot != b) *dot = 0;
    return b;
}

int sb_import_icf(const sb_import_icf_options *o, char **job_json, char **report_json) {
    sb_verrs e = {0}, w = {0};
    int rc;
    *job_json = NULL;
    *report_json = NULL;
    char *txt = NULL;
    size_t len = 0;
    if (!o || !o->icf_path) {
        sb_verrs_add(&e, "", "E_ICF_FORMAT", "Keine ICF-Datei angegeben.");
        rc = SB_EXIT_INVALID;
    } else if (sb_read_file(o->icf_path, &txt, &len)) {
        sb_verrs_add(&e, "", "E_IO", "%s ist nicht lesbar.", o->icf_path);
        rc = SB_EXIT_ERROR;
    } else {
        char nm[128];
        sb_icf_opts io = {(const char *const *)o->targets, o->ntargets, base_noext(o->icf_path, nm, sizeof nm)};
        cJSON *job = NULL;
        rc = sb_icf_convert(txt, len, &io, &job, &e, &w);
        if (rc == 0 && job) {
            char *jt = cJSON_Print(job);
            cJSON_Delete(job);
            sb_job j;
            sb_verrs ve = {0}, vw = {0};
            sb_job_load_text(&j, jt, &ve, &vw);
            for (int i = 0; i < ve.n; i++) {
                if (env_dependent(ve.v[i].code))
                    sb_verrs_add(&w, ve.v[i].pointer, "W_ICF_VALIDATE", "%s: %s", ve.v[i].code, ve.v[i].msg);
                else
                    sb_verrs_add(&e, ve.v[i].pointer, ve.v[i].code, "%s", ve.v[i].msg);
            }
            for (int i = 0; i < vw.n; i++) sb_verrs_add(&w, vw.v[i].pointer, vw.v[i].code, "%s", vw.v[i].msg);
            sb_verrs_free(&ve);
            sb_verrs_free(&vw);
            sb_job_free(&j);
            if (e.n) { free(jt); rc = SB_EXIT_INVALID; }
            else *job_json = jt;
        } else if (rc == 0) rc = SB_EXIT_INVALID;
    }
    free(txt);
    cJSON *d = cJSON_CreateObject();
    cJSON_AddStringToObject(d, "schema", "sb-validate/1");
    cJSON_AddBoolToObject(d, "ok", e.n == 0);
    add_list(d, "errors", &e);
    add_list(d, "warnings", &w);
    *report_json = cJSON_Print(d);
    cJSON_Delete(d);
    sb_verrs_free(&e);
    sb_verrs_free(&w);
    return rc;
}
