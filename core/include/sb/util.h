/* Hilfsfunktionen: Strings, Zeit, Pfade, Fehlertexte. Reines Win32. */
#ifndef SB_UTIL_H
#define SB_UTIL_H
#include <stddef.h>
#include <stdint.h>
#include <stdarg.h>
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

/* UTF-8 <-> UTF-16, Ergebnis mit free() freigeben */
wchar_t *sb_u8_to_w(const char *s);
char    *sb_w_to_u8(const wchar_t *w);
char    *sb_strdup(const char *s);
char    *sb_aprintf(const char *fmt, ...);

/* Zeit */
uint64_t sb_qpc(void);
uint64_t sb_qpf(void);
double   sb_qpc_to_ns(uint64_t ticks);
void     sb_utc_iso(char out[32]);           /* 2026-10-20T12:00:00Z */
void     sb_utc_iso_ms(char out[40]);        /* 2026-10-20T12:00:00.123Z */
void     sb_new_run_id(char out[32]);        /* 20261020-120000-1a2b */

/* letzte Win32-Fehlermeldung als UTF-8 (statischer Thread-Puffer) */
const char *sb_errtext(DWORD err);

/* Dateien */
int   sb_read_file(const char *path_u8, char **data, size_t *len); /* 0 = ok */
int   sb_write_file(const char *path_u8, const void *data, size_t len);
int   sb_file_exists(const char *path_u8);
int   sb_is_dir(const char *path_u8);
int   sb_mkdirs(const char *path_u8);
char *sb_path_join(const char *a, const char *b);
const char *sb_basename(const char *p);

/* Datenverzeichnis (docs/cli.md, Vertrag 1.1): SBENCH_DATA_DIR, sonst data\ neben
 * sbench.exe (falls beschreibbar), sonst %ProgramData%\sbench.
 * Unterordner results\, jobs\, logs\, runs\ werden angelegt. */
const char *sb_data_dir(void);

/* Laufzeit-Größenangaben "4K", "8KiB", "256MiB", "1G", Zahl */
int sb_parse_size(const char *s, uint64_t *out);

#endif
