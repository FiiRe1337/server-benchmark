/* Systeminfo: OS (RtlGetVersion + Registry), CPU (CPUID bzw. Registry auf ARM64,
 * GetLogicalProcessorInformationEx), RAM, Hypervisor (mehrere Methoden, jede wird
 * in partition_info.methods protokolliert), Volumes, physische Disks, NICs.
 * Nur APIs bis Windows 10 1607 / Server 2016. */
#define _WIN32_DCOM
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <iphlpapi.h>
#include <winioctl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_M_X64) || defined(_M_IX86) || defined(__x86_64__) || defined(__i386__)
#define SB_HAVE_CPUID 1
#ifdef _MSC_VER
#include <intrin.h>
static void sb_cpuid(int leaf, int r[4]) { __cpuid(r, leaf); }
#else
#include <cpuid.h>
static void sb_cpuid(int leaf, int r[4]) {
    unsigned a, b, c, d;
    __cpuid((unsigned)leaf, a, b, c, d);
    r[0] = (int)a; r[1] = (int)b; r[2] = (int)c; r[3] = (int)d;
}
#endif
#else
#define SB_HAVE_CPUID 0
#endif
#include <ole2.h>
#include <wbemidl.h>
#include "sb/info.h"
#include "sb/util.h"
int sb_is_admin(void);   /* target.c */

/* ---- Registry-Helfer --------------------------------------------------- */

static int reg_str(HKEY root, const wchar_t *key, const wchar_t *val, char *out, size_t outlen) {
    wchar_t buf[512];
    DWORD sz = sizeof buf, type = 0;
    if (RegGetValueW(root, key, val, RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ, &type, buf, &sz) != ERROR_SUCCESS) return -1;
    char *u = sb_w_to_u8(buf);
    if (!u) return -1;
    snprintf(out, outlen, "%s", u);
    free(u);
    return 0;
}

static int reg_dword(HKEY root, const wchar_t *key, const wchar_t *val, DWORD *out) {
    DWORD sz = sizeof *out;
    return RegGetValueW(root, key, val, RRF_RT_REG_DWORD, NULL, out, &sz) == ERROR_SUCCESS ? 0 : -1;
}

static void trim(char *s) {
    char *p = s;
    while (*p == ' ') p++;
    if (p != s) memmove(s, p, strlen(p) + 1);
    size_t n = strlen(s);
    while (n && (s[n - 1] == ' ' || s[n - 1] == '\t')) s[--n] = 0;
}

/* ---- OS ------------------------------------------------------------------ */

typedef LONG(WINAPI *RtlGetVersion_t)(PRTL_OSVERSIONINFOW);

static cJSON *os_info(void) {
    RTL_OSVERSIONINFOW v;
    memset(&v, 0, sizeof v);
    v.dwOSVersionInfoSize = sizeof v;
    HMODULE nt = GetModuleHandleW(L"ntdll.dll");
    RtlGetVersion_t rgv = nt ? (RtlGetVersion_t)(void *)GetProcAddress(nt, "RtlGetVersion") : NULL;
    if (rgv) rgv(&v);
    const wchar_t *K = L"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion";
    char name[256] = "Windows", disp[64] = "", inst[64] = "";
    reg_str(HKEY_LOCAL_MACHINE, K, L"ProductName", name, sizeof name);
    if (reg_str(HKEY_LOCAL_MACHINE, K, L"DisplayVersion", disp, sizeof disp))
        reg_str(HKEY_LOCAL_MACHINE, K, L"ReleaseId", disp, sizeof disp);
    reg_str(HKEY_LOCAL_MACHINE, K, L"InstallationType", inst, sizeof inst);
    /* Windows 11 meldet in ProductName weiterhin "Windows 10" */
    if (v.dwBuildNumber >= 22000 && !strncmp(name, "Windows 10", 10)) { name[8] = '1'; name[9] = '1'; }
    if (!strcmp(inst, "Server Core")) strncat(name, " (Server Core)", sizeof name - strlen(name) - 1);
    DWORD ubr = 0;
    reg_dword(HKEY_LOCAL_MACHINE, K, L"UBR", &ubr);
    char ver[96], build[64];
    if (disp[0]) snprintf(ver, sizeof ver, "%s", disp);
    else snprintf(ver, sizeof ver, "%lu.%lu", (unsigned long)v.dwMajorVersion, (unsigned long)v.dwMinorVersion);
    snprintf(build, sizeof build, "%lu.%lu", (unsigned long)v.dwBuildNumber, (unsigned long)ubr);
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "name", name);
    cJSON_AddStringToObject(o, "version", ver);
    cJSON_AddStringToObject(o, "build", build);
    return o;
}

