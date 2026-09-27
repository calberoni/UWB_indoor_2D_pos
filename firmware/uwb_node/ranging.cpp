#include "ranging.h"

#include <Arduino.h>
#include <string.h>

#include <deca_device_api.h>

#include "config.h"
#include "dw3000_port.h"
#include "twr_math.h"

// ---------------------------------------------------------------- Tramas

// Trama de datos IEEE 802.15.4 con direcciones cortas y PAN comprimido:
//   0-1  control de trama (0x8841)
//   2    número de secuencia
//   3-4  PAN
//   5-6  destino
//   7-8  origen
//   9    tipo de mensaje
//   10   identificador del intercambio
//   11-  datos
// El chip añade los 2 bytes de CRC.
#define FRAME_HEADER_LEN 11
#define FRAME_MAX_LEN    32

#define MSG_POLL     0x21
#define MSG_RESPONSE 0x10
#define MSG_FINAL    0x23
#define MSG_REPORT   0x2A

#define FINAL_PAYLOAD_LEN  12 // poll_tx, resp_rx, final_tx
#define REPORT_PAYLOAD_LEN 4  // distancia en mm

struct Frame {
    uint16_t dest;
    uint16_t src;
    uint8_t type;
    uint8_t exchange;
    uint8_t payload[FRAME_MAX_LEN - FRAME_HEADER_LEN];
    uint8_t payload_len;
};

enum RxOutcome { RX_FRAME, RX_TIMEOUT, RX_ERROR };

static RangingStats stats;
static uint8_t frame_seq;

const RangingStats &ranging_stats()
{
    return stats;
}

void ranging_reset_stats()
{
    memset(&stats, 0, sizeof(stats));
}

static void write_frame(uint16_t dest, uint8_t type, uint8_t exchange, const uint8_t *payload,
                        uint8_t payload_len)
{
    uint8_t buffer[FRAME_MAX_LEN];
    buffer[0] = 0x41;
    buffer[1] = 0x88;
    buffer[2] = frame_seq++;
    buffer[3] = (uint8_t)(PAN_ID & 0xFF);
    buffer[4] = (uint8_t)(PAN_ID >> 8);
    buffer[5] = (uint8_t)(dest & 0xFF);
    buffer[6] = (uint8_t)(dest >> 8);
    buffer[7] = (uint8_t)(MY_ADDR & 0xFF);
    buffer[8] = (uint8_t)(MY_ADDR >> 8);
    buffer[9] = type;
    buffer[10] = exchange;
    if (payload_len > 0) {
        memcpy(&buffer[FRAME_HEADER_LEN], payload, payload_len);
    }
    uint16_t length = FRAME_HEADER_LEN + payload_len;
    dwt_writetxdata(length, buffer, 0);
    dwt_writetxfctrl(length + FCS_LEN, 0, 1);
}

// Lee la trama recibida. Devuelve false si no es una trama de este protocolo
// dirigida a este nodo.
static bool read_frame(Frame *frame)
{
    uint8_t ranging_bit = 0;
    uint16_t length = dwt_getframelength(&ranging_bit);
    if (length < FRAME_HEADER_LEN + FCS_LEN || length > FRAME_MAX_LEN + FCS_LEN) {
        return false;
    }
    length -= FCS_LEN;

    uint8_t buffer[FRAME_MAX_LEN];
    dwt_readrxdata(buffer, length, 0);

    if (buffer[0] != 0x41 || buffer[1] != 0x88) {
        return false;
    }
    if (buffer[3] != (PAN_ID & 0xFF) || buffer[4] != (PAN_ID >> 8)) {
        return false;
    }
    frame->dest = (uint16_t)buffer[5] | ((uint16_t)buffer[6] << 8);
    frame->src = (uint16_t)buffer[7] | ((uint16_t)buffer[8] << 8);
    if (frame->dest != MY_ADDR) {
        return false;
    }
    frame->type = buffer[9];
    frame->exchange = buffer[10];
    frame->payload_len = (uint8_t)(length - FRAME_HEADER_LEN);
    memcpy(frame->payload, &buffer[FRAME_HEADER_LEN], frame->payload_len);
    return true;
}

// ---------------------------------------------------------------- Espera de eventos

// Espera por consulta del registro de estado: en mbed no se puede usar el SPI
// dentro de una interrupción.
static RxOutcome wait_rx()
{
    uint32_t start = micros();
    for (;;) {
        uint32_t status = dwt_readsysstatuslo();
        if (status & DWT_INT_RXFCG_BIT_MASK) {
            dwt_writesysstatuslo(DWT_INT_RXFCG_BIT_MASK);
            return RX_FRAME;
        }
        if (status & SYS_STATUS_ALL_RX_TO) {
            dwt_writesysstatuslo(SYS_STATUS_ALL_RX_TO);
            return RX_TIMEOUT;
        }
        if (status & SYS_STATUS_ALL_RX_ERR) {
            dwt_writesysstatuslo(SYS_STATUS_ALL_RX_ERR);
            return RX_ERROR;
        }
        if (micros() - start > WAIT_GUARD_US) {
            dwt_forcetrxoff();
            return RX_TIMEOUT;
        }
    }
}

