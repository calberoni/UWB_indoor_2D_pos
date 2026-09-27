#include "record.h"

#include "twr_math.h"

void record_pack(const Record *record, uint8_t out[RECORD_LEN])
{
    out[0] = (uint8_t)(record->seq & 0xFF);
    out[1] = (uint8_t)(record->seq >> 8);
    twr_put_u32(&out[2], (uint32_t)record->d_a);
    twr_put_u32(&out[6], (uint32_t)record->d_b);
    out[10] = record->q_a;
    out[11] = record->q_b;
    twr_put_u32(&out[12], record->t_ms);
}