/* ---- CPU ----------------------------------------------------------------- */

static int popcount64(ULONG_PTR x) { int n = 0; while (x) { x &= x - 1; n++; } return n; }

static cJSON *cpu_info(void) {
    char model[128] = "";
#if SB_HAVE_CPUID
    int r[4];
    sb_cpuid((int)0x80000000, r);
    if ((unsigned)r[0] >= 0x80000004u) {
        int b[12];
        sb_cpuid((int)0x80000002, b); sb_cpuid((int)0x80000003, b + 4); sb_cpuid((int)0x80000004, b + 8);
        memcpy(model, b, 48);
        model[48] = 0;
        trim(model);
    }
#endif
    if (!model[0])
        reg_str(HKEY_LOCAL_MACHINE, L"HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0", L"ProcessorNameString",
                model, sizeof model);
    if (!model[0]) snprintf(model, sizeof model, "unbekannt");
    trim(model);
    DWORD mhz = 0;
    reg_dword(HKEY_LOCAL_MACHINE, L"HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0", L"~MHz", &mhz);
    int sockets = 0, cores = 0, threads = 0;
    DWORD len = 0;
    GetLogicalProcessorInformationEx(RelationAll, NULL, &len);
    BYTE *buf = len ? (BYTE *)malloc(len) : NULL;
    if (buf && GetLogicalProcessorInformationEx(RelationAll, (PSYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)buf, &len)) {
        for (DWORD off = 0; off < len;) {
            PSYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX p = (PSYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)(buf + off);
            if (p->Relationship == RelationProcessorPackage) sockets++;
            else if (p->Relationship == RelationProcessorCore) {
                cores++;
                for (WORD g = 0; g < p->Processor.GroupCount; g++) threads += popcount64(p->Processor.GroupMask[g].Mask);
            }
            off += p->Size;
        }
    }
    free(buf);
    if (!threads) { SYSTEM_INFO si; GetNativeSystemInfo(&si); threads = (int)si.dwNumberOfProcessors; }
    if (!cores) cores = threads;
    if (!sockets) sockets = 1;
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "model", model);
    cJSON_AddNumberToObject(o, "sockets", sockets);
    cJSON_AddNumberToObject(o, "cores", cores);
    cJSON_AddNumberToObject(o, "threads", threads);
    cJSON_AddNumberToObject(o, "base_mhz", (double)mhz);
    return o;
}

static double ram_bytes(void) {
    ULONGLONG kb = 0;
    if (GetPhysicallyInstalledSystemMemory(&kb) && kb) return (double)kb * 1024.0;
    MEMORYSTATUSEX ms;
    ms.dwLength = sizeof ms;
    return GlobalMemoryStatusEx(&ms) ? (double)ms.ullTotalPhys : 0;
}

static double ram_free_bytes(void) {
    MEMORYSTATUSEX ms;
    ms.dwLength = sizeof ms;
    return GlobalMemoryStatusEx(&ms) ? (double)ms.ullAvailPhys : 0;
}

/* NUMA-Knoten: CPUs als globale Indizes (Gruppen hintereinander), freier Speicher per
 * GetNumaAvailableMemoryNodeEx. Win32 liefert keinen Gesamt-RAM pro Knoten: bei einem
 * Knoten = installierter RAM, bei mehreren gleichmäßig aufgeteilt (Näherung, docs/core.md). */
