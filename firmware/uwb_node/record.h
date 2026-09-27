// Registro de un ciclo de ranging con las tres anclas, tal como viaja por BLE.
// El formato está en docs/contrato-datos.md. C puro, para probarlo en el Mac.
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define RECORD_ANCHORS 3
// Cabe en una notificación BLE con el MTU mínimo (20 bytes de datos).
#define RECORD_LEN 15

// Valor binario de un ranging fallido; en texto se escribe -1.
#define RECORD_DISTANCE_FAILED 0xFFFF
#define RECORD_DISTANCE_MAX_MM 65534

typedef struct {
    uint16_t seq;
    int32_t distance_mm[RECORD_ANCHORS]; // -1 si el ranging falló; orden A, B, C
    uint8_t quality[RECORD_ANCHORS];
    uint32_t t_ms;
} Record;

// Empaqueta el registro en little-endian. Las distancias van como uint16; las
// mayores de 65534 mm se recortan a ese valor.
void record_pack(const Record *record, uint8_t out[RECORD_LEN]);

#ifdef __cplusplus
}
#endif
