/* Storage-Ziele: Prüfen, Anlegen, Öffnen. */
#ifndef SB_TARGET_H
#define SB_TARGET_H
#include "sb/util.h"
#include "sb/job.h"

typedef struct sb_target_probe {
    int      exists;          /* Datei: Verzeichnis existiert; sonst Gerät existiert */
    uint64_t size_bytes;      /* volume/physical: Größe; file: aktuelle Dateigröße (0 = neu) */
    uint32_t sector_logical;
    uint32_t sector_physical;
    uint64_t free_bytes, total_bytes;   /* file: freier/gesamter Platz des Volumes */
    char     msg[256];
} sb_target_probe;

void sb_target_probe_path(const sb_target *t, sb_target_probe *p);

/* Rückgabewerte für Öffnen/Vorbereiten */
enum { SB_TOK = 0, SB_TERR_IO = 1, SB_TERR_ACCESS = 4, SB_TERR_NOTFOUND = 5, SB_TERR_STOPPED = 6 };

/* Fortschritts-Callback für das Anlegen von Testdateien (Bytes geschrieben); !=0 -> Abbruch */
typedef int (*sb_fill_cb)(void *ctx, uint64_t done, uint64_t total);

/* Testdatei anlegen bzw. vergrößern. used_valid_data = 1, wenn SetFileValidData genutzt wurde,
 * filled = 1, wenn mit Zufallsdaten beschrieben wurde. */
/* *created = 1, wenn die Datei vorher nicht existierte (auch bei Fehlerrückgabe gesetzt) */
int sb_target_prepare_file(const sb_target *t, int *created, int *used_valid_data, int *filled, sb_fill_cb cb, void *cbctx,
                           char *err, size_t errlen);

/* Öffnet ein Ziel für den Test (FILE_FLAG_OVERLAPPED + Cache-Flags) */
int sb_target_open(const sb_target *t, sb_cache_mode cache, int need_write, HANDLE *out, char *err, size_t errlen);

/* Größe eines geöffneten Ziels in Bytes */
uint64_t sb_target_handle_size(const sb_target *t, HANDLE h);

/* Privileg im Prozesstoken aktivieren; 1 = aktiv */
int sb_enable_privilege(const wchar_t *name);
int sb_is_admin(void);

#endif
