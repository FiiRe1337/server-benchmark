#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "sb/util.h"
#include <sddl.h>

#ifdef _MSC_VER
#define SB_TLS __declspec(thread)
#else
#define SB_TLS __thread
#endif

wchar_t *sb_u8_to_w(const char *s) {
    if (!s) return NULL;
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);
    if (n <= 0) return NULL;
    wchar_t *w = (wchar_t *)malloc((size_t)n * sizeof(wchar_t));
    if (w) MultiByteToWideChar(CP_UTF8, 0, s, -1, w, n);
    return w;
}

char *sb_w_to_u8(const wchar_t *w) {
    if (!w) return NULL;
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
    if (n <= 0) return NULL;
    char *s = (char *)malloc((size_t)n);
    if (s) WideCharToMultiByte(CP_UTF8, 0, w, -1, s, n, NULL, NULL);
    return s;
}

char *sb_strdup(const char *s) {
    if (!s) return NULL;
    size_t n = strlen(s) + 1;
    char *d = (char *)malloc(n);
    if (d) memcpy(d, s, n);
    return d;
}

char *sb_aprintf(const char *fmt, ...) {
    va_list ap, ap2;
    va_start(ap, fmt);
    va_copy(ap2, ap);
    int n = vsnprintf(NULL, 0, fmt, ap);
    va_end(ap);
    if (n < 0) { va_end(ap2); return NULL; }
    char *s = (char *)malloc((size_t)n + 1);
    if (s) vsnprintf(s, (size_t)n + 1, fmt, ap2);
    va_end(ap2);
    return s;
}

static LARGE_INTEGER g_qpf;
uint64_t sb_qpf(void) {
    if (!g_qpf.QuadPart) QueryPerformanceFrequency(&g_qpf);
    return (uint64_t)g_qpf.QuadPart;
}
uint64_t sb_qpc(void) { LARGE_INTEGER t; QueryPerformanceCounter(&t); return (uint64_t)t.QuadPart; }
double sb_qpc_to_ns(uint64_t ticks) { return (double)ticks * 1e9 / (double)sb_qpf(); }

void sb_utc_iso(char out[32]) {
    SYSTEMTIME st; GetSystemTime(&st);
    snprintf(out, 32, "%04u-%02u-%02uT%02u:%02u:%02uZ", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);
}
void sb_utc_iso_ms(char out[40]) {
    SYSTEMTIME st; GetSystemTime(&st);
    snprintf(out, 40, "%04u-%02u-%02uT%02u:%02u:%02u.%03uZ", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute,
             st.wSecond, st.wMilliseconds);
}
void sb_new_run_id(char out[32]) {
    SYSTEMTIME st; GetSystemTime(&st);
    unsigned r = (unsigned)(sb_qpc() ^ (GetCurrentProcessId() * 2654435761u)) & 0xffffu;
    snprintf(out, 32, "%04u%02u%02u-%02u%02u%02u-%04x", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, r);
}

const char *sb_errtext(DWORD err) {
    static SB_TLS char buf[512];
    wchar_t w[400];
    DWORD n = FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, NULL, err,
                             MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT), w, 400, NULL);
    while (n && (w[n - 1] == L'\r' || w[n - 1] == L'\n' || w[n - 1] == L' ' || w[n - 1] == L'.')) w[--n] = 0;
    if (!n) { snprintf(buf, sizeof buf, "Win32-Fehler %lu", (unsigned long)err); return buf; }
    int k = WideCharToMultiByte(CP_UTF8, 0, w, -1, buf, (int)sizeof buf - 24, NULL, NULL);
    if (k <= 0) buf[0] = 0;
    size_t l = strlen(buf);
    snprintf(buf + l, sizeof buf - l, " (%lu)", (unsigned long)err);
    return buf;
}

