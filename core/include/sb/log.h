/* Lauf-Log: Zeilen "<UTC-Zeitstempel> <LEVEL> <Text>" in logs/<run_id>.log */
#ifndef SB_LOG_H
#define SB_LOG_H
#include "sb/util.h"

typedef struct sb_log { HANDLE h; CRITICAL_SECTION cs; char path[MAX_PATH * 3]; } sb_log;

int  sb_log_open(sb_log *l, const char *path_u8);   /* 0 = ok; bei Fehler bleibt Log stumm */
void sb_log_close(sb_log *l);
void sb_log_write(sb_log *l, const char *level, const char *fmt, ...);
/* Fehler inkl. GetLastError-Text */
void sb_log_winerr(sb_log *l, DWORD err, const char *fmt, ...);

#define SB_LOGI(l, ...) sb_log_write((l), "INFO", __VA_ARGS__)
#define SB_LOGW(l, ...) sb_log_write((l), "WARN", __VA_ARGS__)
#define SB_LOGE(l, ...) sb_log_write((l), "ERROR", __VA_ARGS__)

#endif