static bool wait_tx_done()
{
    uint32_t start = micros();
    while (!(dwt_readsysstatuslo() & DWT_INT_TXFRS_BIT_MASK)) {
        if (micros() - start > WAIT_GUARD_US) {
            dwt_forcetrxoff();
            return false;
        }
    }
    dwt_writesysstatuslo(DWT_INT_TXFRS_BIT_MASK);
    return true;
}

static void count_failure(RxOutcome outcome)
{
    if (outcome == RX_TIMEOUT) {
        stats.timeout++;
    } else {
        stats.error++;
    }
}

static uint64_t read_rx_timestamp()
{
    uint8_t bytes[5];
    dwt_readrxtimestamp(bytes, DWT_COMPAT_NONE);
    return twr_ts_from_bytes(bytes);
}

static uint64_t read_tx_timestamp()
{
    uint8_t bytes[5];
    dwt_readtxtimestamp(bytes);
    return twr_ts_from_bytes(bytes);
}

// ---------------------------------------------------------------- Calidad

static uint8_t read_quality()
{
    dwt_cirdiags_t diag;
    int16_t power_q8;
    if (dwt_readdiagnostics_acc(&diag, DWT_ACC_IDX_IP_M) != DWT_SUCCESS) {
        return 0;
    }
    if (dwt_calculate_first_path_power(&diag, DWT_ACC_IDX_IP_M, &power_q8) != DWT_SUCCESS) {
        return 0;
    }
    // Potencia de primer camino en dBm con 8 bits fraccionarios.
    int32_t span_q8 = (int32_t)(QUALITY_FP_MAX_DBM - QUALITY_FP_MIN_DBM) * 256;
    int32_t above_q8 = (int32_t)power_q8 - (int32_t)QUALITY_FP_MIN_DBM * 256;
    if (above_q8 <= 0) {
        return 0;
    }
    if (above_q8 >= span_q8) {
        return 255;
    }
    return (uint8_t)((above_q8 * 255) / span_q8);
}

// ---------------------------------------------------------------- Tag (initiator)

RangingResult ranging_initiate(uint16_t anchor_addr, RangingRaw *raw)
{
    static uint8_t exchange_counter;
    const RangingResult failed = {-1, 0};
    uint8_t exchange = ++exchange_counter;
    Frame frame;

    dwt_forcetrxoff();
    dwt_writesysstatuslo(SYS_STATUS_ALL_RX_GOOD | SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO |
                         DWT_INT_TXFRS_BIT_MASK);

    // POLL
    write_frame(anchor_addr, MSG_POLL, exchange, NULL, 0);
    dwt_setrxaftertxdelay(0);
    dwt_setrxtimeout(RX_AFTER_REPLY_TIMEOUT_UUS);
    if (dwt_starttx(DWT_START_TX_IMMEDIATE | DWT_RESPONSE_EXPECTED) != DWT_SUCCESS) {
        stats.error++;
        return failed;
    }

    // RESPONSE
    RxOutcome outcome = wait_rx();
    if (outcome != RX_FRAME) {
        count_failure(outcome);
        return failed;
    }
    if (!read_frame(&frame) || frame.src != anchor_addr || frame.type != MSG_RESPONSE ||
        frame.exchange != exchange) {
        stats.error++;
        return failed;
    }

    // FINAL, programado respecto a la llegada de RESPONSE
    uint64_t poll_tx = read_tx_timestamp();
    uint64_t resp_rx = read_rx_timestamp();
    uint32_t final_time = twr_delayed_tx_time(resp_rx, REPLY_DELAY_US);
    uint64_t final_tx = twr_delayed_tx_ts(final_time, port_antenna_delay());

    uint8_t payload[FINAL_PAYLOAD_LEN];
    twr_put_u32(&payload[0], (uint32_t)poll_tx);
    twr_put_u32(&payload[4], (uint32_t)resp_rx);
    twr_put_u32(&payload[8], (uint32_t)final_tx);
    write_frame(anchor_addr, MSG_FINAL, exchange, payload, sizeof(payload));
    dwt_setdelayedtrxtime(final_time);
    dwt_setrxtimeout(RX_REPORT_TIMEOUT_UUS);
    if (dwt_starttx(DWT_START_TX_DELAYED | DWT_RESPONSE_EXPECTED) != DWT_SUCCESS) {
        stats.late++;
        return failed;
    }

    // REPORT
    outcome = wait_rx();
    if (outcome != RX_FRAME) {
        count_failure(outcome);
        return failed;
    }
    if (!read_frame(&frame) || frame.src != anchor_addr || frame.type != MSG_REPORT ||
        frame.exchange != exchange || frame.payload_len < REPORT_PAYLOAD_LEN) {
        stats.error++;
        return failed;
    }

    RangingResult result;
    result.distance_mm = (int32_t)twr_get_u32(frame.payload);
    result.quality = read_quality();
    if (raw != NULL) {
        memset(raw, 0, sizeof(*raw));
        raw->poll_tx = (uint32_t)poll_tx;
        raw->resp_rx = (uint32_t)resp_rx;
        raw->final_tx = (uint32_t)final_tx;
        raw->distance_mm = result.distance_mm;
    }
    stats.ok++;
    return result;
}

