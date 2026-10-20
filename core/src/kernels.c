/* Rechenkerne für Memory- und CPU-Tests.
 * Speicher: Lesen = XOR-Akkumulation über 4 Register, Schreiben = konstantes Muster,
 * Kopieren = Laden + Speichern; je ISA (scalar, SSE2, AVX2, AVX-512 auf x64, NEON auf ARM64),
 * optional mit Non-temporal Stores (x64: MOVNTI/MOVNTDQ + SFENCE).
 * "scalar" ist absichtlich nicht vektorisiert (pragma/attribute). */
#include <string.h>
#include "sb/kernels.h"
#include "sb/rng.h"
#include "sb/sha256.h"

#if defined(_M_X64) || defined(__x86_64__)
#define SB_X64 1
#include <immintrin.h>
#elif defined(_M_ARM64) || defined(__aarch64__)
#define SB_ARM64 1
#ifdef _MSC_VER
#include <arm64_neon.h>
#else
#include <arm_neon.h>
#endif
#endif

#if defined(__GNUC__) && !defined(__clang__)
#define SB_TGT(x) __attribute__((target(x)))
#define SB_NOVEC __attribute__((optimize("no-tree-vectorize")))
#elif defined(__clang__)
#define SB_TGT(x) __attribute__((target(x)))
#define SB_NOVEC
#else
#define SB_TGT(x)
#define SB_NOVEC
#endif

/* ---- scalar ---------------------------------------------------------------- */

SB_NOVEC static uint64_t rd_scalar(const void *p, size_t bytes) {
    const volatile uint64_t *q = (const volatile uint64_t *)p;   /* volatile: keine Vektorisierung, jede Last real */
    uint64_t a = 0, b = 0, c = 0, d = 0;
    size_t n = bytes / 8;
#ifdef _MSC_VER
#pragma loop(no_vector)
#endif
    for (size_t i = 0; i < n; i += 4) { a ^= q[i]; b ^= q[i + 1]; c ^= q[i + 2]; d ^= q[i + 3]; }
    return a ^ b ^ c ^ d;
}

SB_NOVEC static void wr_scalar(void *p, size_t bytes, int nt) {
    size_t n = bytes / 8;
#ifdef SB_X64
    if (nt) {
        long long *q = (long long *)p;
        for (size_t i = 0; i < n; i += 4) {
            _mm_stream_si64(q + i, (long long)i); _mm_stream_si64(q + i + 1, (long long)i);
            _mm_stream_si64(q + i + 2, (long long)i); _mm_stream_si64(q + i + 3, (long long)i);
        }
        _mm_sfence();
        return;
    }
#endif
    volatile uint64_t *q = (volatile uint64_t *)p;
#ifdef _MSC_VER
#pragma loop(no_vector)
#endif
    for (size_t i = 0; i < n; i += 4) { q[i] = i; q[i + 1] = i; q[i + 2] = i; q[i + 3] = i; }
}

SB_NOVEC static void cp_scalar(void *d, const void *s, size_t bytes, int nt) {
    size_t n = bytes / 8;
    const volatile uint64_t *a = (const volatile uint64_t *)s;
#ifdef SB_X64
    if (nt) {
        long long *q = (long long *)d;
        for (size_t i = 0; i < n; i += 4) {
            _mm_stream_si64(q + i, (long long)a[i]); _mm_stream_si64(q + i + 1, (long long)a[i + 1]);
            _mm_stream_si64(q + i + 2, (long long)a[i + 2]); _mm_stream_si64(q + i + 3, (long long)a[i + 3]);
        }
        _mm_sfence();
        return;
    }
#endif
    volatile uint64_t *q = (volatile uint64_t *)d;
#ifdef _MSC_VER
#pragma loop(no_vector)
#endif
    for (size_t i = 0; i < n; i += 4) { q[i] = a[i]; q[i + 1] = a[i + 1]; q[i + 2] = a[i + 2]; q[i + 3] = a[i + 3]; }
}