static cJSON *numa_nodes(void) {
    cJSON *a = cJSON_CreateArray();
    ULONG hi = 0;
    if (!GetNumaHighestNodeNumber(&hi)) hi = 0;
    WORD ng = GetActiveProcessorGroupCount();
    DWORD goff[64] = {0};
    for (WORD g = 1; g < ng && g < 64; g++) goff[g] = goff[g - 1] + GetActiveProcessorCount((WORD)(g - 1));
    int nodes = 0;
    for (ULONG n = 0; n <= hi && n < 1024; n++) {
        GROUP_AFFINITY ga;
        memset(&ga, 0, sizeof ga);
        if (!GetNumaNodeProcessorMaskEx((USHORT)n, &ga) || !ga.Mask) {
            ULONGLONG fr0 = 0;
            if (!GetNumaAvailableMemoryNodeEx((USHORT)n, &fr0) || !fr0) continue;   /* leerer Knoten */
        }
        nodes++;
    }
    if (!nodes) nodes = 1;
    double total = ram_bytes();
    for (ULONG n = 0; n <= hi && n < 1024; n++) {
        GROUP_AFFINITY ga;
        memset(&ga, 0, sizeof ga);
        ULONGLONG fr = 0;
        BOOL okm = GetNumaNodeProcessorMaskEx((USHORT)n, &ga);
        BOOL okf = GetNumaAvailableMemoryNodeEx((USHORT)n, &fr);
        if ((!okm || !ga.Mask) && (!okf || !fr)) continue;
        cJSON *o = cJSON_CreateObject();
        cJSON_AddNumberToObject(o, "id", (double)n);
        cJSON *c = cJSON_AddArrayToObject(o, "cpus");
        for (int b = 0; b < 64; b++)
            if (okm && (ga.Mask >> b) & 1) cJSON_AddItemToArray(c, cJSON_CreateNumber((double)(goff[ga.Group < 64 ? ga.Group : 0] + (DWORD)b)));
        double rb = nodes == 1 ? total : (double)(uint64_t)(total / nodes);
        if ((double)fr > rb) rb = (double)fr;
        cJSON_AddNumberToObject(o, "ram_bytes", rb);
        cJSON_AddNumberToObject(o, "ram_free_bytes", (double)fr);
        cJSON_AddItemToArray(a, o);
    }
    return a;
}

/* ---- Hypervisor ------------------------------------------------------------ */

typedef struct wmi_cs { int ok; int hv_present; char model[128]; char manufacturer[128]; } wmi_cs;

static void bstr_to_u8(BSTR b, char *out, size_t n) {
    out[0] = 0;
    if (!b) return;
    char *u = sb_w_to_u8(b);
    if (u) { snprintf(out, n, "%s", u); free(u); }
}