// ---------------------------------------------------------------- Ancla (responder)

static bool receiver_on;

static void start_listening()
{
    dwt_setrxtimeout(0);
    dwt_rxenable(DWT_START_RX_IMMEDIATE);
    receiver_on = true;
}

static bool handle_poll(const Frame &poll, RangingRaw *raw)
{
    Frame frame;

    // RESPONSE, programado respecto a la llegada de POLL
    uint64_t poll_rx = read_rx_timestamp();
    uint32_t resp_time = twr_delayed_tx_time(poll_rx, REPLY_DELAY_US);
    write_frame(poll.src, MSG_RESPONSE, poll.exchange, NULL, 0);
    dwt_setdelayedtrxtime(resp_time);
    dwt_setrxaftertxdelay(0);
    dwt_setrxtimeout(RX_AFTER_REPLY_TIMEOUT_UUS);
    if (dwt_starttx(DWT_START_TX_DELAYED | DWT_RESPONSE_EXPECTED) != DWT_SUCCESS) {
        stats.late++;
        return false;
    }

    // FINAL
    RxOutcome outcome = wait_rx();
    if (outcome != RX_FRAME) {
        count_failure(outcome);
        return false;
    }
    if (!read_frame(&frame) || frame.src != poll.src || frame.type != MSG_FINAL ||
        frame.exchange != poll.exchange || frame.payload_len < FINAL_PAYLOAD_LEN) {
        stats.error++;
        return false;
    }

    uint32_t resp_tx = (uint32_t)read_tx_timestamp();
    uint32_t final_rx = (uint32_t)read_rx_timestamp();
    uint32_t poll_tx = twr_get_u32(&frame.payload[0]);
    uint32_t resp_rx = twr_get_u32(&frame.payload[4]);
    uint32_t final_tx = twr_get_u32(&frame.payload[8]);

    double tof = twr_ds_tof_dtu(poll_tx, resp_rx, final_tx, (uint32_t)poll_rx, resp_tx, final_rx);
    int32_t distance_mm = twr_tof_to_mm(tof);
    // -1 está reservado para "ranging fallido"; antes de calibrar, a muy corta
    // distancia el cálculo puede salir negativo.
    if (distance_mm < 0) {
        distance_mm = 0;
    }

    // REPORT
    uint8_t payload[REPORT_PAYLOAD_LEN];
    twr_put_u32(payload, (uint32_t)distance_mm);
    write_frame(poll.src, MSG_REPORT, poll.exchange, payload, sizeof(payload));
    if (dwt_starttx(DWT_START_TX_IMMEDIATE) != DWT_SUCCESS || !wait_tx_done()) {
        stats.error++;
        return false;
    }

    if (raw != NULL) {
        raw->poll_tx = poll_tx;
        raw->resp_rx = resp_rx;
        raw->final_tx = final_tx;
        raw->poll_rx = (uint32_t)poll_rx;
        raw->resp_tx = resp_tx;
        raw->final_rx = final_rx;
        raw->distance_mm = distance_mm;
    }
    stats.ok++;
    return true;
}

bool ranging_respond(RangingRaw *raw)
{
    if (!receiver_on) {
        start_listening();
        return false;
    }

    uint32_t status = dwt_readsysstatuslo();
    if (status & DWT_INT_RXFCG_BIT_MASK) {
        dwt_writesysstatuslo(DWT_INT_RXFCG_BIT_MASK);
        receiver_on = false;
        Frame frame;
        // Las tramas dirigidas a otro nodo se descartan sin contarlas como error.
        if (read_frame(&frame) && frame.type == MSG_POLL && frame.src == ADDR_TAG) {
            bool done = handle_poll(frame, raw);
            start_listening();
            return done;
        }
        start_listening();
    } else if (status & (SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO)) {
        dwt_writesysstatuslo(SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
        start_listening();
    }
    return false;
}