#ifdef SB_X64
/* ---- SSE2 ------------------------------------------------------------------ */
static uint64_t rd_sse2(const void *p, size_t bytes) {
    const __m128i *q = (const __m128i *)p;
    __m128i a = _mm_setzero_si128(), b = a, c = a, d = a;
    for (size_t i = 0, n = bytes / 16; i < n; i += 4) {
        a = _mm_xor_si128(a, _mm_load_si128(q + i)); b = _mm_xor_si128(b, _mm_load_si128(q + i + 1));
        c = _mm_xor_si128(c, _mm_load_si128(q + i + 2)); d = _mm_xor_si128(d, _mm_load_si128(q + i + 3));
    }
    a = _mm_xor_si128(_mm_xor_si128(a, b), _mm_xor_si128(c, d));
    return (uint64_t)_mm_cvtsi128_si64(a) ^ (uint64_t)_mm_cvtsi128_si64(_mm_unpackhi_epi64(a, a));
}
static void wr_sse2(void *p, size_t bytes, int nt) {
    __m128i *q = (__m128i *)p, v = _mm_set1_epi32(0x5be7c0de);
    size_t n = bytes / 16;
    if (nt) { for (size_t i = 0; i < n; i += 4) { _mm_stream_si128(q + i, v); _mm_stream_si128(q + i + 1, v); _mm_stream_si128(q + i + 2, v); _mm_stream_si128(q + i + 3, v); } _mm_sfence(); }
    else for (size_t i = 0; i < n; i += 4) { _mm_store_si128(q + i, v); _mm_store_si128(q + i + 1, v); _mm_store_si128(q + i + 2, v); _mm_store_si128(q + i + 3, v); }
}
static void cp_sse2(void *d, const void *s, size_t bytes, int nt) {
    __m128i *q = (__m128i *)d;
    const __m128i *a = (const __m128i *)s;
    size_t n = bytes / 16;
    if (nt) {
        for (size_t i = 0; i < n; i += 4) {
            __m128i x0 = _mm_load_si128(a + i), x1 = _mm_load_si128(a + i + 1), x2 = _mm_load_si128(a + i + 2), x3 = _mm_load_si128(a + i + 3);
            _mm_stream_si128(q + i, x0); _mm_stream_si128(q + i + 1, x1); _mm_stream_si128(q + i + 2, x2); _mm_stream_si128(q + i + 3, x3);
        }
        _mm_sfence();
    } else {
        for (size_t i = 0; i < n; i += 4) {
            __m128i x0 = _mm_load_si128(a + i), x1 = _mm_load_si128(a + i + 1), x2 = _mm_load_si128(a + i + 2), x3 = _mm_load_si128(a + i + 3);
            _mm_store_si128(q + i, x0); _mm_store_si128(q + i + 1, x1); _mm_store_si128(q + i + 2, x2); _mm_store_si128(q + i + 3, x3);
        }
    }
}