/* WMI Win32_ComputerSystem: HypervisorPresent, Model, Manufacturer */
static void wmi_computer_system(wmi_cs *r) {
    memset(r, 0, sizeof *r);
    r->hv_present = -1;
    HRESULT hr = CoInitializeEx(NULL, COINIT_MULTITHREADED);
    int uninit = SUCCEEDED(hr);
    if (FAILED(hr) && hr != RPC_E_CHANGED_MODE) return;
    CoInitializeSecurity(NULL, -1, NULL, NULL, RPC_C_AUTHN_LEVEL_DEFAULT, RPC_C_IMP_LEVEL_IMPERSONATE, NULL, EOAC_NONE, NULL);
    IWbemLocator *loc = NULL;
    IWbemServices *svc = NULL;
    IEnumWbemClassObject *en = NULL;
    hr = CoCreateInstance(&CLSID_WbemLocator, NULL, CLSCTX_INPROC_SERVER, &IID_IWbemLocator, (void **)&loc);
    if (FAILED(hr)) goto done;
    BSTR ns = SysAllocString(L"ROOT\\CIMV2");
    hr = loc->lpVtbl->ConnectServer(loc, ns, NULL, NULL, NULL, 0, NULL, NULL, &svc);
    SysFreeString(ns);
    if (FAILED(hr)) goto done;
    CoSetProxyBlanket((IUnknown *)svc, RPC_C_AUTHN_WINNT, RPC_C_AUTHZ_NONE, NULL, RPC_C_AUTHN_LEVEL_CALL,
                      RPC_C_IMP_LEVEL_IMPERSONATE, NULL, EOAC_NONE);
    BSTR lang = SysAllocString(L"WQL");
    BSTR q = SysAllocString(L"SELECT HypervisorPresent, Model, Manufacturer FROM Win32_ComputerSystem");
    hr = svc->lpVtbl->ExecQuery(svc, lang, q, WBEM_FLAG_FORWARD_ONLY | WBEM_FLAG_RETURN_IMMEDIATELY, NULL, &en);
    SysFreeString(lang); SysFreeString(q);
    if (FAILED(hr)) goto done;
    IWbemClassObject *obj = NULL;
    ULONG got = 0;
    if (SUCCEEDED(en->lpVtbl->Next(en, 5000, 1, &obj, &got)) && got == 1) {
        VARIANT v;
        r->ok = 1;
        VariantInit(&v);
        if (SUCCEEDED(obj->lpVtbl->Get(obj, L"HypervisorPresent", 0, &v, NULL, NULL)) && v.vt == VT_BOOL)
            r->hv_present = v.boolVal ? 1 : 0;
        VariantClear(&v);
        if (SUCCEEDED(obj->lpVtbl->Get(obj, L"Model", 0, &v, NULL, NULL)) && v.vt == VT_BSTR)
            bstr_to_u8(v.bstrVal, r->model, sizeof r->model);
        VariantClear(&v);
        if (SUCCEEDED(obj->lpVtbl->Get(obj, L"Manufacturer", 0, &v, NULL, NULL)) && v.vt == VT_BSTR)
            bstr_to_u8(v.bstrVal, r->manufacturer, sizeof r->manufacturer);
        VariantClear(&v);
        obj->lpVtbl->Release(obj);
    }
done:
    if (en) en->lpVtbl->Release(en);
    if (svc) svc->lpVtbl->Release(svc);
    if (loc) loc->lpVtbl->Release(loc);
    if (uninit) CoUninitialize();
}

static int service_exists(const wchar_t *name) {
    SC_HANDLE scm = OpenSCManagerW(NULL, NULL, SC_MANAGER_CONNECT);
    if (!scm) return -1;
    SC_HANDLE s = OpenServiceW(scm, name, SERVICE_QUERY_STATUS);
    int ex;
    if (s) { ex = 1; CloseServiceHandle(s); }
    else ex = GetLastError() == ERROR_SERVICE_DOES_NOT_EXIST ? 0 : -1;
    CloseServiceHandle(scm);
    return ex;
}

