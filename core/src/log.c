#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sb/log.h"

int sb_log_open(sb_log *l, const char *path) {
    memset(l, 0, sizeof *l);
    l->h = INVALID_HANDLE_VALUE;
    InitializeCriticalSection(&l->cs);
    if (!path) return -1;
    snprintf(l->path, sizeof l->path, "%s", path);
    wchar_t *w = sb_u8_to_w(path);
    if (!w) return -1;
    l->h = CreateFileW(w, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL, NULL);
    free(w);
    return l->h == INVALID_HANDLE_VALUE ? -1 : 0;
}

void sb_log_close(sb_log *l) {
    if (l->h != INVALID_HANDLE_VALUE) CloseHandle(l->h);
    l->h = INVALID_HANDLE_VALUE;
    DeleteCriticalSection(&l->cs);
}

static void vlog(sb_log *l, const char *level, const char *suffix, const char *fmt, va_list ap) {
    if (!l || l->h == INVALID_HANDLE_VALUE) return;
    char ts[40], msg[2048], line[2300];
    sb_utc_iso_ms(ts);
    vsnprintf(msg, sizeof msg, fmt, ap);
    int n = snprintf(line, sizeof line, "%s %-5s %s%s%s\r\n", ts, level, msg, suffix ? ": " : "", suffix ? suffix : "");
    if (n < 0) return;
    if ((size_t)n >= sizeof line) n = (int)sizeof line - 1;
    EnterCriticalSection(&l->cs);
    DWORD put;
    WriteFile(l->h, line, (DWORD)n, &put, NULL);
    LeaveCriticalSection(&l->cs);
}

void sb_log_write(sb_log *l, const char *level, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); vlog(l, level, NULL, fmt, ap); va_end(ap);
}

void sb_log_winerr(sb_log *l, DWORD err, const char *fmt, ...) {
    char et[600];
    snprintf(et, sizeof et, "%s", sb_errtext(err));
    va_list ap; va_start(ap, fmt); vlog(l, "ERROR", et, fmt, ap); va_end(ap);
}
