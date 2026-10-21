/* Import von Iometer-Konfigurationsdateien (.icf) nach sb-job/1 (sbench import-icf). */
#ifndef SB_ICF_H
#define SB_ICF_H
#include <stddef.h>
#include "sb/job.h"

typedef struct sb_icf_opts {
    const char *const *targets;  /* --target: ersetzt die Targets aus dem ICF in Reihenfolge (NULL = aus dem ICF) */
    int ntargets;
    const char *job_name;        /* Fallback für "name", wenn das ICF keine Test Description hat */
} sb_icf_opts;

/* Wandelt ICF-Text in ein sb-job/1-Objekt. Rückgabe 0 = ok (*job gesetzt, Warnungen in w),
 * 2 = ICF fehlerhaft (Fehler E_ICF_* in e, *job = NULL). Pfade in e/w sind JSON-Pointer in den
 * erzeugten Job ("" = ganzer Job), ICF-Zeilennummern stehen in msg. */
int sb_icf_convert(const char *text, size_t len, const sb_icf_opts *o, cJSON **job, sb_verrs *e, sb_verrs *w);

#endif
