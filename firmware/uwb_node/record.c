#include "record.h"

#include "twr_math.h"

static uint16_t encode_distance(int32_t distance_mm)
{
    if (distance_mm < 0) {
        return RECORD_DISTANCE_FAILED;
    }
    if (distance_mm > RECORD_DISTANCE_MAX_MM) {
        return RECORD_DISTANCE_MAX_MM;
    }
    return (uint16_t)distance_mm;
}

void record_pack(const Record *record, uint8_t out[RECORD_LEN])
{
    out[0] = (uint8_t)(record->seq & 0xFF);
    out[1] = (uint8_t)(record->seq >> 8);
    for (int i = 0; i < RECORD_ANCHORS; i++) {
        uint16_t d = encode_distance(record->distance_mm[i]);
        out[2 + 2 * i] = (uint8_t)(d & 0xFF);
        out[3 + 2 * i] = (uint8_t)(d >> 8);
    }
    for (int i = 0; i < RECORD_ANCHORS; i++) {
        out[8 + i] = record->quality[i];
    }
    twr_put_u32(&out[11], record->t_ms);
}
