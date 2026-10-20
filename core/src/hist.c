#include <string.h>
#include <math.h>
#include "sb/hist.h"

static unsigned floor_log2_u64(uint64_t v) {
    unsigned r = 0;
    while (v >>= 1) r++;
    return r;
}

void sb_hist_init(sb_hist *h) {
    memset(h, 0, sizeof(*h));
    h->min_ns = UINT64_MAX;
}

uint32_t sb_hist_index(uint64_t ns) {
    if (ns < 256) return (uint32_t)ns;
    unsigned e = floor_log2_u64(ns);
    if (e > SB_HIST_MAX_EXP) return SB_HIST_BUCKETS - 1;
    unsigned shift = e - 7;
    uint64_t m = ns >> shift;                 /* 128..255 */
    return 256u + (e - 8u) * SB_HIST_SUB + (uint32_t)(m - 128u);
}

uint64_t sb_hist_bucket_lo(uint32_t idx) {
    if (idx < 256) return idx;
    uint32_t k = idx - 256u;
    unsigned e = 8u + k / SB_HIST_SUB;
    uint64_t m = 128u + k % SB_HIST_SUB;
    return m << (e - 7u);
}

uint64_t sb_hist_bucket_hi(uint32_t idx) {
    if (idx < 256) return idx;
    uint32_t k = idx - 256u;
    unsigned e = 8u + k / SB_HIST_SUB;
    uint64_t m = 128u + k % SB_HIST_SUB;
    return ((m + 1u) << (e - 7u)) - 1u;
}

void sb_hist_record(sb_hist *h, uint64_t ns) {
    uint32_t i = sb_hist_index(ns);
    if (ns > sb_hist_bucket_hi(SB_HIST_BUCKETS - 1)) h->overflow++;
    h->b[i]++;
    h->count++;
    if (ns < h->min_ns) h->min_ns = ns;
    if (ns > h->max_ns) h->max_ns = ns;
    double d = (double)ns;
    h->sum_ns += d;
    h->sumsq_ns += d * d;
}

void sb_hist_merge(sb_hist *dst, const sb_hist *src) {
    if (!src->count) return;
    for (uint32_t i = 0; i < SB_HIST_BUCKETS; i++) dst->b[i] += src->b[i];
    dst->count += src->count;
    dst->overflow += src->overflow;
    dst->sum_ns += src->sum_ns;
    dst->sumsq_ns += src->sumsq_ns;
    if (src->min_ns < dst->min_ns) dst->min_ns = src->min_ns;
    if (src->max_ns > dst->max_ns) dst->max_ns = src->max_ns;
}

double sb_hist_percentile(const sb_hist *h, double p) {
    if (!h->count) return 0.0;
    if (p <= 0) return (double)h->min_ns;
    if (p >= 100) return (double)h->max_ns;
    /* Rang = ceil(p/100 * n), mindestens 1 */
    double rr = ceil(p / 100.0 * (double)h->count - 1e-9);
    uint64_t rank = rr < 1 ? 1 : (uint64_t)rr;
    uint64_t cum = 0;
    for (uint32_t i = 0; i < SB_HIST_BUCKETS; i++) {
        cum += h->b[i];
        if (cum >= rank) {
            double lo = (double)sb_hist_bucket_lo(i), hi = (double)sb_hist_bucket_hi(i);
            double v = (lo + hi) / 2.0;
            if (v < (double)h->min_ns) v = (double)h->min_ns;
            if (v > (double)h->max_ns) v = (double)h->max_ns;
            return v;
        }
    }
    return (double)h->max_ns;
}

double sb_hist_mean(const sb_hist *h) { return h->count ? h->sum_ns / (double)h->count : 0.0; }

double sb_hist_stddev(const sb_hist *h) {
    if (h->count < 2) return 0.0;
    double n = (double)h->count, m = h->sum_ns / n;
    double var = h->sumsq_ns / n - m * m;
    return var > 0 ? sqrt(var) : 0.0;
}
