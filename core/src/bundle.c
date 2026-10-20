/* sbench bundle (docs/cli.md, Vertrag 1.1): ZIP mit manifest.json (sb-bundle/1), info.json,
 * results/, runs/, logs/, jobs/ und extra/<ordnername>/ (--include). Optional --anonymize. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "sb/sb.h"
#include "sb/util.h"
#include "sb/sha256.h"
#include "sb/info.h"
#include "sb/progress.h"
#include "sb/runs.h"
#include "cJSON.h"
#include "miniz.h"
#include <winioctl.h>

typedef struct bfile { char *src; char *arc; uint64_t size; } bfile;
typedef struct blist { bfile *v; int n, cap; } blist;

static void bl_add(blist *l, const char *src, const char *arc) {
    for (int i = 0; i < l->n; i++) if (!strcmp(l->v[i].arc, arc)) return;   /* Duplikate vermeiden */
    if (l->n == l->cap) {
        int nc = l->cap ? l->cap * 2 : 32;
        bfile *nv = (bfile *)realloc(l->v, (size_t)nc * sizeof *nv);
        if (!nv) return;
        l->v = nv; l->cap = nc;
    }
    l->v[l->n].src = sb_strdup(src);
    l->v[l->n].arc = sb_strdup(arc);
    l->v[l->n].size = 0;
    wchar_t *w = sb_u8_to_w(src);
    WIN32_FILE_ATTRIBUTE_DATA fa;
    if (w && GetFileAttributesExW(w, GetFileExInfoStandard, &fa))
        l->v[l->n].size = ((uint64_t)fa.nFileSizeHigh << 32) | fa.nFileSizeLow;
    free(w);
    l->n++;
}

static void bl_add_if(blist *l, const char *src, const char *arc) { if (sb_file_exists(src)) bl_add(l, src, arc); }

/* Ergebnis, Fortschrittsdatei, Log und Job-Kopie eines Laufs */
static void add_run(blist *l, const char *dd, const char *run_id, const char *result_src) {
    char *a;
    if (result_src && sb_file_exists(result_src)) {
        a = sb_aprintf("results/%s.json", run_id); bl_add(l, result_src, a); free(a);
    }
    const char *kinds[3][3] = {{"runs", ".jsonl", "runs"}, {"logs", ".log", "logs"}, {"jobs", ".json", "jobs"}};
    for (int k = 0; k < 3; k++) {
        char *src = sb_aprintf("%s\\%s\\%s%s", dd, kinds[k][0], run_id, kinds[k][1]);
        a = sb_aprintf("%s/%s%s", kinds[k][2], run_id, kinds[k][1]);
        if (src && a) bl_add_if(l, src, a);
        free(src); free(a);
    }
}

/* Ordner rekursiv -> arcdir/... */
static void add_tree(blist *l, const char *dir, const char *arcdir, int depth) {
    if (depth > 32) return;
    char *pat = sb_aprintf("%s\\*", dir);
    wchar_t *w = pat ? sb_u8_to_w(pat) : NULL;
    free(pat);
    if (!w) return;
    WIN32_FIND_DATAW fd;
    HANDLE f = FindFirstFileW(w, &fd);
    free(w);
    if (f == INVALID_HANDLE_VALUE) return;
    do {
        if (!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) continue;   /* keine Junctions folgen */
        char *n8 = sb_w_to_u8(fd.cFileName);
        if (!n8) continue;
        char *src = sb_aprintf("%s\\%s", dir, n8), *arc = sb_aprintf("%s/%s", arcdir, n8);
        if (src && arc) {
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) add_tree(l, src, arc, depth + 1);
            else bl_add(l, src, arc);
        }
        free(src); free(arc); free(n8);
    } while (FindNextFileW(f, &fd));
    FindClose(f);
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

