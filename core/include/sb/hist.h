/* Log-lineares Latenz-Histogramm (HDR-artig).
 * Werte in Nanosekunden. Bis 255 ns exakt, darüber 128 Unter-Buckets je
 * Zweierpotenz -> relative Bucketbreite <= 1/128 (~0,78 %), Fehler des
 * Bucket-Mittelwerts <= ~0,4 %. Abgedeckt: 0 ns .. 2^38 ns (~275 s).
 * Ein Histogramm gehört genau einem Worker-Thread (lockfrei), am Ende wird
 * mit sb_hist_merge zusammengeführt. */
#ifndef SB_HIST_H
#define SB_HIST_H
#include <stdint.h>

#define SB_HIST_SUB      128u
#define SB_HIST_MAX_EXP  37u                 /* höchste Zweierpotenz */
#define SB_HIST_BUCKETS  (256u + (SB_HIST_MAX_EXP - 7u) * SB_HIST_SUB)

typedef struct sb_hist {
    uint64_t count;
    uint64_t min_ns, max_ns;
    double   sum_ns, sumsq_ns;               /* für exakten Mittelwert/Stddev */
    uint64_t overflow;                       /* Werte > Bereich (auf max geklemmt) */
    uint64_t b[SB_HIST_BUCKETS];
} sb_hist;

void     sb_hist_init(sb_hist *h);
void     sb_hist_record(sb_hist *h, uint64_t ns);
void     sb_hist_merge(sb_hist *dst, const sb_hist *src);
uint32_t sb_hist_index(uint64_t ns);
uint64_t sb_hist_bucket_lo(uint32_t idx);
uint64_t sb_hist_bucket_hi(uint32_t idx);    /* inklusiv */
/* p in [0,100]; Ergebnis in ns (Bucket-Mitte, auf [min,max] geklemmt) */
double   sb_hist_percentile(const sb_hist *h, double p);
double   sb_hist_mean(const sb_hist *h);
double   sb_hist_stddev(const sb_hist *h);

#endif