static cJSON *hypervisor_info(void) {
    cJSON *hv = cJSON_CreateObject();
    cJSON *pi = cJSON_CreateObject();
    cJSON *m = cJSON_CreateObject();
    int present = 0, role_host = 0, role_guest = 0;
    char vendor[16] = "";
    const char *src = "none";
    int have_cpuid = 0;

#if SB_HAVE_CPUID
    {
        int r[4];
        sb_cpuid(1, r);
        int bit = (r[2] >> 31) & 1;
        cJSON *c = cJSON_CreateObject();
        cJSON_AddBoolToObject(c, "hypervisor_bit", bit);
        if (bit) {
            sb_cpuid(0x40000000, r);
            memcpy(vendor, &r[1], 4); memcpy(vendor + 4, &r[2], 4); memcpy(vendor + 8, &r[3], 4);
            vendor[12] = 0;
            cJSON_AddStringToObject(c, "vendor", vendor);
            unsigned maxleaf = (unsigned)r[0];
            if (!strcmp(vendor, "Microsoft Hv") && maxleaf >= 0x40000003u) {
                sb_cpuid(0x40000003, r);
                int create_partitions = r[1] & 1;
                cJSON_AddBoolToObject(c, "create_partitions", create_partitions);
                if (create_partitions) role_host = 1; else role_guest = 1;
            } else {
                role_guest = 1;  /* fremder Hypervisor (KVM, VMware, Xen ...) -> Gast */
            }
            present = 1;
        }
        cJSON_AddItemToObject(m, "cpuid", c);
        have_cpuid = 1;
        src = "cpuid";
    }
#endif
    /* Registry: Hyper-V-Gast (Integrationsdienste/KVP) */
    HKEY k;
    int guest_reg = 0;
    char phys_host[256] = "";
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Microsoft\\Virtual Machine\\Guest\\Parameters", 0, KEY_READ, &k) == ERROR_SUCCESS) {
        guest_reg = 1;
        RegCloseKey(k);
        reg_str(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Microsoft\\Virtual Machine\\Guest\\Parameters", L"PhysicalHostNameFullyQualified",
                phys_host, sizeof phys_host);
        if (!phys_host[0])
            reg_str(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Microsoft\\Virtual Machine\\Guest\\Parameters", L"PhysicalHostName",
                    phys_host, sizeof phys_host);
    }
    cJSON_AddBoolToObject(m, "registry_guest_parameters", guest_reg);
    /* Dienst vmms = Hyper-V-Rolle installiert */
    int vmms = service_exists(L"vmms");
    if (vmms < 0) cJSON_AddNullToObject(m, "service_vmms"); else cJSON_AddBoolToObject(m, "service_vmms", vmms);
    /* IsProcessorFeaturePresent: Virtualisierung in Firmware aktiviert (PF_VIRT_FIRMWARE_ENABLED = 21) */
    cJSON_AddBoolToObject(m, "pf_virt_firmware_enabled", IsProcessorFeaturePresent(21) ? 1 : 0);
    /* WMI */
    wmi_cs w;
    wmi_computer_system(&w);
    cJSON *wj = cJSON_CreateObject();
    if (w.hv_present < 0) cJSON_AddNullToObject(wj, "hypervisor_present"); else cJSON_AddBoolToObject(wj, "hypervisor_present", w.hv_present);
    cJSON_AddStringToObject(wj, "model", w.model);
    cJSON_AddStringToObject(wj, "manufacturer", w.manufacturer);
    cJSON_AddBoolToObject(wj, "ok", w.ok);
    cJSON_AddItemToObject(m, "wmi_computer_system", wj);

    int wmi_vm_model = !strcmp(w.model, "Virtual Machine") || strstr(w.model, "VMware") || strstr(w.model, "KVM") ||
                       strstr(w.manufacturer, "QEMU") || strstr(w.model, "HVM domU");
    if (!have_cpuid) {
        /* ARM64 bzw. ohne CPUID: Registry/Dienst/WMI */
        present = w.hv_present == 1 || guest_reg || wmi_vm_model;
        if (guest_reg || wmi_vm_model) { role_guest = 1; src = guest_reg ? "registry" : "wmi"; }
        else if (present && vmms == 1) { role_host = 1; src = "service_vmms+wmi"; }
        else if (present) src = "wmi";
        if (present && !vendor[0] && (guest_reg || vmms == 1 || !strcmp(w.manufacturer, "Microsoft Corporation")))
            snprintf(vendor, sizeof vendor, "Microsoft Hv");
    } else if (!present && (guest_reg || wmi_vm_model)) {
        /* Hypervisor-Bit verborgen, aber deutliche Gast-Indizien */
        present = 1; role_guest = 1; src = guest_reg ? "registry" : "wmi";
    }
    const char *role = role_host ? "host" : role_guest ? "guest" : "none";
    cJSON_AddBoolToObject(hv, "present", present);
    if (vendor[0]) cJSON_AddStringToObject(hv, "vendor", vendor); else cJSON_AddNullToObject(hv, "vendor");
    cJSON_AddStringToObject(hv, "role", role);
    cJSON_AddBoolToObject(pi, "is_root", role_host);
    cJSON_AddBoolToObject(pi, "provisional", 1);
    cJSON_AddStringToObject(pi, "role_source", src);
    if (phys_host[0]) cJSON_AddStringToObject(pi, "physical_host", phys_host);
    cJSON_AddItemToObject(pi, "methods", m);
    cJSON_AddItemToObject(hv, "partition_info", pi);
    return hv;
}

