#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sb/runs.h"

static char *lock_path(const char *run_id) { return sb_aprintf("%s\\runs\\%s.lock.json", sb_data_dir(), run_id); }

int sb_runs_write_lock(const char *run_id, const char *started_utc, const char *job, const char *out) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "run_id", run_id);
    cJSON_AddNumberToObject(o, "pid", (double)GetCurrentProcessId());
    cJSON_AddStringToObject(o, "started_utc", started_utc);
    cJSON_AddStringToObject(o, "job", job);
    cJSON_AddStringToObject(o, "out", out);
    char *txt = cJSON_Print(o);
    cJSON_Delete(o);
    char *p = lock_path(run_id), *tmp = sb_aprintf("%s.tmp", p);
    int rc = -1;
    if (txt && p && tmp && sb_write_file(tmp, txt, strlen(txt)) == 0) {
        wchar_t *wt = sb_u8_to_w(tmp), *wp = sb_u8_to_w(p);
        if (wt && wp && MoveFileExW(wt, wp, MOVEFILE_REPLACE_EXISTING)) rc = 0;
        free(wt); free(wp);
    }
    cJSON_free(txt); free(p); free(tmp);
    return rc;
}

void sb_runs_remove_lock(const char *run_id) {
    char *p = lock_path(run_id);
    wchar_t *w = p ? sb_u8_to_w(p) : NULL;
    if (w) DeleteFileW(w);
    free(w); free(p);
}

/* "YYYY-MM-DDTHH:MM:SS[.fff]Z" -> FILETIME (100 ns) */
static int parse_utc(const char *s, uint64_t *ft) {
    SYSTEMTIME st;
    memset(&st, 0, sizeof st);
    int Y, M, D, h, m, sec;
    if (!s || sscanf(s, "%4d-%2d-%2dT%2d:%2d:%2d", &Y, &M, &D, &h, &m, &sec) != 6) return -1;
    st.wYear = (WORD)Y; st.wMonth = (WORD)M; st.wDay = (WORD)D;
    st.wHour = (WORD)h; st.wMinute = (WORD)m; st.wSecond = (WORD)sec;
    FILETIME f;
    if (!SystemTimeToFileTime(&st, &f)) return -1;
    *ft = ((uint64_t)f.dwHighDateTime << 32) | f.dwLowDateTime;
    const char *dot = strchr(s, '.');
    if (dot && dot - s == 19) {
        uint64_t frac = 0, scale = 10000000;
        for (const char *c = dot + 1; *c >= '0' && *c <= '9'; c++) { scale /= 10; frac += (uint64_t)(*c - '0') * scale; if (!scale) break; }
        *ft += frac;
    }
    return 0;
}

/* Prozess pid läuft und wurde kurz vor started_utc gestartet (Schutz gegen PID-Wiederverwendung) */
static int pid_matches(DWORD pid, const char *started_utc) {
    HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!h) return GetLastError() == ERROR_ACCESS_DENIED;   /* existiert, aber fremder Kontext: als laufend werten */
    DWORD code = 0;
    int alive = GetExitCodeProcess(h, &code) && code == STILL_ACTIVE;
    FILETIME c, x, k, u;
    uint64_t st = 0;
    if (alive && GetProcessTimes(h, &c, &x, &k, &u) && parse_utc(started_utc, &st) == 0) {
        uint64_t cr = ((uint64_t)c.dwHighDateTime << 32) | c.dwLowDateTime;
        /* Lauf startet nach dem Prozess, spätestens 10 min später. Toleranz 2 s in beide
         * Richtungen: started_utc stammt aus der groben Systemuhr (~15,6 ms), die
         * Prozess-Startzeit ist genauer und kann daher minimal später liegen. */
        const uint64_t tol = 20000000ULL, max_gap = 6000000000ULL;
        if (cr > st + tol || st > cr + max_gap) alive = 0;
    }
    CloseHandle(h);
    return alive;
}

static cJSON *read_lock(const char *path) {
    size_t n = 0;
    char *t = NULL;
    if (sb_read_file(path, &t, &n) || !t) return NULL;
    cJSON *o = cJSON_Parse(t);
    free(t);
    return o;
}

static int lock_is_alive(cJSON *o) {
    if (!o) return 0;
    const cJSON *pid = cJSON_GetObjectItemCaseSensitive(o, "pid");
    const cJSON *st = cJSON_GetObjectItemCaseSensitive(o, "started_utc");
    if (!cJSON_IsNumber(pid) || !cJSON_IsString(st) || pid->valuedouble < 1) return 0;
    return pid_matches((DWORD)pid->valuedouble, st->valuestring);
}

int sb_runs_lock_alive(const char *run_id) {
    char *p = lock_path(run_id);
    cJSON *o = p ? read_lock(p) : NULL;
    int a = lock_is_alive(o);
    cJSON_Delete(o);
    free(p);
    return a;
}

