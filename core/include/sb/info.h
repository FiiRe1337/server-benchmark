/* Systeminformationen (sb-info/1, meta-Block von sb-result/1). */
#ifndef SB_INFO_H
#define SB_INFO_H
#include "cJSON.h"

/* meta-Objekt nach sb-result-1 $defs/meta */
cJSON *sb_info_meta(void);
/* komplettes sb-info/1-Dokument */
cJSON *sb_info_document(void);

#endif
