// Registro de un ciclo de ranging, tal como viaja por BLE.
// El formato está en docs/contrato-datos.md. C puro, para probarlo en el Mac.
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define RECORD_LEN 16

typedef struct {
    uint16_t seq;
    int32_t d_a; // mm, -1 si el ranging falló
    int32_t d_b;
    uint8_t q_a;
    uint8_t q_b;
    uint32_t t_ms;
} Record;

// Empaqueta el registro en little-endian.
void record_pack(const Record *record, uint8_t out[RECORD_LEN]);

#ifdef __cplusplus
}
#endif
