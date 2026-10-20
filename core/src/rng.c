#include <stddef.h>
#include <string.h>
#include "sb/rng.h"

static inline uint64_t rotl(uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }

static uint64_t splitmix64(uint64_t *x) {
    uint64_t z = (*x += 0x9e3779b97f4a7c15ULL);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

void sb_rng_seed(sb_rng *r, uint64_t seed) {
    uint64_t x = seed;
    for (int i = 0; i < 4; i++) r->s[i] = splitmix64(&x);
}

uint64_t sb_rng_next(sb_rng *r) {
    uint64_t *s = r->s;
    const uint64_t result = rotl(s[1] * 5, 7) * 9;
    const uint64_t t = s[1] << 17;
    s[2] ^= s[0]; s[3] ^= s[1]; s[1] ^= s[2]; s[0] ^= s[3];
    s[2] ^= t;
    s[3] = rotl(s[3], 45);
    return result;
}

uint64_t sb_rng_below(sb_rng *r, uint64_t n) {
    if (n <= 1) return 0;
    /* Rejection sampling: Schwelle = 2^64 mod n */
    uint64_t threshold = (0 - n) % n;
    for (;;) {
        uint64_t x = sb_rng_next(r);
        if (x >= threshold) return x % n;
    }
}

double sb_rng_unit(sb_rng *r) { return (double)(sb_rng_next(r) >> 11) * (1.0 / 9007199254740992.0); }

void sb_rng_fill(sb_rng *r, void *buf, size_t len) {
    unsigned char *p = (unsigned char *)buf;
    while (len >= 8) { uint64_t v = sb_rng_next(r); memcpy(p, &v, 8); p += 8; len -= 8; }
    if (len) { uint64_t v = sb_rng_next(r); memcpy(p, &v, len); }
}
