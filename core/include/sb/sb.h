/* Öffentliche API des sbench-Messkerns (sbcore_static / sbcore.dll).
 * Alle Strings sind UTF-8. Rückgabewerte entsprechen den Exit-Codes aus docs/cli.md:
 * 0 ok, 1 Fehler, 2 ungültiger Job/Argumente, 3 abgebrochen, 4 fehlende Rechte. */
#ifndef SB_SB_H
#define SB_SB_H
#include <stddef.h>
#include <stdint.h>

#if defined(SB_BUILD_DLL)
#define SB_API __declspec(dllexport)
#elif defined(SB_USE_DLL)
#define SB_API __declspec(dllimport)
#else
#define SB_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

enum { SB_EXIT_OK = 0, SB_EXIT_ERROR = 1, SB_EXIT_INVALID = 2, SB_EXIT_ABORTED = 3, SB_EXIT_RIGHTS = 4 };

typedef struct sb_run_options {
    const char *job_path;        /* --job (Pflicht) */
    const char *out_path;        /* --out; NULL -> <datenverzeichnis>\results\<run_id>.json */
    const char *progress;        /* --progress: NULL|"stdout"|"pipe:<name>"|"file:<pfad>" */
    const char *id;              /* --id; NULL -> generiert */
    const char *log_path;        /* --log; NULL -> <datenverzeichnis>\logs\<run_id>.log */
    uint32_t    tick_ms;         /* --tick-ms, 0 -> 1000 */
    int         allow_destructive; /* --allow-destructive: Schreibtests auf volume/physical mit allow_destructive */
    int         keep_files;        /* --keep-files: von sbench angelegte Testdateien nicht löschen */
    const char *cmdline;         /* nur fürs Log */
} sb_run_options;

typedef struct sb_bundle_options {
    const char  *out_path;       /* --out (Pflicht) */
    int          all;            /* --all: alle beendeten Läufe aus runs\index.jsonl */
    const char **results;        /* --results: Ergebnisdateien oder Ordner */
    int          nresults;
    const char **includes;       /* --include: Ordner -> extra/<ordnername>/ */
    int          nincludes;
    int          anonymize;      /* --anonymize */
    const char  *progress;       /* wie bei run */
} sb_bundle_options;

typedef struct sb_import_icf_options {
    const char  *icf_path;       /* Iometer-.icf (Pflicht) */
    const char **targets;        /* --target: ersetzt die ICF-Targets in Reihenfolge */
    int          ntargets;
} sb_import_icf_options;

SB_API const char *sb_version(void);
SB_API const char *sb_data_directory(void);
/* --data-dir: vor allen anderen Regeln; vor jedem anderen Aufruf setzen. 0 = ok */
SB_API int  sb_set_data_directory(const char *path);
SB_API int  sb_run(const sb_run_options *o);
SB_API int  sb_stop(const char *id, char *msg, size_t msglen);
/* sb-validate/1 als JSON-Text (mit sb_free freigeben) */
SB_API int  sb_validate(const char *job_path, char **json_out);
/* sb-runs/1 der laufenden Läufe */
SB_API int  sb_list_running(char **json_out);
/* sb-info/1 als JSON-Text */
SB_API int  sb_info(char **json_out);
SB_API int  sb_export_csv(const char *result_path, const char *csv_path, const char *style, char *err, size_t errlen);
SB_API int  sb_bundle(const sb_bundle_options *o, char *err, size_t errlen);
/* Iometer-.icf -> sb-job/1. *job_json = erzeugter Job (nur bei Rückgabe 0), *report_json = sb-validate/1
 * mit E_ICF_x/W_ICF_x und den Meldungen der Job-Prüfung. Rückgabe 0 ok, 1 Datei nicht lesbar, 2 ICF fehlerhaft. */
SB_API int  sb_import_icf(const sb_import_icf_options *o, char **job_json, char **report_json);
SB_API void sb_free(void *p);

#ifdef __cplusplus
}
#endif
#endif
