#include "twr_math.h"

#define TS_MASK_40 0xFFFFFFFFFFULL

// Velocidad de la luz en el aire por unidad de tiempo del DW3000, en mm.
static const double MM_PER_DTU = 299702547.0 / (499.2e6 * 128.0) * 1000.0;

uint64_t twr_ts_from_bytes(const uint8_t bytes[5])
{
    uint64_t ts = 0;
    for (int i = 4; i >= 0; i--) {
        ts = (ts << 8) | bytes[i];
    }
    return ts;
}

uint32_t twr_delayed_tx_time(uint64_t rx_ts, uint32_t delay_us)
{
    uint64_t target = (rx_ts + (uint64_t)delay_us * TWR_DTU_PER_US) & TS_MASK_40;
    return (uint32_t)(target >> 8);
}

uint64_t twr_delayed_tx_ts(uint32_t delayed_time, uint16_t tx_antenna_delay)
{
    uint64_t ts = ((uint64_t)(delayed_time & 0xFFFFFFFEUL)) << 8;
    return (ts + tx_antenna_delay) & TS_MASK_40;
}

double twr_ds_tof_dtu(uint32_t poll_tx, uint32_t resp_rx, uint32_t final_tx,
                      uint32_t poll_rx, uint32_t resp_tx, uint32_t final_rx)
{
    // Ra, Da: ida y vuelta y retardo de respuesta vistos por el tag.
    // Rb, Db: los mismos vistos por el ancla.
    uint32_t ra = resp_rx - poll_tx;
    uint32_t da = final_tx - resp_rx;
    uint32_t rb = final_rx - resp_tx;
    uint32_t db = resp_tx - poll_rx;

    // Los productos caben en 64 bits con signo mientras cada intervalo dure
    // menos de 47 ms; con enteros la resta es exacta.
    int64_t num = (int64_t)ra * (int64_t)rb - (int64_t)da * (int64_t)db;
    int64_t den = (int64_t)ra + (int64_t)rb + (int64_t)da + (int64_t)db;
    if (den == 0) {
        return 0.0;
    }
    return (double)num / (double)den;
}

int32_t twr_tof_to_mm(double tof_dtu)
{
    double mm = tof_dtu * MM_PER_DTU;
    return (int32_t)(mm >= 0.0 ? mm + 0.5 : mm - 0.5);
}

void twr_put_u32(uint8_t *dst, uint32_t value)
{
    dst[0] = (uint8_t)value;
    dst[1] = (uint8_t)(value >> 8);
    dst[2] = (uint8_t)(value >> 16);
    dst[3] = (uint8_t)(value >> 24);
}

uint32_t twr_get_u32(const uint8_t *src)
{
    return (uint32_t)src[0] | ((uint32_t)src[1] << 8) | ((uint32_t)src[2] << 16) |
           ((uint32_t)src[3] << 24);
}
