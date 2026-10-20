#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sb/target.h"
#include <winioctl.h>
#include "sb/rng.h"

int sb_enable_privilege(const wchar_t *name) {
    HANDLE tok;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &tok)) return 0;
    TOKEN_PRIVILEGES tp;
    tp.PrivilegeCount = 1;
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
    int ok = 0;
    if (LookupPrivilegeValueW(NULL, name, &tp.Privileges[0].Luid)) {
        if (AdjustTokenPrivileges(tok, FALSE, &tp, sizeof tp, NULL, NULL) && GetLastError() == ERROR_SUCCESS) ok = 1;
    }
    CloseHandle(tok);
    return ok;
}

int sb_is_admin(void) {
    BOOL member = FALSE;
    SID_IDENTIFIER_AUTHORITY nt = SECURITY_NT_AUTHORITY;
    PSID admins = NULL;
    if (AllocateAndInitializeSid(&nt, 2, SECURITY_BUILTIN_DOMAIN_RID, DOMAIN_ALIAS_RID_ADMINS, 0, 0, 0, 0, 0, 0, &admins)) {
        if (!CheckTokenMembership(NULL, admins, &member)) member = FALSE;
        FreeSid(admins);
    }
    return member ? 1 : 0;
}

static void query_alignment(HANDLE h, uint32_t *logical, uint32_t *physical) {
    STORAGE_PROPERTY_QUERY q;
    memset(&q, 0, sizeof q);
    q.PropertyId = StorageAccessAlignmentProperty;
    q.QueryType = PropertyStandardQuery;
    STORAGE_ACCESS_ALIGNMENT_DESCRIPTOR d;
    memset(&d, 0, sizeof d);
    DWORD br;
    if (DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY, &q, sizeof q, &d, sizeof d, &br, NULL) && br >= 16) {
        if (d.BytesPerLogicalSector) *logical = d.BytesPerLogicalSector;
        if (d.BytesPerPhysicalSector) *physical = d.BytesPerPhysicalSector;
    }
}

static int volume_root_of(const wchar_t *path, wchar_t *root, DWORD n) {
    return GetVolumePathNameW(path, root, n) ? 0 : -1;
}