/* ---- AVX2 ------------------------------------------------------------------ */
SB_TGT("avx2") static uint64_t rd_avx2(const void *p, size_t bytes) {
    const __m256i *q = (const __m256i *)p;
    __m256i a = _mm256_setzero_si256(), b = a, c = a, d = a;
    for (size_t i = 0, n = bytes / 32; i < n; i += 4) {
        a = _mm256_xor_si256(a, _mm256_load_si256(q + i)); b = _mm256_xor_si256(b, _mm256_load_si256(q + i + 1));
        c = _mm256_xor_si256(c, _mm256_load_si256(q + i + 2)); d = _mm256_xor_si256(d, _mm256_load_si256(q + i + 3));
    }
    a = _mm256_xor_si256(_mm256_xor_si256(a, b), _mm256_xor_si256(c, d));
    __m128i x = _mm_xor_si128(_mm256_castsi256_si128(a), _mm256_extracti128_si256(a, 1));
    uint64_t r = (uint64_t)_mm_cvtsi128_si64(x) ^ (uint64_t)_mm_cvtsi128_si64(_mm_unpackhi_epi64(x, x));
    _mm256_zeroupper();
    return r;
}
SB_TGT("avx2") static void wr_avx2(void *p, size_t bytes, int nt) {
    __m256i *q = (__m256i *)p, v = _mm256_set1_epi32(0x5be7c0de);
    size_t n = bytes / 32;
    if (nt) { for (size_t i = 0; i < n; i += 4) { _mm256_stream_si256(q + i, v); _mm256_stream_si256(q + i + 1, v); _mm256_stream_si256(q + i + 2, v); _mm256_stream_si256(q + i + 3, v); } _mm_sfence(); }
    else for (size_t i = 0; i < n; i += 4) { _mm256_store_si256(q + i, v); _mm256_store_si256(q + i + 1, v); _mm256_store_si256(q + i + 2, v); _mm256_store_si256(q + i + 3, v); }
    _mm256_zeroupper();
}
SB_TGT("avx2") static void cp_avx2(void *d, const void *s, size_t bytes, int nt) {
    __m256i *q = (__m256i *)d;
    const __m256i *a = (const __m256i *)s;
    size_t n = bytes / 32;
    if (nt) {
        for (size_t i = 0; i < n; i += 4) {
            __m256i x0 = _mm256_load_si256(a + i), x1 = _mm256_load_si256(a + i + 1), x2 = _mm256_load_si256(a + i + 2), x3 = _mm256_load_si256(a + i + 3);
            _mm256_stream_si256(q + i, x0); _mm256_stream_si256(q + i + 1, x1); _mm256_stream_si256(q + i + 2, x2); _mm256_stream_si256(q + i + 3, x3);
        }
        _mm_sfence();
    } else {
        for (size_t i = 0; i < n; i += 4) {
            __m256i x0 = _mm256_load_si256(a + i), x1 = _mm256_load_si256(a + i + 1), x2 = _mm256_load_si256(a + i + 2), x3 = _mm256_load_si256(a + i + 3);
            _mm256_store_si256(q + i, x0); _mm256_store_si256(q + i + 1, x1); _mm256_store_si256(q + i + 2, x2); _mm256_store_si256(q + i + 3, x3);
        }
    }
    _mm256_zeroupper();
}