/* ---- Disks ---------------------------------------------------------------- */

static const char *bus_name(int t) {
    switch (t) {
    case 1: return "SCSI"; case 2: return "ATAPI"; case 3: return "ATA"; case 4: return "IEEE1394";
    case 5: return "SSA"; case 6: return "Fibre"; case 7: return "USB"; case 8: return "RAID";
    case 9: return "iSCSI"; case 10: return "SAS"; case 11: return "SATA"; case 12: return "SD";
    case 13: return "MMC"; case 14: return "Virtual"; case 15: return "FileBackedVirtual"; case 16: return "Spaces";
    case 17: return "NVMe"; case 18: return "SCM"; case 19: return "UFS";
    default: return "Unknown";
    }
}

static cJSON *physical_disks(void) {
    cJSON *arr = cJSON_CreateArray();
    for (int n = 0; n < 128; n++) {
        wchar_t path[40];
        _snwprintf(path, 40, L"\\\\.\\PhysicalDrive%d", n);
        path[39] = 0;
        HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
        int has_read = h != INVALID_HANDLE_VALUE;
        if (!has_read) h = CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
        if (h == INVALID_HANDLE_VALUE) continue;
        char model[256] = "";
        const char *bus = "Unknown";
        STORAGE_PROPERTY_QUERY q;
        memset(&q, 0, sizeof q);
        q.PropertyId = StorageDeviceProperty;
        q.QueryType = PropertyStandardQuery;
        BYTE buf[2048];
        DWORD br = 0;
        if (DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY, &q, sizeof q, buf, sizeof buf, &br, NULL) &&
            br >= sizeof(STORAGE_DEVICE_DESCRIPTOR)) {
            STORAGE_DEVICE_DESCRIPTOR *d = (STORAGE_DEVICE_DESCRIPTOR *)buf;
            char vendor[128] = "", prod[128] = "";
            if (d->VendorIdOffset && d->VendorIdOffset < br) snprintf(vendor, sizeof vendor, "%s", (char *)buf + d->VendorIdOffset);
            if (d->ProductIdOffset && d->ProductIdOffset < br) snprintf(prod, sizeof prod, "%s", (char *)buf + d->ProductIdOffset);
            trim(vendor); trim(prod);
            if (vendor[0] && strncmp(prod, vendor, strlen(vendor))) snprintf(model, sizeof model, "%s %s", vendor, prod);
            else snprintf(model, sizeof model, "%s", prod);
            bus = bus_name((int)d->BusType);
        }
        uint64_t size = 0;
        GET_LENGTH_INFORMATION li;
        if (has_read && DeviceIoControl(h, IOCTL_DISK_GET_LENGTH_INFO, NULL, 0, &li, sizeof li, &br, NULL))
            size = (uint64_t)li.Length.QuadPart;
        DISK_GEOMETRY_EX g;
        uint32_t slog = 0, sphy = 0;
        if (DeviceIoControl(h, IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, NULL, 0, &g, sizeof g, &br, NULL)) {
            if (!size) size = (uint64_t)g.DiskSize.QuadPart;
            slog = g.Geometry.BytesPerSector;
        }
        memset(&q, 0, sizeof q);
        q.PropertyId = StorageAccessAlignmentProperty;
        q.QueryType = PropertyStandardQuery;
        STORAGE_ACCESS_ALIGNMENT_DESCRIPTOR ad;
        memset(&ad, 0, sizeof ad);
        if (DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY, &q, sizeof q, &ad, sizeof ad, &br, NULL) && br >= 16) {
            if (ad.BytesPerLogicalSector) slog = ad.BytesPerLogicalSector;
            sphy = ad.BytesPerPhysicalSector;
        }
        CloseHandle(h);
        cJSON *o = cJSON_CreateObject();
        char p8[40];
        snprintf(p8, sizeof p8, "\\\\.\\PhysicalDrive%d", n);
        cJSON_AddStringToObject(o, "path", p8);
        cJSON_AddStringToObject(o, "model", model);
        cJSON_AddNumberToObject(o, "size_bytes", (double)size);
        cJSON_AddStringToObject(o, "bus", bus);
        if (slog) cJSON_AddNumberToObject(o, "sector_logical", slog);
        if (sphy) cJSON_AddNumberToObject(o, "sector_physical", sphy);
        cJSON_AddItemToArray(arr, o);
    }
    return arr;
}

