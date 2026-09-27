// Aritmética de timestamps y fórmula DS-TWR. C puro, sin dependencias del
// hardware, para poder probarla en el Mac (firmware/tests).
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Unidades de tiempo del DW3000 (1 / (499.2 MHz * 128) = 15.65 ps) por microsegundo.
#define TWR_DTU_PER_US 63898ULL

// Timestamp de 40 bits a partir de los 5 bytes que entrega el driver (little-endian).
uint64_t twr_ts_from_bytes(const uint8_t bytes[5]);

// Valor para el registro de transmisión programada: los 32 bits altos del
// instante rx_ts + delay_us.
uint32_t twr_delayed_tx_time(uint64_t rx_ts, uint32_t delay_us);

// Timestamp con el que saldrá una transmisión programada. El chip ignora el bit
// bajo del registro y suma el antenna delay de TX.
uint64_t twr_delayed_tx_ts(uint32_t delayed_time, uint16_t tx_antenna_delay);

// Tiempo de vuelo por DS-TWR asimétrico, en unidades de tiempo del DW3000.
// Los seis timestamps son los 32 bits bajos; los intervalos se calculan módulo
// 2^32, así que el desbordamiento del contador no afecta mientras cada
// intervalo dure menos de 47 ms.
double twr_ds_tof_dtu(uint32_t poll_tx, uint32_t resp_rx, uint32_t final_tx,
                      uint32_t poll_rx, uint32_t resp_tx, uint32_t final_rx);

// Distancia en milímetros, redondeada. Puede ser negativa antes de calibrar.
int32_t twr_tof_to_mm(double tof_dtu);

// Entero de 32 bits hacia y desde una trama, en little-endian.
void twr_put_u32(uint8_t *dst, uint32_t value);
uint32_t twr_get_u32(const uint8_t *src);

#ifdef __cplusplus
}
#endif