/* ---- AVX-512 --------------------------------------------------------------- */
SB_TGT("avx512f") static uint64_t rd_avx512(const void *p, size_t bytes) {
    const __m512i *q = (const __m512i *)p;
    __m512i a = _mm512_setzero_si512(), b = a, c = a, d = a;
    for (size_t i = 0, n = bytes / 64; i < n; i += 4) {
        a = _mm512_xor_si512(a, _mm512_load_si512(q + i)); b = _mm512_xor_si512(b, _mm512_load_si512(q + i + 1));
        c = _mm512_xor_si512(c, _mm512_load_si512(q + i + 2)); d = _mm512_xor_si512(d, _mm512_load_si512(q + i + 3));
    }
    a = _mm512_xor_si512(_mm512_xor_si512(a, b), _mm512_xor_si512(c, d));
    uint64_t r = (uint64_t)_mm512_reduce_or_epi64(a);
    _mm256_zeroupper();
    return r;
}
SB_TGT("avx512f") static void wr_avx512(void *p, size_t bytes, int nt) {
    __m512i *q = (__m512i *)p, v = _mm512_set1_epi32(0x5be7c0de);
    size_t n = bytes / 64;
    if (nt) { for (size_t i = 0; i < n; i += 4) { _mm512_stream_si512(q + i, v); _mm512_stream_si512(q + i + 1, v); _mm512_stream_si512(q + i + 2, v); _mm512_stream_si512(q + i + 3, v); } _mm_sfence(); }
    else for (size_t i = 0; i < n; i += 4) { _mm512_store_si512(q + i, v); _mm512_store_si512(q + i + 1, v); _mm512_store_si512(q + i + 2, v); _mm512_store_si512(q + i + 3, v); }
    _mm256_zeroupper();
}
SB_TGT("avx512f") static void cp_avx512(void *d, const void *s, size_t bytes, int nt) {
    __m512i *q = (__m512i *)d;
    const __m512i *a = (const __m512i *)s;
    size_t n = bytes / 64;
    if (nt) {
        for (size_t i = 0; i < n; i += 4) {
            __m512i x0 = _mm512_load_si512(a + i), x1 = _mm512_load_si512(a + i + 1), x2 = _mm512_load_si512(a + i + 2), x3 = _mm512_load_si512(a + i + 3);
            _mm512_stream_si512(q + i, x0); _mm512_stream_si512(q + i + 1, x1); _mm512_stream_si512(q + i + 2, x2); _mm512_stream_si512(q + i + 3, x3);
        }
        _mm_sfence();
    } else {
        for (size_t i = 0; i < n; i += 4) {
            __m512i x0 = _mm512_load_si512(a + i), x1 = _mm512_load_si512(a + i + 1), x2 = _mm512_load_si512(a + i + 2), x3 = _mm512_load_si512(a + i + 3);
            _mm512_store_si512(q + i, x0); _mm512_store_si512(q + i + 1, x1); _mm512_store_si512(q + i + 2, x2); _mm512_store_si512(q + i + 3, x3);
        }
    }
    _mm256_zeroupper();
}
#endif

#ifdef SB_ARM64
/* ---- NEON (Non-temporal nicht unterstützt: normale Stores) ------------------- */
static uint64_t rd_neon(const void *p, size_t bytes) {
    const uint64_t *q = (const uint64_t *)p;
    uint64x2_t a = vdupq_n_u64(0), b = a, c = a, d = a;
    for (size_t i = 0, n = bytes / 8; i < n; i += 8) {
        a = veorq_u64(a, vld1q_u64(q + i)); b = veorq_u64(b, vld1q_u64(q + i + 2));
        c = veorq_u64(c, vld1q_u64(q + i + 4)); d = veorq_u64(d, vld1q_u64(q + i + 6));
    }
    a = veorq_u64(veorq_u64(a, b), veorq_u64(c, d));
    return vgetq_lane_u64(a, 0) ^ vgetq_lane_u64(a, 1);
}
static void wr_neon(void *p, size_t bytes, int nt) {
    uint64_t *q = (uint64_t *)p;
    uint64x2_t v = vdupq_n_u64(0x5be7c0de5be7c0deULL);
    (void)nt;
    for (size_t i = 0, n = bytes / 8; i < n; i += 8) { vst1q_u64(q + i, v); vst1q_u64(q + i + 2, v); vst1q_u64(q + i + 4, v); vst1q_u64(q + i + 6, v); }
}
static void cp_neon(void *d, const void *s, size_t bytes, int nt) {
    uint64_t *q = (uint64_t *)d;
    const uint64_t *a = (const uint64_t *)s;
    (void)nt;
    for (size_t i = 0, n = bytes / 8; i < n; i += 8) {
        uint64x2_t x0 = vld1q_u64(a + i), x1 = vld1q_u64(a + i + 2), x2 = vld1q_u64(a + i + 4), x3 = vld1q_u64(a + i + 6);
        vst1q_u64(q + i, x0); vst1q_u64(q + i + 2, x1); vst1q_u64(q + i + 4, x2); vst1q_u64(q + i + 6, x3);
    }
}
#endif