static cJSON *volumes(void) {
    cJSON *arr = cJSON_CreateArray();
    wchar_t vol[MAX_PATH];
    HANDLE f = FindFirstVolumeW(vol, MAX_PATH);
    if (f == INVALID_HANDLE_VALUE) return arr;
    do {
        wchar_t names[1024];
        DWORD len = 0;
        if (!GetVolumePathNamesForVolumeNameW(vol, names, 1024, &len)) continue;
        /* erster Laufwerksbuchstabe "X:\" */
        wchar_t letter = 0;
        for (wchar_t *p = names; *p; p += wcslen(p) + 1)
            if (p[1] == L':' && p[2] == L'\\' && p[3] == 0) { letter = p[0]; break; }
        if (!letter) continue;  /* Schema verlangt \\.\X: */
        wchar_t label[MAX_PATH + 1] = L"", fs[MAX_PATH + 1] = L"";
        if (!GetVolumeInformationW(vol, label, MAX_PATH + 1, NULL, NULL, NULL, fs, MAX_PATH + 1)) continue;
        ULARGE_INTEGER fa, tot, fr;
        if (!GetDiskFreeSpaceExW(vol, &fa, &tot, &fr)) continue;
        if (letter >= L'a' && letter <= L'z') letter = (wchar_t)(letter - 32);
        char path[8] = "\\\\.\\X:", L[2] = "X";
        path[4] = (char)letter; L[0] = (char)letter;
        char *l8 = sb_w_to_u8(label), *f8 = sb_w_to_u8(fs);
        cJSON *o = cJSON_CreateObject();
        cJSON_AddStringToObject(o, "path", path);
        cJSON_AddStringToObject(o, "letter", L);
        cJSON_AddStringToObject(o, "label", l8 ? l8 : "");
        cJSON_AddStringToObject(o, "fs", f8 ? f8 : "");
        cJSON_AddNumberToObject(o, "size_bytes", (double)tot.QuadPart);
        cJSON_AddNumberToObject(o, "free_bytes", (double)fr.QuadPart);
        free(l8); free(f8);
        cJSON_AddItemToArray(arr, o);
    } while (FindNextVolumeW(f, vol, MAX_PATH));
    FindVolumeClose(f);
    return arr;
}

/* ---- NICs ------------------------------------------------------------------ */

