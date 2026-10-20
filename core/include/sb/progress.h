/* Fortschritts-Senke für JSONL-Events. Jede Zeile wird sofort per WriteFile
 * (ohne CRT-Puffer) ausgegeben, d.h. nach jeder Zeile "geflusht".
 * Ziele: "stdout", "pipe:\\.\pipe\sbench-<id>" (sbench ist Client),
 * "file:<pfad>" (wird angehängt), NULL/"none" = aus. */
#ifndef SB_PROGRESS_H
#define SB_PROGRESS_H
#include "sb/util.h"
#include "cJSON.h"

typedef struct sb_progress {
    HANDLE h;
    int    is_console;
    int    owns;
    int    broken;      /* Gegenseite weg -> weitere Writes ignorieren */
    int    is_pipe;
    HANDLE mirror;      /* runs\<run_id>.jsonl: bekommt immer jedes Event */
    void  (*on_broken)(void *ctx, DWORD err);  /* einmalig beim Abbruch der Pipe */
    void  *ctx;
    CRITICAL_SECTION cs;
} sb_progress;

/* 0 = ok, sonst -1 (err gesetzt) */
int  sb_progress_open(sb_progress *p, const char *spec, char *err, size_t errlen);
void sb_progress_close(sb_progress *p);
/* zusätzliche Kopie aller Events in eine Datei (wird neu angelegt) */
int  sb_progress_mirror(sb_progress *p, const char *path, char *err, size_t errlen);
/* Serialisiert obj kompakt + '\n'; obj gehört weiter dem Aufrufer */
void sb_progress_emit(sb_progress *p, const cJSON *obj);

#endif