int sb_memkern_get(sb_simd isa, sb_memkern *k) {
    if (isa == SB_SIMD_AUTO) isa = sb_simd_auto();
    if (!sb_simd_supported(isa)) return 1;
    switch (isa) {
    case SB_SIMD_SCALAR: k->read = rd_scalar; k->write = wr_scalar; k->copy = cp_scalar; return 0;
#ifdef SB_X64
    case SB_SIMD_SSE2: k->read = rd_sse2; k->write = wr_sse2; k->copy = cp_sse2; return 0;
    case SB_SIMD_AVX2: k->read = rd_avx2; k->write = wr_avx2; k->copy = cp_avx2; return 0;
    case SB_SIMD_AVX512: k->read = rd_avx512; k->write = wr_avx512; k->copy = cp_avx512; return 0;
#endif
#ifdef SB_ARM64
    case SB_SIMD_NEON: k->read = rd_neon; k->write = wr_neon; k->copy = cp_neon; return 0;
#endif
    default: return 1;
    }
}

/* ---- Pointer-Chasing --------------------------------------------------------- */

/* Zufälliger Zyklus über alle Zeilen (Sattolo): jede Zeile wird genau einmal besucht,
 * Hardware-Prefetcher können die Folge nicht vorhersagen. */
void sb_chase_build(void *buf, size_t bytes, size_t line, uint64_t seed) {
    size_t n = bytes / line;
    char *b = (char *)buf;
    if (n < 2) { *(void **)b = b; return; }
    sb_rng r;
    sb_rng_seed(&r, seed);
    /* Permutation in den Zeilen selbst ablegen (Index im zweiten Wort), dann verketten */
    for (size_t i = 0; i < n; i++) ((uint64_t *)(b + i * line))[1] = i;
    for (size_t i = n - 1; i > 0; i--) {
        size_t j = (size_t)sb_rng_below(&r, i);          /* j < i: Sattolo -> ein Zyklus */
        uint64_t *x = (uint64_t *)(b + i * line) + 1, *y = (uint64_t *)(b + j * line) + 1;
        uint64_t t = *x; *x = *y; *y = t;
    }
    /* perm[k] = Index an Position k; Kette perm[k] -> perm[k+1] */
    /* Wort 0 = Zeiger, Wort 1 = Permutation; das Verketten überschreibt nur Wort 0 */
    size_t first = (size_t)((uint64_t *)b)[1], prev = first;
    for (size_t k = 0; k + 1 < n; k++) {
        size_t a = (size_t)((uint64_t *)(b + k * line))[1];
        size_t c = (size_t)((uint64_t *)(b + (k + 1) * line))[1];
        *(void **)(b + a * line) = b + c * line;
        prev = c;
    }
    *(void **)(b + prev * line) = b + first * line;
}

uint64_t sb_chase(void **pos, uint64_t n) {
    void *p = *pos;
    uint64_t i = 0;
    for (; i + 16 <= n; i += 16) {
        p = *(void **)p; p = *(void **)p; p = *(void **)p; p = *(void **)p;
        p = *(void **)p; p = *(void **)p; p = *(void **)p; p = *(void **)p;
        p = *(void **)p; p = *(void **)p; p = *(void **)p; p = *(void **)p;
        p = *(void **)p; p = *(void **)p; p = *(void **)p; p = *(void **)p;
    }
    for (; i < n; i++) p = *(void **)p;
    *(void *volatile *)pos = p;
    return i;
}

/* ---- CPU-Lasten -------------------------------------------------------------- */

const char *sb_cpu_workload_name(sb_cpu_workload w) {
    static const char *const N[] = {"int", "float", "crypto", "mixed"};
    return (unsigned)w < 4 ? N[w] : "?";
}

static uint64_t rotl(uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }

/* int: 1 Operation = 1 Runde aus Addition, Multiplikation, Rotation, XOR, Shift und
 * einem Tabellenzugriff (L1) auf 4 abhängigen Ketten (~24 Integer-Befehle) */
