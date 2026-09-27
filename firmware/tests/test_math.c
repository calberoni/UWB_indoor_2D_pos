// Pruebas de la aritmética del ranging y del empaquetado del registro.
// Se ejecutan en el Mac con `make test`.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "record.h"
#include "twr_math.h"

static int failures;

#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                     \
            printf("FALLO %s:%d: %s\n", __FILE__, __LINE__, #condition);        \
            failures++;                                                         \
        }                                                                       \
    } while (0)

static const double DTU_PER_MM = (499.2e6 * 128.0) / 299702547.0 / 1000.0;

// Timestamp que marcaría un reloj con desfase y deriva, en sus 32 bits bajos.
static uint32_t clock_read(double true_dtu, double offset_dtu, double drift_ppm)
{
    double local = true_dtu * (1.0 + drift_ppm * 1e-6) + offset_dtu;
    return (uint32_t)(uint64_t)fmod(local, 4294967296.0);
}

// Simula un intercambio completo y devuelve la distancia calculada en mm.
static int32_t simulate(double distance_mm, double reply_us, double tag_offset, double tag_ppm,
                        double anchor_offset, double anchor_ppm)
{
    double tof = distance_mm * DTU_PER_MM;
    double reply = reply_us * (double)TWR_DTU_PER_US;

    double t_poll_tx = 1000.0;
    double t_poll_rx = t_poll_tx + tof;
    double t_resp_tx = t_poll_rx + reply;
    double t_resp_rx = t_resp_tx + tof;
    double t_final_tx = t_resp_rx + reply;
    double t_final_rx = t_final_tx + tof;

    double tof_dtu = twr_ds_tof_dtu(clock_read(t_poll_tx, tag_offset, tag_ppm),
                                    clock_read(t_resp_rx, tag_offset, tag_ppm),
                                    clock_read(t_final_tx, tag_offset, tag_ppm),
                                    clock_read(t_poll_rx, anchor_offset, anchor_ppm),
                                    clock_read(t_resp_tx, anchor_offset, anchor_ppm),
                                    clock_read(t_final_rx, anchor_offset, anchor_ppm));
    return twr_tof_to_mm(tof_dtu);
}

static void test_timestamps(void)
{
    const uint8_t bytes[5] = {0x01, 0x02, 0x03, 0x04, 0x05};
    CHECK(twr_ts_from_bytes(bytes) == 0x0504030201ULL);

    // 1000 us después de 0x100, en los 32 bits altos.
    CHECK(twr_delayed_tx_time(0x100ULL, 1000) == (uint32_t)((0x100ULL + 63898000ULL) >> 8));

    // El instante programado da la vuelta al contador de 40 bits.
    uint64_t near_wrap = 0xFFFFFFFFFFULL - 1000ULL;
    uint64_t expected = (near_wrap + 3000ULL * TWR_DTU_PER_US) & 0xFFFFFFFFFFULL;
    CHECK(twr_delayed_tx_time(near_wrap, 3000) == (uint32_t)(expected >> 8));

    // El chip ignora el bit bajo del registro y suma el antenna delay.
    CHECK(twr_delayed_tx_ts(0x00000003UL, 16385) == ((0x2ULL << 8) + 16385ULL));
    CHECK(twr_delayed_tx_ts(0xFFFFFFFFUL, 16385) ==
          (((0xFFFFFFFEULL << 8) + 16385ULL) & 0xFFFFFFFFFFULL));
}

static void test_distance(void)
{
    // Relojes ideales.
    CHECK(abs(simulate(2000.0, 3000.0, 0.0, 0.0, 0.0, 0.0) - 2000) <= 5);
    CHECK(abs(simulate(100.0, 3000.0, 0.0, 0.0, 0.0, 0.0) - 100) <= 5);
    CHECK(abs(simulate(30000.0, 3000.0, 0.0, 0.0, 0.0, 0.0) - 30000) <= 5);

    // Relojes con origen distinto.
    CHECK(abs(simulate(4000.0, 3000.0, 123456789.0, 0.0, 3987654321.0, 0.0) - 4000) <= 5);

    // El contador de 32 bits da la vuelta a mitad del intercambio.
    double just_before_wrap = 4294967296.0 - 2.0 * 3000.0 * (double)TWR_DTU_PER_US;
    CHECK(abs(simulate(4000.0, 3000.0, just_before_wrap, 0.0, 17.0, 0.0) - 4000) <= 5);
    CHECK(abs(simulate(4000.0, 3000.0, 17.0, 0.0, just_before_wrap, 0.0) - 4000) <= 5);

    // Cristales con 20 ppm de diferencia: el DS-TWR asimétrico lo compensa.
    CHECK(abs(simulate(4000.0, 3000.0, 1e6, 10.0, 2e9, -10.0) - 4000) <= 10);
    CHECK(abs(simulate(4000.0, 3000.0, 1e6, -20.0, 2e9, 20.0) - 4000) <= 10);

    CHECK(twr_tof_to_mm(0.0) == 0);
    CHECK(twr_tof_to_mm(2000.0 * DTU_PER_MM) == 2000);
    CHECK(twr_tof_to_mm(-100.0 * DTU_PER_MM) == -100);
}

static void test_bytes(void)
{
    uint8_t buffer[4];
    twr_put_u32(buffer, 0xA1B2C3D4UL);
    CHECK(buffer[0] == 0xD4 && buffer[1] == 0xC3 && buffer[2] == 0xB2 && buffer[3] == 0xA1);
    CHECK(twr_get_u32(buffer) == 0xA1B2C3D4UL);
}

static void test_record(void)
{
    // Equivale a struct.pack("<HHHHBBBI", 1234, 2940, 0xFFFF, 65534, 210, 0, 198, 123456):
    // el fallo (-1) va como 0xFFFF y 70000 mm se recorta a 65534.
    const Record record = {1234, {2940, -1, 70000}, {210, 0, 198}, 123456};
    const uint8_t expected[RECORD_LEN] = {0xD2, 0x04, 0x7C, 0x0B, 0xFF, 0xFF, 0xFE, 0xFF,
                                          0xD2, 0x00, 0xC6, 0x40, 0xE2, 0x01, 0x00};
    uint8_t packed[RECORD_LEN];
    record_pack(&record, packed);
    CHECK(memcmp(packed, expected, RECORD_LEN) == 0);
    CHECK(RECORD_LEN <= 20);
}

int main(void)
{
    test_timestamps();
    test_distance();
    test_bytes();
    test_record();
    if (failures > 0) {
        printf("%d comprobaciones fallidas\n", failures);
        return 1;
    }
    printf("firmware: aritmética y registro OK\n");
    return 0;
}
