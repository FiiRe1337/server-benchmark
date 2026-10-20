#ifndef SB_SHA256_H
#define SB_SHA256_H
#include <stddef.h>
#include <stdint.h>
typedef struct sb_sha256 { uint32_t h[8]; uint64_t len; uint8_t buf[64]; size_t n; } sb_sha256;
void sb_sha256_init(sb_sha256 *c);
void sb_sha256_update(sb_sha256 *c, const void *data, size_t len);
void sb_sha256_final(sb_sha256 *c, uint8_t out[32]);
/* hex: 65 Bytes inkl. NUL */
void sb_sha256_hex(const void *data, size_t len, char hex[65]);
void sb_hex32(const uint8_t d[32], char hex[65]);
#endif
