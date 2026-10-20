/* Lauf-Verwaltung im Datenverzeichnis (docs/cli.md, Vertrag 1.1):
 * runs\<run_id>.jsonl, runs\<run_id>.lock.json, runs\index.jsonl */
#ifndef SB_RUNS_H
#define SB_RUNS_H
#include "sb/util.h"
#include "cJSON.h"

/* Lock-Datei schreiben (atomar) / löschen */
int  sb_runs_write_lock(const char *run_id, const char *started_utc, const char *job, const char *out);
void sb_runs_remove_lock(const char *run_id);
/* 1 = Lock vorhanden und zugehöriger Prozess läuft noch */
int  sb_runs_lock_alive(const char *run_id);
/* Zeile an runs\index.jsonl anhängen */
int  sb_runs_index_append(const char *run_id, const char *started_utc, const char *ended_utc, const char *out,
                          int exit_code);
/* sb-runs/1-Dokument der laufenden Läufe; verwaiste Locks werden entfernt */
cJSON *sb_runs_running(void);
/* Alle Zeilen aus index.jsonl (Array von Objekten, späterer Eintrag gewinnt bei gleicher run_id) */
cJSON *sb_runs_index(void);

#endif