void sb_target_probe_path(const sb_target *t, sb_target_probe *p) {
    memset(p, 0, sizeof *p);
    wchar_t *w = sb_u8_to_w(t->path);
    if (!w) { snprintf(p->msg, sizeof p->msg, "Pfad ist kein gültiges UTF-8."); return; }
    if (t->type == SB_TGT_FILE) {
        wchar_t full[MAX_PATH * 2], *fp = NULL;
        DWORD n = GetFullPathNameW(w, MAX_PATH * 2, full, &fp);
        if (!n || n >= MAX_PATH * 2) { snprintf(p->msg, sizeof p->msg, "Pfad '%s' ist ungültig.", t->path); free(w); return; }
        WIN32_FILE_ATTRIBUTE_DATA fa;
        if (GetFileAttributesExW(full, GetFileExInfoStandard, &fa)) {
            if (fa.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                snprintf(p->msg, sizeof p->msg, "'%s' ist ein Verzeichnis, keine Datei.", t->path);
                free(w); return;
            }
            p->size_bytes = ((uint64_t)fa.nFileSizeHigh << 32) | fa.nFileSizeLow;
            p->exists = 1;
        } else {
            /* Elternverzeichnis prüfen */
            wchar_t dir[MAX_PATH * 2];
            wcscpy_s(dir, MAX_PATH * 2, full);
            wchar_t *s = wcsrchr(dir, L'\\');
            if (s) *s = 0;
            if (s && s > dir && s[-1] == L':') { s[0] = L'\\'; s[1] = 0; }
            DWORD a = GetFileAttributesW(dir);
            if (a == INVALID_FILE_ATTRIBUTES || !(a & FILE_ATTRIBUTE_DIRECTORY)) {
                char *d8 = sb_w_to_u8(dir);
                snprintf(p->msg, sizeof p->msg, "Verzeichnis '%s' existiert nicht.", d8 ? d8 : "?");
                free(d8); free(w); return;
            }
            p->exists = 1;
        }
        wchar_t root[MAX_PATH + 1];
        if (!volume_root_of(full, root, MAX_PATH)) {
            DWORD spc, bps, fc, tc;
            if (GetDiskFreeSpaceW(root, &spc, &bps, &fc, &tc)) p->sector_logical = bps;
            ULARGE_INTEGER fa, tot, fr;
            if (GetDiskFreeSpaceExW(root, &fa, &tot, &fr)) { p->free_bytes = fa.QuadPart; p->total_bytes = tot.QuadPart; }
            /* physische Sektorgröße über das Volume-Gerät (\\.\X:) */
            if (root[1] == L':') {
                wchar_t dev[8] = L"\\\\.\\X:";
                dev[4] = root[0];
                HANDLE h = CreateFileW(dev, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
                if (h != INVALID_HANDLE_VALUE) {
                    uint32_t l = p->sector_logical, ph = 0;
                    query_alignment(h, &l, &ph);
                    if (!p->sector_logical) p->sector_logical = l;
                    p->sector_physical = ph;
                    CloseHandle(h);
                }
            }
        }
    } else {
        HANDLE h = CreateFileW(w, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
        if (h == INVALID_HANDLE_VALUE) {
            DWORD e = GetLastError();
            if (e == ERROR_ACCESS_DENIED) { p->exists = 1; snprintf(p->msg, sizeof p->msg, "Zugriff verweigert."); }
            else snprintf(p->msg, sizeof p->msg, "Ziel '%s' nicht gefunden: %s", t->path, sb_errtext(e));
            free(w);
            return;
        }
        p->exists = 1;
        query_alignment(h, &p->sector_logical, &p->sector_physical);
        DISK_GEOMETRY_EX g;
        DWORD br;
        if (t->type == SB_TGT_PHYSICAL &&
            DeviceIoControl(h, IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, NULL, 0, &g, sizeof g, &br, NULL)) {
            p->size_bytes = (uint64_t)g.DiskSize.QuadPart;
            if (!p->sector_logical) p->sector_logical = g.Geometry.BytesPerSector;
        }
        CloseHandle(h);
        if (t->type == SB_TGT_VOLUME) {
            wchar_t root[4] = L"X:\\";
            root[0] = w[4];
            ULARGE_INTEGER fa, tot, fr;
            if (GetDiskFreeSpaceExW(root, &fa, &tot, &fr)) p->size_bytes = tot.QuadPart;
            DWORD spc, bps, fc, tc;
            if (!p->sector_logical && GetDiskFreeSpaceW(root, &spc, &bps, &fc, &tc)) p->sector_logical = bps;
        }
    }
    free(w);
}

int sb_target_prepare_file(const sb_target *t, int *created, int *used_valid_data, int *filled, sb_fill_cb cb, void *cbctx,
                           char *err, size_t errlen) {
    *used_valid_data = 0;
    *filled = 0;
    *created = 0;
    wchar_t *w = sb_u8_to_w(t->path);
    if (!w) { snprintf(err, errlen, "ungültiger Pfad"); return SB_TERR_IO; }
    HANDLE h = CreateFileW(w, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD cerr = GetLastError();
    free(w);
    if (h == INVALID_HANDLE_VALUE) {
        snprintf(err, errlen, "Testdatei '%s' nicht anlegbar: %s", t->path, sb_errtext(cerr));
        return cerr == ERROR_ACCESS_DENIED ? SB_TERR_ACCESS : SB_TERR_IO;
    }
    if (cerr != ERROR_ALREADY_EXISTS) *created = 1;   /* von sbench angelegt -> nach dem Test löschen */
    LARGE_INTEGER cur;
    GetFileSizeEx(h, &cur);
    uint64_t have = (uint64_t)cur.QuadPart;
    if (have >= t->file_size) { CloseHandle(h); return SB_TOK; }   /* vorhandene Datei weiterverwenden */

    LARGE_INTEGER sz;
    sz.QuadPart = (LONGLONG)t->file_size;
    if (!SetFilePointerEx(h, sz, NULL, FILE_BEGIN) || !SetEndOfFile(h)) {
        DWORD e = GetLastError();
        snprintf(err, errlen, "Testdatei '%s' kann nicht auf %llu Bytes vergrößert werden: %s", t->path,
                 (unsigned long long)t->file_size, sb_errtext(e));
        CloseHandle(h);
        return e == ERROR_DISK_FULL ? SB_TERR_IO : SB_TERR_IO;
    }
    if (sb_enable_privilege(L"SeManageVolumePrivilege") && SetFileValidData(h, (LONGLONG)t->file_size)) {
        *used_valid_data = 1;
        CloseHandle(h);
        return SB_TOK;
    }
    CloseHandle(h);

    /* Ohne Privileg: ab der bisherigen Größe mit Zufallsdaten füllen (ungepuffert, sequenziell) */
    w = sb_u8_to_w(t->path);
    h = CreateFileW(w, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
                    FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH, NULL);
    free(w);
    if (h == INVALID_HANDLE_VALUE) {
        snprintf(err, errlen, "Testdatei '%s' nicht zum Füllen öffnbar: %s", t->path, sb_errtext(GetLastError()));
        return SB_TERR_IO;
    }
    const size_t chunk = 1u << 20;
    unsigned char *buf = (unsigned char *)VirtualAlloc(NULL, chunk, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!buf) { CloseHandle(h); snprintf(err, errlen, "kein Speicher"); return SB_TERR_IO; }
    sb_rng r;
    sb_rng_seed(&r, 0xf111da7aULL ^ t->file_size);
    uint64_t pos = have & ~(uint64_t)(chunk - 1);   /* auf 1 MiB abrunden (Sektor-ausgerichtet) */
    int rc = SB_TOK;
    while (pos < t->file_size) {
        uint64_t left = t->file_size - pos;
        DWORD n = (DWORD)(left < chunk ? left : chunk);
        DWORD n_al = (n + 4095u) & ~4095u;  /* ungepuffert: Vielfaches der Sektorgröße */
        sb_rng_fill(&r, buf, n_al);
        LARGE_INTEGER off; off.QuadPart = (LONGLONG)pos;
        DWORD put = 0;
        if (!SetFilePointerEx(h, off, NULL, FILE_BEGIN) || !WriteFile(h, buf, n_al, &put, NULL)) {
            snprintf(err, errlen, "Füllen der Testdatei '%s' fehlgeschlagen: %s", t->path, sb_errtext(GetLastError()));
            rc = SB_TERR_IO;
            break;
        }
        pos += n;
        if (cb && cb(cbctx, pos, t->file_size)) { rc = SB_TERR_STOPPED; break; }
    }
    VirtualFree(buf, 0, MEM_RELEASE);
    CloseHandle(h);
    if (rc == SB_TOK) {
        /* letzte, auf Sektorgröße aufgerundete Schreiboperation zurückschneiden */
        w = sb_u8_to_w(t->path);
        h = CreateFileW(w, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
        free(w);
        if (h != INVALID_HANDLE_VALUE) {
            LARGE_INTEGER s2; s2.QuadPart = (LONGLONG)t->file_size;
            SetFilePointerEx(h, s2, NULL, FILE_BEGIN);
            SetEndOfFile(h);
            CloseHandle(h);
        }
        *filled = 1;
    }
    return rc;
}

int sb_target_open(const sb_target *t, sb_cache_mode cache, int need_write, HANDLE *out, char *err, size_t errlen) {
    wchar_t *w = sb_u8_to_w(t->path);
    if (!w) { snprintf(err, errlen, "ungültiger Pfad"); return SB_TERR_IO; }
    DWORD flags = FILE_FLAG_OVERLAPPED;
    if (cache == SB_CACHE_UNBUFFERED) flags |= FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH;
    else if (cache == SB_CACHE_WRITETHROUGH) flags |= FILE_FLAG_WRITE_THROUGH;
    if (t->type == SB_TGT_FILE) flags |= FILE_ATTRIBUTE_NORMAL;
    DWORD access = GENERIC_READ | (need_write ? GENERIC_WRITE : 0);
    HANDLE h = CreateFileW(w, access, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, flags, NULL);
    free(w);
    if (h == INVALID_HANDLE_VALUE) {
        DWORD e = GetLastError();
        snprintf(err, errlen, "Ziel '%s' nicht öffnbar: %s", t->path, sb_errtext(e));
        if (e == ERROR_ACCESS_DENIED || e == ERROR_PRIVILEGE_NOT_HELD) return SB_TERR_ACCESS;
        if (e == ERROR_FILE_NOT_FOUND || e == ERROR_PATH_NOT_FOUND) return SB_TERR_NOTFOUND;
        return SB_TERR_IO;
    }
    *out = h;
    return SB_TOK;
}

uint64_t sb_target_handle_size(const sb_target *t, HANDLE h) {
    if (t->type == SB_TGT_FILE) {
        LARGE_INTEGER s;
        return GetFileSizeEx(h, &s) ? (uint64_t)s.QuadPart : 0;
    }
    GET_LENGTH_INFORMATION li;
    DWORD br;
    if (DeviceIoControl(h, IOCTL_DISK_GET_LENGTH_INFO, NULL, 0, &li, sizeof li, &br, NULL))
        return (uint64_t)li.Length.QuadPart;
    DISK_GEOMETRY_EX g;
    if (DeviceIoControl(h, IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, NULL, 0, &g, sizeof g, &br, NULL))
        return (uint64_t)g.DiskSize.QuadPart;
    return 0;
}