int sb_read_file(const char *path, char **data, size_t *len) {
    wchar_t *w = sb_u8_to_w(path);
    if (!w) return -1;
    HANDLE h = CreateFileW(w, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    free(w);
    if (h == INVALID_HANDLE_VALUE) return -1;
    LARGE_INTEGER sz;
    if (!GetFileSizeEx(h, &sz) || sz.QuadPart > (1LL << 31)) { CloseHandle(h); return -1; }
    char *buf = (char *)malloc((size_t)sz.QuadPart + 1);
    DWORD got = 0;
    if (!buf || !ReadFile(h, buf, (DWORD)sz.QuadPart, &got, NULL)) { free(buf); CloseHandle(h); return -1; }
    CloseHandle(h);
    buf[got] = 0;
    *data = buf;
    if (len) *len = got;
    return 0;
}

int sb_write_file(const char *path, const void *data, size_t len) {
    wchar_t *w = sb_u8_to_w(path);
    if (!w) return -1;
    HANDLE h = CreateFileW(w, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    free(w);
    if (h == INVALID_HANDLE_VALUE) return -1;
    DWORD put = 0;
    BOOL ok = WriteFile(h, data, (DWORD)len, &put, NULL);
    CloseHandle(h);
    return ok && put == len ? 0 : -1;
}

int sb_file_exists(const char *path) {
    wchar_t *w = sb_u8_to_w(path);
    DWORD a = w ? GetFileAttributesW(w) : INVALID_FILE_ATTRIBUTES;
    free(w);
    return a != INVALID_FILE_ATTRIBUTES;
}

int sb_is_dir(const char *path) {
    wchar_t *w = sb_u8_to_w(path);
    DWORD a = w ? GetFileAttributesW(w) : INVALID_FILE_ATTRIBUTES;
    free(w);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

int sb_mkdirs(const char *path) {
    char *p = sb_strdup(path);
    if (!p) return -1;
    size_t n = strlen(p);
    for (size_t i = 1; i <= n; i++) {
        if (p[i] == '\\' || p[i] == '/' || p[i] == 0) {
            char c = p[i];
            p[i] = 0;
            if (!(i == 2 && p[1] == ':') && strlen(p) > 0) {
                wchar_t *w = sb_u8_to_w(p);
                if (w) { CreateDirectoryW(w, NULL); free(w); }
            }
            p[i] = c;
        }
    }
    int ok = sb_is_dir(path);
    free(p);
    return ok ? 0 : -1;
}

char *sb_path_join(const char *a, const char *b) {
    size_t la = strlen(a);
    int sep = la && (a[la - 1] == '\\' || a[la - 1] == '/');
    return sb_aprintf("%s%s%s", a, sep ? "" : "\\", b);
}

const char *sb_basename(const char *p) {
    const char *b = p;
    for (const char *q = p; *q; q++) if (*q == '\\' || *q == '/') b = q + 1;
    return b;
}

static int dir_writable(const wchar_t *dir) {
    wchar_t probe[MAX_PATH * 2];
    _snwprintf(probe, MAX_PATH * 2, L"%ls\\.sbench-write-test-%lu", dir, (unsigned long)GetCurrentProcessId());
    probe[MAX_PATH * 2 - 1] = 0;
    HANDLE h = CreateFileW(probe, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    CloseHandle(h);
    return 1;
}

static char g_data_dir[MAX_PATH * 3];

const char *sb_data_dir(void) {
    if (g_data_dir[0]) return g_data_dir;
    wchar_t buf[MAX_PATH * 2];
    /* 1. SBENCH_DATA_DIR, 2. data\ neben sbench.exe (falls beschreibbar), 3. %ProgramData%\sbench */
    DWORD n = GetEnvironmentVariableW(L"SBENCH_DATA_DIR", buf, MAX_PATH * 2);
    if (n == 0 || n >= MAX_PATH * 2) {
        int ok = 0;
        n = GetModuleFileNameW(NULL, buf, MAX_PATH * 2);
        if (n && n < MAX_PATH * 2) {
            wchar_t *s = wcsrchr(buf, L'\\');
            if (s) {
                *s = 0;
                wcscat_s(buf, MAX_PATH * 2, L"\\data");
                CreateDirectoryW(buf, NULL);
                ok = dir_writable(buf);
            }
        }
        if (!ok) {
            n = GetEnvironmentVariableW(L"ProgramData", buf, MAX_PATH * 2);
            if (n == 0 || n >= MAX_PATH * 2) wcscpy_s(buf, MAX_PATH * 2, L"C:\\ProgramData");
            wcscat_s(buf, MAX_PATH * 2, L"\\sbench");
            /* gemeinsames Verzeichnis für erhöhte und nicht erhöhte Prozesse: authentifizierte
             * Benutzer dürfen ändern (vererbt), sonst könnte die GUI Dateien eines per runas
             * gestarteten sbench nicht lesen/aufräumen. */
            if (GetFileAttributesW(buf) == INVALID_FILE_ATTRIBUTES) {
                SECURITY_ATTRIBUTES sa;
                PSECURITY_DESCRIPTOR sd = NULL;
                memset(&sa, 0, sizeof sa);
                sa.nLength = sizeof sa;
                if (ConvertStringSecurityDescriptorToSecurityDescriptorW(
                        L"D:(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1301bf;;;AU)", SDDL_REVISION_1, &sd, NULL))
                    sa.lpSecurityDescriptor = sd;
                CreateDirectoryW(buf, sd ? &sa : NULL);
                if (sd) LocalFree(sd);
            }
        }
    }
    n = (DWORD)wcslen(buf);
    while (n > 3 && (buf[n - 1] == L'\\' || buf[n - 1] == L'/')) buf[--n] = 0;
    char *u = sb_w_to_u8(buf);
    if (u) { snprintf(g_data_dir, sizeof g_data_dir, "%s", u); free(u); }
    sb_mkdirs(g_data_dir);
    const char *subs[] = {"results", "jobs", "logs", "runs"};
    for (int i = 0; i < 4; i++) { char *p = sb_path_join(g_data_dir, subs[i]); if (p) { sb_mkdirs(p); free(p); } }
    return g_data_dir;
}

int sb_parse_size(const char *s, uint64_t *out) {
    if (!s) return -1;
    while (isspace((unsigned char)*s)) s++;
    char *end = NULL;
    double v = strtod(s, &end);
    if (end == s || v < 0) return -1;
    while (isspace((unsigned char)*end)) end++;
    char u[8] = {0};
    size_t k = 0;
    while (*end && k < 7) { if (!isspace((unsigned char)*end)) u[k++] = (char)toupper((unsigned char)*end); end++; }
    if (*end) return -1;
    double m = 1;
    if (!strcmp(u, "") || !strcmp(u, "B")) m = 1;
    else if (!strcmp(u, "K") || !strcmp(u, "KB") || !strcmp(u, "KIB")) m = 1024.0;
    else if (!strcmp(u, "M") || !strcmp(u, "MB") || !strcmp(u, "MIB")) m = 1048576.0;
    else if (!strcmp(u, "G") || !strcmp(u, "GB") || !strcmp(u, "GIB")) m = 1073741824.0;
    else if (!strcmp(u, "T") || !strcmp(u, "TB") || !strcmp(u, "TIB")) m = 1099511627776.0;
    else return -1;
    *out = (uint64_t)(v * m + 0.5);
    return 0;
}