int sb_runs_index_append(const char *run_id, const char *started_utc, const char *ended_utc, const char *out,
                         int exit_code) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "run_id", run_id);
    cJSON_AddStringToObject(o, "started_utc", started_utc);
    cJSON_AddStringToObject(o, "ended_utc", ended_utc);
    cJSON_AddStringToObject(o, "out", out);
    cJSON_AddNumberToObject(o, "exit_code", exit_code);
    char *s = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    if (!s) return -1;
    char *line = sb_aprintf("%s\n", s);
    cJSON_free(s);
    char *p = sb_aprintf("%s\\runs\\index.jsonl", sb_data_dir());
    wchar_t *w = p ? sb_u8_to_w(p) : NULL;
    int rc = -1;
    if (w && line) {
        /* FILE_APPEND_DATA: atomares Anhängen auch bei parallelen Läufen; kurz wiederholen, falls gesperrt */
        for (int i = 0; i < 50 && rc; i++) {
            HANDLE h = CreateFileW(w, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                                   OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
            if (h == INVALID_HANDLE_VALUE) { Sleep(20); continue; }
            DWORD put = 0;
            if (WriteFile(h, line, (DWORD)strlen(line), &put, NULL)) rc = 0;
            CloseHandle(h);
        }
    }
    free(w); free(p); free(line);
    return rc;
}

cJSON *sb_runs_running(void) {
    cJSON *d = cJSON_CreateObject();
    cJSON_AddStringToObject(d, "schema", "sb-runs/1");
    cJSON *arr = cJSON_AddArrayToObject(d, "runs");
    char *pat = sb_aprintf("%s\\runs\\*.lock.json", sb_data_dir());
    wchar_t *wp = pat ? sb_u8_to_w(pat) : NULL;
    WIN32_FIND_DATAW fd;
    HANDLE f = wp ? FindFirstFileW(wp, &fd) : INVALID_HANDLE_VALUE;
    if (f != INVALID_HANDLE_VALUE) {
        do {
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
            char *nm = sb_w_to_u8(fd.cFileName);
            char *full = nm ? sb_aprintf("%s\\runs\\%s", sb_data_dir(), nm) : NULL;
            cJSON *o = full ? read_lock(full) : NULL;
            const cJSON *id = o ? cJSON_GetObjectItemCaseSensitive(o, "run_id") : NULL;
            const cJSON *out = o ? cJSON_GetObjectItemCaseSensitive(o, "out") : NULL;
            const cJSON *st = o ? cJSON_GetObjectItemCaseSensitive(o, "started_utc") : NULL;
            const cJSON *pid = o ? cJSON_GetObjectItemCaseSensitive(o, "pid") : NULL;
            if (o && cJSON_IsString(id) && cJSON_IsString(out) && cJSON_IsString(st) && cJSON_IsNumber(pid) &&
                lock_is_alive(o)) {
                cJSON *x = cJSON_CreateObject();
                cJSON_AddStringToObject(x, "run_id", id->valuestring);
                cJSON_AddNumberToObject(x, "pid", pid->valuedouble);
                cJSON_AddStringToObject(x, "started_utc", st->valuestring);
                cJSON_AddStringToObject(x, "out", out->valuestring);
                char *pf = sb_aprintf("%s\\runs\\%s.jsonl", sb_data_dir(), id->valuestring);
                cJSON_AddStringToObject(x, "progress_file", pf ? pf : "");
                free(pf);
                cJSON_AddItemToArray(arr, x);
            } else if (full) {
                /* verwaist (Prozess beendet / PID wiederverwendet / unlesbar): entfernen */
                wchar_t *wf = sb_u8_to_w(full);
                if (wf) DeleteFileW(wf);
                free(wf);
            }
            cJSON_Delete(o);
            free(full); free(nm);
        } while (FindNextFileW(f, &fd));
        FindClose(f);
    }
    free(wp); free(pat);
    return d;
}

cJSON *sb_runs_index(void) {
    cJSON *arr = cJSON_CreateArray();
    char *p = sb_aprintf("%s\\runs\\index.jsonl", sb_data_dir());
    size_t n = 0;
    char *t = NULL;
    if (!p || sb_read_file(p, &t, &n)) t = NULL;
    free(p);
    if (!t) return arr;
    char *save = t;
    for (char *line = t; line && *line;) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = 0;
        cJSON *o = cJSON_Parse(line);
        const cJSON *id = o ? cJSON_GetObjectItemCaseSensitive(o, "run_id") : NULL;
        if (cJSON_IsString(id)) {
            /* gleiche run_id: späterer Eintrag ersetzt früheren */
            int i = 0;
            cJSON *it;
            cJSON_ArrayForEach(it, arr) {
                const cJSON *j = cJSON_GetObjectItemCaseSensitive(it, "run_id");
                if (cJSON_IsString(j) && !strcmp(j->valuestring, id->valuestring)) break;
                i++;
            }
            if (it) cJSON_ReplaceItemInArray(arr, i, o);
            else cJSON_AddItemToArray(arr, o);
        } else {
            cJSON_Delete(o);
        }
        line = nl ? nl + 1 : NULL;
    }
    free(save);
    return arr;
}