static cJSON *nics(void) {
    cJSON *arr = cJSON_CreateArray();
    ULONG len = 32 * 1024;
    IP_ADAPTER_ADDRESSES *aa = NULL;
    ULONG rc = ERROR_BUFFER_OVERFLOW;
    for (int tries = 0; tries < 3 && rc == ERROR_BUFFER_OVERFLOW; tries++) {
        free(aa);
        aa = (IP_ADAPTER_ADDRESSES *)malloc(len);
        if (!aa) return arr;
        rc = GetAdaptersAddresses(AF_UNSPEC, GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER,
                                  NULL, aa, &len);
    }
    if (rc != NO_ERROR) { free(aa); return arr; }
    for (IP_ADAPTER_ADDRESSES *a = aa; a; a = a->Next) {
        if (a->IfType == IF_TYPE_SOFTWARE_LOOPBACK || a->IfType == IF_TYPE_TUNNEL) continue;
        char *desc = sb_w_to_u8(a->Description && a->Description[0] ? a->Description : a->FriendlyName);
        cJSON *o = cJSON_CreateObject();
        cJSON_AddStringToObject(o, "name", desc ? desc : "");
        free(desc);
        cJSON *v4 = cJSON_AddArrayToObject(o, "ipv4");
        cJSON *v6 = cJSON_AddArrayToObject(o, "ipv6");
        for (IP_ADAPTER_UNICAST_ADDRESS *u = a->FirstUnicastAddress; u; u = u->Next) {
            wchar_t s[64];
            DWORD sl = 64;
            if (WSAAddressToStringW(u->Address.lpSockaddr, (DWORD)u->Address.iSockaddrLength, NULL, s, &sl) == 0) {
                char *s8 = sb_w_to_u8(s);
                if (s8) {
                    cJSON_AddItemToArray(u->Address.lpSockaddr->sa_family == AF_INET ? v4 : v6, cJSON_CreateString(s8));
                    free(s8);
                }
            }
        }
        if (a->OperStatus == IfOperStatusUp && a->TransmitLinkSpeed && a->TransmitLinkSpeed != (ULONG64)-1)
            cJSON_AddNumberToObject(o, "link_mbps", (double)(a->TransmitLinkSpeed / 1000000ULL));
        else
            cJSON_AddNullToObject(o, "link_mbps");
        cJSON_AddItemToArray(arr, o);
    }
    free(aa);
    return arr;
}

/* ---- Zusammenbau ------------------------------------------------------------- */

static cJSON *meta_with(cJSON *drives, cJSON *nic) {
    cJSON *m = cJSON_CreateObject();
    wchar_t host[256];
    DWORD hl = 256;
    char *h8 = NULL;
    if (GetComputerNameExW(ComputerNameDnsHostname, host, &hl)) h8 = sb_w_to_u8(host);
    cJSON_AddStringToObject(m, "host", h8 ? h8 : "unbekannt");
    free(h8);
    cJSON_AddItemToObject(m, "os", os_info());
    cJSON_AddItemToObject(m, "cpu", cpu_info());
    cJSON_AddNumberToObject(m, "ram_bytes", ram_bytes());
    cJSON_AddItemToObject(m, "hypervisor", hypervisor_info());
    cJSON_AddItemToObject(m, "drives", drives);
    cJSON_AddItemToObject(m, "nics", nic);
    cJSON_AddNumberToObject(m, "ram_free_bytes", ram_free_bytes());
    cJSON_AddItemToObject(m, "numa_nodes", numa_nodes());
    cJSON_AddBoolToObject(m, "elevated", sb_is_admin());
    cJSON_AddStringToObject(m, "data_dir", sb_data_dir());
    return m;
}

static void wsa_once(void) {
    static int done = 0;
    if (!done) { WSADATA w; WSAStartup(MAKEWORD(2, 2), &w); done = 1; }
}

cJSON *sb_info_meta(void) {
    wsa_once();
    return meta_with(physical_disks(), nics());
}

cJSON *sb_info_document(void) {
    wsa_once();
    cJSON *d = cJSON_CreateObject();
    cJSON_AddStringToObject(d, "schema", "sb-info/1");
    cJSON *drives = physical_disks();
    cJSON *nic = nics();
    cJSON *m = meta_with(cJSON_Duplicate(drives, 1), cJSON_Duplicate(nic, 1));
    cJSON_AddItemToObject(d, "meta", m);
    cJSON *t = cJSON_CreateObject();
    cJSON_AddItemToObject(t, "volumes", volumes());
    cJSON_AddItemToObject(t, "physical_disks", drives);
    cJSON_AddItemToObject(t, "nics", nic);
    cJSON_AddItemToObject(d, "targets", t);
    return d;
}
