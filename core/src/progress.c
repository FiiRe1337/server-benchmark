#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sb/progress.h"

int sb_progress_open(sb_progress *p, const char *spec, char *err, size_t errlen) {
    memset(p, 0, sizeof *p);
    p->h = INVALID_HANDLE_VALUE;
    p->mirror = INVALID_HANDLE_VALUE;
    InitializeCriticalSection(&p->cs);
    if (!spec || !*spec || !strcmp(spec, "none")) return 0;
    if (!strcmp(spec, "stdout")) {
        p->h = GetStdHandle(STD_OUTPUT_HANDLE);
        DWORD mode;
        p->is_console = GetConsoleMode(p->h, &mode) ? 1 : 0;
        return 0;
    }
    if (!strncmp(spec, "pipe:", 5)) {
        wchar_t *w = sb_u8_to_w(spec + 5);
        if (!w) { snprintf(err, errlen, "ungueltiger Pipe-Name"); return -1; }
        /* Die GUI legt die Pipe an; bis zu 10 s warten (WaitNamedPipe), sonst E_PIPE_CONNECT */
        p->is_pipe = 1;
        DWORD t0 = GetTickCount();
        for (;;) {
            p->h = CreateFileW(w, GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
            if (p->h != INVALID_HANDLE_VALUE) break;
            DWORD e = GetLastError();
            if (GetTickCount() - t0 > 10000 || (e != ERROR_PIPE_BUSY && e != ERROR_FILE_NOT_FOUND)) {
                snprintf(err, errlen, "Pipe %s nicht verbindbar: %s", spec + 5, sb_errtext(e));
                free(w);
                return -1;
            }
            DWORD left = 10000 - (GetTickCount() - t0);
            if (e == ERROR_PIPE_BUSY) WaitNamedPipeW(w, left < 1000 ? left : 1000); else Sleep(100);
        }
        free(w);
        DWORD mode = PIPE_READMODE_BYTE;
        SetNamedPipeHandleState(p->h, &mode, NULL, NULL);
        p->owns = 1;
        return 0;
    }
    if (!strncmp(spec, "file:", 5)) {
        wchar_t *w = sb_u8_to_w(spec + 5);
        if (!w) { snprintf(err, errlen, "ungueltiger Dateipfad"); return -1; }
        p->h = CreateFileW(w, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        free(w);
        if (p->h == INVALID_HANDLE_VALUE) {
            snprintf(err, errlen, "Fortschrittsdatei %s: %s", spec + 5, sb_errtext(GetLastError()));
            return -1;
        }
        p->owns = 1;
        return 0;
    }
    snprintf(err, errlen, "unbekanntes --progress-Ziel '%s' (stdout|pipe:<name>|file:<pfad>)", spec);
    return -1;
}

int sb_progress_mirror(sb_progress *p, const char *path, char *err, size_t errlen) {
    wchar_t *w = sb_u8_to_w(path);
    if (!w) { snprintf(err, errlen, "ungueltiger Pfad"); return -1; }
    p->mirror = CreateFileW(w, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    free(w);
    if (p->mirror == INVALID_HANDLE_VALUE) {
        snprintf(err, errlen, "Fortschrittsdatei %s: %s", path, sb_errtext(GetLastError()));
        return -1;
    }
    return 0;
}

void sb_progress_close(sb_progress *p) {
    if (p->mirror != INVALID_HANDLE_VALUE) { FlushFileBuffers(p->mirror); CloseHandle(p->mirror); }
    p->mirror = INVALID_HANDLE_VALUE;
    if (p->owns && p->h != INVALID_HANDLE_VALUE) {
        FlushFileBuffers(p->h);
        CloseHandle(p->h);
    }
    p->h = INVALID_HANDLE_VALUE;
    DeleteCriticalSection(&p->cs);
}

void sb_progress_emit(sb_progress *p, const cJSON *obj) {
    int primary = !(p->h == INVALID_HANDLE_VALUE || p->h == NULL || p->broken);
    if (!primary && p->mirror == INVALID_HANDLE_VALUE) return;
    char *s = cJSON_PrintUnformatted(obj);
    if (!s) return;
    size_t n = strlen(s);
    char *line = (char *)malloc(n + 2);
    if (!line) { cJSON_free(s); return; }
    memcpy(line, s, n);
    line[n] = '\n';
    line[n + 1] = 0;
    cJSON_free(s);
    EnterCriticalSection(&p->cs);
    DWORD put = 0, berr = 0;
    if (p->mirror != INVALID_HANDLE_VALUE) WriteFile(p->mirror, line, (DWORD)(n + 1), &put, NULL);
    if (primary && !WriteFile(p->h, line, (DWORD)(n + 1), &put, NULL)) {
        DWORD e = GetLastError();
        if (e == ERROR_NO_DATA || e == ERROR_BROKEN_PIPE || e == ERROR_PIPE_NOT_CONNECTED) { p->broken = 1; berr = e; }
    }
    LeaveCriticalSection(&p->cs);
    if (berr && p->on_broken) p->on_broken(p->ctx, berr);
    free(line);
}