/* run_id zu einer Ergebnisdatei: über runs\index.jsonl (out), sonst Dateiname ohne Endung */
static char *run_id_for(const cJSON *index, const char *path) {
    {
        char *t = NULL;
        size_t n = 0;
        if (sb_read_file(path, &t, &n) == 0 && t) {
            cJSON *j = cJSON_ParseWithLength(t, n);
            const cJSON *id = j ? cJSON_GetObjectItemCaseSensitive(j, "run_id") : NULL;
            char *r = NULL;
            if (cJSON_IsString(id) && id->valuestring[0] && strlen(id->valuestring) <= 64 &&
                strspn(id->valuestring, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-") == strlen(id->valuestring))
                r = sb_strdup(id->valuestring);
            cJSON_Delete(j);
            free(t);
            if (r) return r;
        }
    }
    char *fp = full_path(path);
    const cJSON *it;
    cJSON_ArrayForEach(it, index) {
        const cJSON *out = cJSON_GetObjectItemCaseSensitive(it, "out");
        const cJSON *id = cJSON_GetObjectItemCaseSensitive(it, "run_id");
        if (cJSON_IsString(out) && cJSON_IsString(id) && fp && !_stricmp(out->valuestring, fp)) {
            free(fp);
            return sb_strdup(id->valuestring);
        }
    }
    free(fp);
    char *b = sb_strdup(sb_basename(path));
    if (b) { char *d = strrchr(b, '.'); if (d) *d = 0; }
    return b;
}

/* ---- Anonymisierung ----------------------------------------------------------- */

typedef struct amap { char **from, **to; int n, cap, next; const char *fmt; } amap;
typedef struct anon {
    amap host, user, ip4, ip6, mac, serial;
} anon;

static const char *amap_get(amap *m, const char *v, int ci) {
    for (int i = 0; i < m->n; i++) if (ci ? !_stricmp(m->from[i], v) : !strcmp(m->from[i], v)) return m->to[i];
    if (m->n == m->cap) {
        int nc = m->cap ? m->cap * 2 : 16;
        char **f = (char **)realloc(m->from, (size_t)nc * sizeof(char *));
        if (!f) return "x";
        m->from = f;
        char **t = (char **)realloc(m->to, (size_t)nc * sizeof(char *));
        if (!t) return "x";
        m->to = t; m->cap = nc;
    }
    int k = ++m->next;
    char buf[64];
    if (!strcmp(m->fmt, "ip4")) snprintf(buf, sizeof buf, "10.0.%d.%d", k / 254, k % 254 + 1);
    else if (!strcmp(m->fmt, "ip6")) snprintf(buf, sizeof buf, "fd00::%x", k);
    else if (!strcmp(m->fmt, "mac")) snprintf(buf, sizeof buf, "02:00:00:00:%02x:%02x", (k >> 8) & 0xff, k & 0xff);
    else snprintf(buf, sizeof buf, "%s-%d", m->fmt, k);
    m->from[m->n] = sb_strdup(v);
    m->to[m->n] = sb_strdup(buf);
    return m->to[m->n++];
}

static void amap_free(amap *m) {
    for (int i = 0; i < m->n; i++) { free(m->from[i]); free(m->to[i]); }
    free(m->from); free(m->to);
}

/* nur registrieren (Pseudonym vergeben) */
static void amap_note(amap *m, const char *v, int ci) { if (v && strlen(v) >= 2) amap_get(m, v, ci); }

typedef struct sbuf { char *p; size_t n, cap; } sbuf;
static void sb_put(sbuf *b, const char *s, size_t n) {
    if (b->n + n + 1 > b->cap) {
        size_t nc = (b->cap ? b->cap * 2 : 4096);
        while (nc < b->n + n + 1) nc *= 2;
        char *q = (char *)realloc(b->p, nc);
        if (!q) return;
        b->p = q; b->cap = nc;
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
    b->p[b->n] = 0;
}

static int ishex(int c) { return isxdigit((unsigned char)c) != 0; }
static int isword(int c) { return isalnum((unsigned char)c) || c == '-' || c == '_'; }

/* MAC xx:xx:xx:xx:xx:xx oder xx-xx-...; Länge oder 0 */
static size_t match_mac(const char *s, size_t i, size_t n) {
    if (i + 17 > n) return 0;
    if (i > 0 && (ishex(s[i - 1]) || s[i - 1] == ':' || s[i - 1] == '-')) return 0;
    char sep = s[i + 2];
    if (sep != ':' && sep != '-') return 0;
    for (int g = 0; g < 6; g++) {
        size_t p = i + (size_t)g * 3;
        if (!ishex(s[p]) || !ishex(s[p + 1])) return 0;
        if (g < 5 && s[p + 2] != sep) return 0;
    }
    if (i + 17 < n && (ishex(s[i + 17]) || s[i + 17] == sep)) return 0;
    return 17;
}

static size_t match_ip4(const char *s, size_t i, size_t n, int *skip) {
    *skip = 0;
    if (i > 0 && (isdigit((unsigned char)s[i - 1]) || s[i - 1] == '.')) return 0;
    size_t p = i;
    int parts[4];
    for (int g = 0; g < 4; g++) {
        int v = 0, d = 0;
        while (p < n && isdigit((unsigned char)s[p]) && d < 4) { v = v * 10 + (s[p] - '0'); p++; d++; }
        if (!d || d > 3 || v > 255) return 0;
        parts[g] = v;
        if (g < 3) { if (p >= n || s[p] != '.') return 0; p++; }
    }
    if (p < n && (isdigit((unsigned char)s[p]) || (s[p] == '.' && p + 1 < n && isdigit((unsigned char)s[p + 1])))) return 0;
    /* nicht identifizierend: 0.0.0.0, Loopback, Broadcast */
    if ((parts[0] == 0 && parts[1] == 0 && parts[2] == 0 && parts[3] == 0) || parts[0] == 127 ||
        (parts[0] == 255 && parts[1] == 255 && parts[2] == 255 && parts[3] == 255)) *skip = 1;
    return p - i;
}

static size_t match_ip6(const char *s, size_t i, size_t n) {
    if (i > 0 && (ishex(s[i - 1]) || s[i - 1] == ':' || isword(s[i - 1]))) return 0;
    size_t p = i;
    int colons = 0, dbl = 0, grp = 0, hexd = 0;
    while (p < n && (ishex(s[p]) || s[p] == ':')) {
        if (s[p] == ':') {
            colons++;
            if (p + 1 < n && s[p + 1] == ':') { if (dbl) return 0; dbl = 1; colons++; p += 2; grp = 0; continue; }
            grp = 0;
        } else {
            if (++grp > 4) return 0;
            hexd = 1;
        }
        p++;
    }
    if (p < n && (isalnum((unsigned char)s[p]) && s[p] != '%')) return 0;
    if (!hexd || colons < 2 || (!dbl && colons != 7)) return 0;
    if (p > i && s[p - 1] == ':' && !(p >= i + 2 && s[p - 2] == ':')) return 0;
    /* Zone-ID (%12 / %eth0) mitnehmen */
    if (p < n && s[p] == '%') { size_t q = p + 1; while (q < n && isalnum((unsigned char)s[q])) q++; if (q > p + 1) p = q; }
    size_t len = p - i;
    if (len <= 3) return 0;          /* "::", "::1" */
    return len;
}

/* Benutzername nach "\Users\", "\\Users\\" oder "/Users/" */
static size_t match_user(const char *s, size_t i, size_t n, size_t *name_at) {
    static const char *pre[] = {"\\\\Users\\\\", "\\Users\\", "/Users/"};
    for (int k = 0; k < 3; k++) {
        size_t L = strlen(pre[k]);
        if (i + L < n && !_strnicmp(s + i, pre[k], L)) {
            size_t p = i + L, q = p;
            while (q < n && s[q] != '\\' && s[q] != '/' && s[q] != '"' && s[q] != '\n' && s[q] != '\r' && s[q] != ' ' &&
                   s[q] != ',' && s[q] != '\'') q++;
            if (q > p) { *name_at = p; return q - p; }
        }
    }
    return 0;
}

static int user_exempt(const char *u) {
    static const char *ex[] = {"Public", "Default", "Default User", "All Users", "%USERNAME%", NULL};
    for (int i = 0; ex[i]; i++) if (!_stricmp(u, ex[i])) return 1;
    return 0;
}

/* Literale (Hostnamen, Seriennummern) ersetzen; case-insensitiv, an Wortgrenzen */
static size_t match_literal(amap *m, const char *s, size_t i, size_t n, const char **to) {
    if (i > 0 && isword(s[i - 1])) return 0;
    size_t best = 0;
    for (int k = 0; k < m->n; k++) {
        size_t L = strlen(m->from[k]);
        if (L > best && i + L <= n && !_strnicmp(s + i, m->from[k], L) && (i + L == n || !isword(s[i + L]))) {
            best = L; *to = m->to[k];
        }
    }
    return best;
}

static char *anonymize_text(anon *a, const char *s, size_t n, size_t *out_n) {
    sbuf b = {0};
    size_t i = 0, lit_from = 0;
    while (i < n) {
        size_t L, at;
        const char *to = NULL;
        int skip;
        if ((L = match_user(s, i, n, &at)) > 0) {
            char *u = (char *)malloc(L + 1);
            if (u) {
                memcpy(u, s + at, L); u[L] = 0;
                sb_put(&b, s + i, at - i);
                if (user_exempt(u)) sb_put(&b, u, L);
                else { const char *p = amap_get(&a->user, u, 1); sb_put(&b, p, strlen(p)); }
                free(u);
            }
            i = at + L; continue;
        }
        if ((L = match_literal(&a->host, s, i, n, &to)) > 0 || (L = match_literal(&a->serial, s, i, n, &to)) > 0) {
            sb_put(&b, to, strlen(to)); i += L; continue;
        }
        if ((L = match_mac(s, i, n)) > 0) {
            char v[18]; memcpy(v, s + i, 17); v[17] = 0;
            for (int k = 0; k < 17; k++) { v[k] = (char)toupper((unsigned char)v[k]); if (v[k] == '-') v[k] = ':'; }
            const char *p = amap_get(&a->mac, v, 1);
            sb_put(&b, p, strlen(p)); i += L; continue;
        }
        if (isdigit((unsigned char)s[i]) && (L = match_ip4(s, i, n, &skip)) > 0) {
            if (skip) sb_put(&b, s + i, L);
            else {
                char v[16]; memcpy(v, s + i, L); v[L] = 0;
                const char *p = amap_get(&a->ip4, v, 0);
                sb_put(&b, p, strlen(p));
            }
            i += L; continue;
        }
        if ((ishex(s[i]) || s[i] == ':') && (L = match_ip6(s, i, n)) > 0) {
            char *v = (char *)malloc(L + 1);
            if (v) {
                memcpy(v, s + i, L); v[L] = 0;
                const char *p = amap_get(&a->ip6, v, 1);
                sb_put(&b, p, strlen(p));
                free(v);
            }
            i += L; continue;
        }
        sb_put(&b, s + i, 1);
        i++;
    }
    (void)lit_from;
    *out_n = b.n;
    if (!b.p) { b.p = (char *)calloc(1, 1); }
    return b.p;
}

/* JSON-Werte unter Schlüsseln wie host / physical_host / serial* vormerken */
static void collect_json(anon *a, const cJSON *j) {
    const cJSON *it;
    cJSON_ArrayForEach(it, j) {
        if (cJSON_IsString(it) && it->string && it->valuestring[0]) {
            if (!_stricmp(it->string, "host") || strstr(it->string, "physical_host") || !_stricmp(it->string, "hostname"))
                amap_note(&a->host, it->valuestring, 1);
            else if (strstr(it->string, "serial")) amap_note(&a->serial, it->valuestring, 0);
        }
        if (cJSON_IsObject(it) || cJSON_IsArray(it)) collect_json(a, it);
    }
}

static void trim(char *s) {
    size_t n = strlen(s);
    while (n && (s[n - 1] == ' ' || s[n - 1] == '\t')) s[--n] = 0;
    size_t k = 0;
    while (s[k] == ' ' || s[k] == '\t') k++;
    if (k) memmove(s, s + k, n - k + 1);
}

static void collect_local(anon *a) {
    COMPUTER_NAME_FORMAT f[] = {ComputerNameDnsHostname, ComputerNameNetBIOS, ComputerNameDnsFullyQualified,
                                ComputerNamePhysicalDnsHostname, ComputerNamePhysicalNetBIOS};
    for (int i = 0; i < 5; i++) {
        wchar_t w[256]; DWORD l = 256;
        if (GetComputerNameExW(f[i], w, &l) && l) { char *u = sb_w_to_u8(w); amap_note(&a->host, u, 1); free(u); }
    }
    /* Hyper-V-Gast: Name des physischen Hosts */
    HKEY k;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Microsoft\\Virtual Machine\\Guest\\Parameters", 0, KEY_READ, &k) == 0) {
        const wchar_t *vals[] = {L"PhysicalHostName", L"PhysicalHostNameFullyQualified", L"HostName", L"VirtualMachineName"};
        for (int i = 0; i < 4; i++) {
            wchar_t w[256]; DWORD l = sizeof w, t = 0;
            if (RegQueryValueExW(k, vals[i], NULL, &t, (BYTE *)w, &l) == 0 && t == REG_SZ) {
                w[255] = 0; char *u = sb_w_to_u8(w); amap_note(&a->host, u, 1); free(u);
            }
        }
        RegCloseKey(k);
    }
    wchar_t un[256]; DWORD ul = 256;
    if (GetUserNameW(un, &ul)) { char *u = sb_w_to_u8(un); if (u && !user_exempt(u)) amap_note(&a->user, u, 1); free(u); }
    /* Seriennummern der physischen Disks */
    for (int d = 0; d < 64; d++) {
        wchar_t p[64];
        swprintf(p, 64, L"\\\\.\\PhysicalDrive%d", d);
        HANDLE h = CreateFileW(p, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
        if (h == INVALID_HANDLE_VALUE) continue;
        STORAGE_PROPERTY_QUERY q;
        memset(&q, 0, sizeof q);
        q.PropertyId = StorageDeviceProperty; q.QueryType = PropertyStandardQuery;
        BYTE buf[4096];
        DWORD got = 0;
        if (DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY, &q, sizeof q, buf, sizeof buf, &got, NULL) &&
            got >= sizeof(STORAGE_DEVICE_DESCRIPTOR)) {
            STORAGE_DEVICE_DESCRIPTOR *sd = (STORAGE_DEVICE_DESCRIPTOR *)buf;
            if (sd->SerialNumberOffset && sd->SerialNumberOffset < got) {
                char s[256];
                snprintf(s, sizeof s, "%s", (const char *)buf + sd->SerialNumberOffset);
                trim(s);
                if (strlen(s) >= 4) amap_note(&a->serial, s, 0);
            }
        }
        CloseHandle(h);
    }
}

static int is_text_kind(const char *arc) {
    const char *d = strrchr(arc, '.');
    return d && (!_stricmp(d, ".json") || !_stricmp(d, ".jsonl") || !_stricmp(d, ".log"));
}

/* ---- Bundle -------------------------------------------------------------------- */

static void emit(sb_progress *p, cJSON *o) { sb_progress_emit(p, o); cJSON_Delete(o); }

static void emit_err(sb_progress *p, const char *code, const char *msg) {
    cJSON *ev = cJSON_CreateObject();
    cJSON_AddStringToObject(ev, "ev", "error");
    cJSON_AddStringToObject(ev, "code", code);
    cJSON_AddStringToObject(ev, "msg", msg);
    emit(p, ev);
}

static void emit_prog(sb_progress *p, double done, double total) {
    cJSON *ev = cJSON_CreateObject();
    cJSON_AddStringToObject(ev, "ev", "progress");
    cJSON_AddNumberToObject(ev, "test", 0);
    cJSON_AddStringToObject(ev, "phase", "preparing");
    cJSON_AddNumberToObject(ev, "done", done);
    cJSON_AddNumberToObject(ev, "total", total);
    cJSON_AddStringToObject(ev, "unit", "bytes");
    emit(p, ev);
}

static int zip_add(mz_zip_archive *zip, cJSON *files, const char *arc, const void *data, size_t n) {
    char hex[65];
    sb_sha256_hex(data, n, hex);
    if (!mz_zip_writer_add_mem(zip, arc, data, n, MZ_DEFAULT_COMPRESSION)) return -1;
    cJSON *f = cJSON_CreateObject();
    cJSON_AddStringToObject(f, "path", arc);
    cJSON_AddNumberToObject(f, "size_bytes", (double)n);
    cJSON_AddStringToObject(f, "sha256", hex);
    cJSON_AddItemToArray(files, f);
    return 0;
}

int sb_bundle(const sb_bundle_options *o, char *err, size_t errlen) {
    if (!o->out_path) { snprintf(err, errlen, "--out fehlt"); return SB_EXIT_INVALID; }
    sb_progress pr;
    if (sb_progress_open(&pr, o->progress, err, errlen)) { sb_progress_close(&pr); return SB_EXIT_ERROR; }
    char bid[40];
    sb_new_run_id(bid);
    cJSON *ev = cJSON_CreateObject();
    cJSON_AddStringToObject(ev, "ev", "start");
    cJSON_AddStringToObject(ev, "run_id", bid);
    cJSON_AddNumberToObject(ev, "tests", 1);
    emit(&pr, ev);
    ev = cJSON_CreateObject();
    cJSON_AddStringToObject(ev, "ev", "phase");
    cJSON_AddNumberToObject(ev, "test", 0);
    cJSON_AddStringToObject(ev, "name", "preparing");
    emit(&pr, ev);

    const char *dd = sb_data_dir();
    cJSON *index = sb_runs_index();
    blist l = {0};
    cJSON *incnames = cJSON_CreateArray();
    int rc = SB_EXIT_OK;
    mz_zip_archive zip;
    memset(&zip, 0, sizeof zip);
    int zip_ok = 0;
    anon an;
    memset(&an, 0, sizeof an);
    an.host.fmt = "host"; an.user.fmt = "user"; an.ip4.fmt = "ip4"; an.ip6.fmt = "ip6"; an.mac.fmt = "mac"; an.serial.fmt = "serial";

    if (o->all) {
        const cJSON *it;
        cJSON_ArrayForEach(it, index) {
            const cJSON *id = cJSON_GetObjectItemCaseSensitive(it, "run_id");
            const cJSON *out = cJSON_GetObjectItemCaseSensitive(it, "out");
            if (cJSON_IsString(id)) add_run(&l, dd, id->valuestring, cJSON_IsString(out) ? out->valuestring : NULL);
        }
    }
    for (int i = 0; i < o->nresults; i++) {
        const char *r = o->results[i];
        if (sb_is_dir(r)) {
            char *pat = sb_aprintf("%s\\*.json", r);
            wchar_t *w = pat ? sb_u8_to_w(pat) : NULL;
            WIN32_FIND_DATAW fd;
            HANDLE f = w ? FindFirstFileW(w, &fd) : INVALID_HANDLE_VALUE;
            if (f != INVALID_HANDLE_VALUE) {
                do {
                    if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
                    char *n8 = sb_w_to_u8(fd.cFileName);
                    char *src = n8 ? sb_aprintf("%s\\%s", r, n8) : NULL;
                    char *id = src ? run_id_for(index, src) : NULL;
                    if (id) add_run(&l, dd, id, src);
                    free(id); free(src); free(n8);
                } while (FindNextFileW(f, &fd));
                FindClose(f);
            }
            free(w); free(pat);
        } else if (sb_file_exists(r)) {
            char *id = run_id_for(index, r);
            if (id) add_run(&l, dd, id, r);
            free(id);
        } else {
            snprintf(err, errlen, "--results: '%s' existiert nicht.", r);
            emit_err(&pr, "E_ARGS", err);
            rc = SB_EXIT_INVALID;
            goto done;
        }
    }
    for (int i = 0; i < o->nincludes; i++) {
        const char *d = o->includes[i];
        if (!sb_is_dir(d)) {
            snprintf(err, errlen, "--include: Ordner '%s' existiert nicht.", d);
            emit_err(&pr, "E_ARGS", err);
            rc = SB_EXIT_INVALID;
            goto done;
        }
        char *fp = full_path(d);
        size_t fl = fp ? strlen(fp) : 0;
        while (fp && fl > 3 && (fp[fl - 1] == '\\' || fp[fl - 1] == '/')) fp[--fl] = 0;
        char base[260];
        snprintf(base, sizeof base, "%s", fp ? sb_basename(fp) : d);
        if (!base[0] || strchr(base, ':')) snprintf(base, sizeof base, "ordner");   /* z. B. Laufwerkswurzel */
        /* gleicher Ordnername mehrfach: -2, -3 ... anhängen */
        char name[280];
        snprintf(name, sizeof name, "%s", base);
        for (int k = 2;; k++) {
            int dup = 0;
            const cJSON *it;
            cJSON_ArrayForEach(it, incnames) if (!_stricmp(it->valuestring, name)) dup = 1;
            if (!dup) break;
            snprintf(name, sizeof name, "%s-%d", base, k);
        }
        cJSON_AddItemToArray(incnames, cJSON_CreateString(name));
        char *arc = sb_aprintf("extra/%s", name);
        add_tree(&l, fp ? fp : d, arc, 0);
        free(arc); free(fp);
    }

    if (o->anonymize) {
        collect_local(&an);
        for (int i = 0; i < l.n; i++) {
            const char *d = strrchr(l.v[i].arc, '.');
            if (!d || _stricmp(d, ".json") || l.v[i].size > (64u << 20)) continue;
            char *data = NULL; size_t n = 0;
            if (sb_read_file(l.v[i].src, &data, &n) == 0 && data) {
                cJSON *j = cJSON_ParseWithLength(data, n);
                if (j) collect_json(&an, j);
                cJSON_Delete(j);
            }
            free(data);
        }
    }

    if (!mz_zip_writer_init_heap(&zip, 0, 1 << 20)) { snprintf(err, errlen, "ZIP-Initialisierung fehlgeschlagen"); rc = SB_EXIT_ERROR; goto done; }
    zip_ok = 1;

    cJSON *man = cJSON_CreateObject();
    char now[40];
    sb_utc_iso_ms(now);
    cJSON_AddStringToObject(man, "schema", "sb-bundle/1");
    cJSON_AddStringToObject(man, "kern_version", sb_version());
    cJSON_AddStringToObject(man, "erstellt_utc", now);
    wchar_t host[256]; DWORD hl = 256;
    char *h8 = GetComputerNameExW(ComputerNameDnsHostname, host, &hl) ? sb_w_to_u8(host) : NULL;
    cJSON_AddStringToObject(man, "host", h8 ? h8 : "unbekannt");
    free(h8);
    cJSON_AddStringToObject(man, "data_dir", dd);
    cJSON_AddBoolToObject(man, "anonymized", o->anonymize != 0);
    cJSON_AddItemToObject(man, "includes", incnames);
    incnames = NULL;
    cJSON *files = cJSON_AddArrayToObject(man, "files");

    uint64_t total = 0, done_b = 0;
    for (int i = 0; i < l.n; i++) total += l.v[i].size;
    ULONGLONG last = GetTickCount64();

    /* info.json */
    {
        cJSON *info = sb_info_document();
        if (o->anonymize) collect_json(&an, info);
        char *it = cJSON_Print(info);
        cJSON_Delete(info);
        if (it) {
            size_t n = strlen(it), an_n = n;
            char *txt = o->anonymize ? anonymize_text(&an, it, n, &an_n) : NULL;
            if (zip_add(&zip, files, "info.json", txt ? txt : it, txt ? an_n : n)) rc = SB_EXIT_ERROR;
            free(txt);
            cJSON_free(it);
        }
    }
    for (int i = 0; i < l.n && rc == SB_EXIT_OK; i++) {
        char *data = NULL;
        size_t n = 0;
        if (sb_read_file(l.v[i].src, &data, &n)) {
            char m[600];
            snprintf(m, sizeof m, "Datei '%s' nicht lesbar, übersprungen: %s", l.v[i].src, sb_errtext(GetLastError()));
            emit_err(&pr, "E_IO", m);
            fprintf(stderr, "sbench: %s\n", m);
            continue;
        }
        const void *payload = data;
        size_t pn = n;
        char *txt = NULL;
        if (o->anonymize && is_text_kind(l.v[i].arc)) { txt = anonymize_text(&an, data ? data : "", n, &pn); payload = txt; }
        if (zip_add(&zip, files, l.v[i].arc, payload ? payload : "", pn)) {
            snprintf(err, errlen, "ZIP: '%s' konnte nicht hinzugefügt werden", l.v[i].arc);
            rc = SB_EXIT_ERROR;
        }
        free(txt);
        free(data);
        done_b += l.v[i].size;
        ULONGLONG t = GetTickCount64();
        if (t - last >= 1000 || i + 1 == l.n) { last = t; emit_prog(&pr, (double)done_b, (double)total); }
    }
    if (rc == SB_EXIT_OK) {
        char *mt = cJSON_Print(man);
        size_t mn = mt ? strlen(mt) : 0;
        char *txt = (mt && o->anonymize) ? anonymize_text(&an, mt, mn, &mn) : NULL;
        if (!mt || !mz_zip_writer_add_mem(&zip, "manifest.json", txt ? txt : mt, mn, MZ_DEFAULT_COMPRESSION)) {
            snprintf(err, errlen, "ZIP: manifest.json konnte nicht hinzugefügt werden");
            rc = SB_EXIT_ERROR;
        }
        free(txt);
        cJSON_free(mt);
    }
    cJSON_Delete(man);
    void *zbuf = NULL;
    size_t zlen = 0;
    if (rc == SB_EXIT_OK && !mz_zip_writer_finalize_heap_archive(&zip, &zbuf, &zlen)) {
        snprintf(err, errlen, "ZIP konnte nicht abgeschlossen werden");
        rc = SB_EXIT_ERROR;
    }
    if (rc == SB_EXIT_OK && sb_write_file(o->out_path, zbuf, zlen)) {
        snprintf(err, errlen, "'%s' nicht schreibbar: %s", o->out_path, sb_errtext(GetLastError()));
        rc = SB_EXIT_ERROR;
    }
    if (zbuf) mz_free(zbuf);
done:
    if (zip_ok) mz_zip_writer_end(&zip);
    cJSON_Delete(incnames);
    cJSON_Delete(index);
    amap_free(&an.host); amap_free(&an.user); amap_free(&an.ip4); amap_free(&an.ip6); amap_free(&an.mac); amap_free(&an.serial);
    for (int k = 0; k < l.n; k++) { free(l.v[k].src); free(l.v[k].arc); }
    free(l.v);
    if (rc == SB_EXIT_OK) {
        ev = cJSON_CreateObject();
        cJSON_AddStringToObject(ev, "ev", "done");
        cJSON_AddStringToObject(ev, "result", o->out_path);
        cJSON_AddBoolToObject(ev, "aborted", 0);
        emit(&pr, ev);
    } else if (rc == SB_EXIT_ERROR) {
        emit_err(&pr, "E_IO", err);
    }
    sb_progress_close(&pr);
    return rc;
}