static void k_int(uint64_t s[8], uint64_t n) {
    uint64_t a = s[0], b = s[1], c = s[2], d = s[3];
    static const uint64_t T[16] = {
        0x243f6a8885a308d3ULL, 0x13198a2e03707344ULL, 0xa4093822299f31d0ULL, 0x082efa98ec4e6c89ULL,
        0x452821e638d01377ULL, 0xbe5466cf34e90c6cULL, 0xc0ac29b7c97c50ddULL, 0x3f84d5b5b5470917ULL,
        0x9216d5d98979fb1bULL, 0xd1310ba698dfb5acULL, 0x2ffd72dbd01adfb7ULL, 0xb8e1afed6a267e96ULL,
        0xba7c9045f12c7f99ULL, 0x24a19947b3916cf7ULL, 0x0801f2e2858efc16ULL, 0x636920d871574e69ULL};
    for (uint64_t i = 0; i < n; i++) {
        a += b ^ T[c & 15];
        b = rotl(b, 13) * 0x9E3779B97F4A7C15ULL;
        c ^= a >> 7;
        d = d * 6364136223846793005ULL + c;
        a ^= rotl(d, 29);
        c += b >> 11;
    }
    s[0] = a; s[1] = b; s[2] = c; s[3] = d;
}

/* float: 1 Operation = 4 unabhängige Ketten aus Multiplikation/Addition plus eine
 * Division (double, 9 Gleitkommabefehle); Werte bleiben im normalisierten Bereich */
static void k_float(uint64_t s[8], uint64_t n) {
    double x, y, z, w;
    memcpy(&x, &s[4], 8); memcpy(&y, &s[5], 8); memcpy(&z, &s[6], 8); memcpy(&w, &s[7], 8);
    if (!(x > 0.5 && x < 2.0)) x = 1.1;
    if (!(y > 0.5 && y < 2.0)) y = 1.2;
    if (!(z > 0.5 && z < 2.0)) z = 1.3;
    if (!(w > 0.5 && w < 2.0)) w = 1.4;
    for (uint64_t i = 0; i < n; i++) {
        x = x * 0.9999999 + 0.0000001;
        y = y * 1.0000001 - 0.0000001;
        z = z * 0.99999995 + 0.00000005;
        w = (w + 1.0) / (w + 0.9999999) + 0.0000001 * x;
        if (w > 1.9) w = 1.1;
    }
    memcpy(&s[4], &x, 8); memcpy(&s[5], &y, 8); memcpy(&s[6], &z, 8); memcpy(&s[7], &w, 8);
}

/* crypto: 1 Operation = SHA-256 über einen 64-Byte-Block (portable Implementierung,
 * keine SHA-Befehlserweiterung) */
static void k_crypto(uint64_t s[8], uint64_t n) {
    sb_sha256 c;
    sb_sha256_init(&c);
    uint8_t blk[64];
    memcpy(blk, s, 64);
    for (uint64_t i = 0; i < n; i++) {
        blk[i & 63] ^= (uint8_t)i;
        sb_sha256_update(&c, blk, 64);
    }
    uint8_t out[32];
    sb_sha256_final(&c, out);
    for (int i = 0; i < 4; i++) { uint64_t v; memcpy(&v, out + i * 8, 8); s[i] ^= v; }
}

void sb_cpu_kernel(sb_cpu_workload w, uint64_t s[8], uint64_t n) {
    switch (w) {
    case SB_CPU_INT: k_int(s, n); break;
    case SB_CPU_FLOAT: k_float(s, n); break;
    case SB_CPU_CRYPTO: k_crypto(s, n); break;
    case SB_CPU_MIXED:
        /* 1 Operation = 128 int + 64 float + 1 crypto-Aufruf (SHA-256 einer 64-Byte-Nachricht,
         * mit Padding 2 Kompressionen); auf dem Referenzsystem je etwa ein Drittel der Zeit */
        for (uint64_t i = 0; i < n; i++) { k_int(s, 128); k_float(s, 64); k_crypto(s, 1); }
        break;
    }
}
