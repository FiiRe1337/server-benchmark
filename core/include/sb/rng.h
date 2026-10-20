/* xoshiro256** – schneller, reproduzierbarer PRNG (Public Domain, Blackman/Vigna). */
#ifndef SB_RNG_H
#define SB_RNG_H
#include <stdint.h>

typedef struct sb_rng { uint64_t s[4]; } sb_rng;

void     sb_rng_seed(sb_rng *r, uint64_t seed);
uint64_t sb_rng_next(sb_rng *r);
/* gleichverteilt in [0, n), ohne Modulo-Bias (Lemire) */
uint64_t sb_rng_below(sb_rng *r, uint64_t n);
double   sb_rng_unit(sb_rng *r); /* [0,1) */
void     sb_rng_fill(sb_rng *r, void *buf, size_t len);

#endif
