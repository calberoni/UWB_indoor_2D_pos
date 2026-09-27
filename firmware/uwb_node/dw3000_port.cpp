#include "dw3000_port.h"

#include <Arduino.h>
#include <SPI.h>
#include <string.h>

#include <deca_device_api.h>
#include <deca_interface.h>

#include "config.h"

static SPISettings spi_settings(SPI_SLOW_HZ, MSBFIRST, SPI_MODE0);
static uint16_t antenna_delay = ANTENNA_DELAY;

// ---------------------------------------------------------------- SPI

// El SPI del core transfiere sobre el propio búfer, así que cabecera y datos se
// copian a uno local y se envían en una sola llamada mientras quepan.
static uint8_t spi_buffer[256];

static void spi_select()
{
    SPI.beginTransaction(spi_settings);
    digitalWrite(PIN_DW_CS, LOW);
}

static void spi_release()
{
    digitalWrite(PIN_DW_CS, HIGH);
    SPI.endTransaction();
}

static void spi_send(const uint8_t *data, uint16_t length)
{
    while (length > 0) {
        uint16_t chunk = length > sizeof(spi_buffer) ? sizeof(spi_buffer) : length;
        memcpy(spi_buffer, data, chunk);
        SPI.transfer(spi_buffer, chunk);
        data += chunk;
        length -= chunk;
    }
}

static int32_t spi_write(uint16_t header_length, const uint8_t *header, uint16_t body_length,
                         const uint8_t *body)
{
    spi_select();
    if ((uint32_t)header_length + body_length <= sizeof(spi_buffer)) {
        memcpy(spi_buffer, header, header_length);
        if (body_length > 0) {
            memcpy(spi_buffer + header_length, body, body_length);
        }
        SPI.transfer(spi_buffer, header_length + body_length);
    } else {
        spi_send(header, header_length);
        spi_send(body, body_length);
    }
    spi_release();
    return DWT_SUCCESS;
}

static int32_t spi_write_crc(uint16_t header_length, const uint8_t *header, uint16_t body_length,
                             const uint8_t *body, uint8_t crc8)
{
    spi_select();
    spi_send(header, header_length);
    spi_send(body, body_length);
    spi_send(&crc8, 1);
    spi_release();
    return DWT_SUCCESS;
}

static int32_t spi_read(uint16_t header_length, uint8_t *header, uint16_t read_length,
                        uint8_t *read_buffer)
{
    spi_select();
    if ((uint32_t)header_length + read_length <= sizeof(spi_buffer)) {
        memcpy(spi_buffer, header, header_length);
        memset(spi_buffer + header_length, 0, read_length);
        SPI.transfer(spi_buffer, header_length + read_length);
        memcpy(read_buffer, spi_buffer + header_length, read_length);
    } else {
        spi_send(header, header_length);
        memset(read_buffer, 0, read_length);
        SPI.transfer(read_buffer, read_length);
    }
    spi_release();
    return DWT_SUCCESS;
}

static void spi_set_slow()
{
    spi_settings = SPISettings(SPI_SLOW_HZ, MSBFIRST, SPI_MODE0);
}

static void spi_set_fast()
{
    spi_settings = SPISettings(SPI_FAST_HZ, MSBFIRST, SPI_MODE0);
}

// ---------------------------------------------------------------- Pines de control

static void wakeup_pulse()
{
    digitalWrite(PIN_DW_WAKEUP, HIGH);
    delayMicroseconds(500);
    digitalWrite(PIN_DW_WAKEUP, LOW);
    delay(1);
}

// RSTn es open-drain: se fuerza a nivel bajo y después se suelta. Nunca se
// pone a nivel alto desde el microcontrolador.
static void reset_chip()
{
    digitalWrite(PIN_DW_RST, LOW);
    pinMode(PIN_DW_RST, OUTPUT);
    delay(2);
    pinMode(PIN_DW_RST, INPUT);
    delay(3);
}

// ---------------------------------------------------------------- Funciones que pide el driver

static const struct dwt_spi_s spi_functions = {
    .readfromspi = spi_read,
    .writetospi = spi_write,
    .writetospiwithcrc = spi_write_crc,
    .setslowrate = spi_set_slow,
    .setfastrate = spi_set_fast,
};

extern "C" {
extern const struct dwt_driver_s dw3000_driver;
}

static const struct dwt_driver_s *driver_list[] = {&dw3000_driver};

static const struct dwt_probe_s probe_interface = {
    .dw = NULL,
    .spi = (void *)&spi_functions,
    .wakeup_device_with_io = wakeup_pulse,
    .driver_list = (struct dwt_driver_s **)driver_list,
    .dw_driver_num = 1,
};

extern "C" {

// No hay rutina de interrupción que acceda al chip, así que la sección crítica
// del driver no necesita proteger nada.
decaIrqStatus_t decamutexon(void)
{
    return 0;
}

void decamutexoff(decaIrqStatus_t state)
{
    (void)state;
}

void deca_sleep(unsigned int time_ms)
{
    delay(time_ms);
}

void deca_usleep(unsigned long time_us)
{
    delayMicroseconds(time_us);
}

} // extern "C"

// ---------------------------------------------------------------- Arranque

static dwt_config_t radio_config = {
    .chan = UWB_CHANNEL,
    .txPreambLength = DWT_PLEN_128,
    .rxPAC = DWT_PAC8,
    .txCode = UWB_PREAMBLE_CODE,
    .rxCode = UWB_PREAMBLE_CODE,
    .sfdType = DWT_SFD_IEEE_4Z,
    .dataRate = DWT_BR_6M8,
    .phrMode = DWT_PHRMODE_STD,
    .phrRate = DWT_PHRRATE_STD,
    // Preámbulo + 1 + longitud del SFD - tamaño del PAC, en símbolos.
    .sfdTO = (128 + 1 + 8 - 8),
    .stsMode = DWT_STS_MODE_OFF,
    .stsLength = DWT_STS_LEN_64,
    .pdoaMode = DWT_PDOA_M0,
};

static dwt_txconfig_t tx_config = {
    .PGdly = UWB_TX_PGDLY,
    .power = UWB_TX_POWER,
    .PGcount = 0,
};

void port_begin()
{
    pinMode(PIN_DW_CS, OUTPUT);
    digitalWrite(PIN_DW_CS, HIGH);
    pinMode(PIN_DW_WAKEUP, OUTPUT);
    digitalWrite(PIN_DW_WAKEUP, LOW);
    pinMode(PIN_DW_IRQ, INPUT_PULLDOWN);
    pinMode(PIN_DW_RST, INPUT);
    SPI.begin();
}

uint32_t port_read_dev_id()
{
    uint8_t header = 0x00; // lectura rápida del registro 0x00
    uint8_t id[4] = {0, 0, 0, 0};
    spi_read(1, &header, sizeof(id), id);
    return (uint32_t)id[0] | ((uint32_t)id[1] << 8) | ((uint32_t)id[2] << 16) |
           ((uint32_t)id[3] << 24);
}

PortStatus port_init_radio()
{
    spi_set_slow();
    reset_chip();

    if (dwt_probe((struct dwt_probe_s *)&probe_interface) != DWT_SUCCESS) {
        return PORT_ERR_DEV_ID;
    }

    uint32_t start = millis();
    while (!dwt_checkidlerc()) {
        if (millis() - start > 100) {
            return PORT_ERR_NOT_READY;
        }
    }

    if (dwt_initialise(DWT_DW_INIT) != DWT_SUCCESS) {
        return PORT_ERR_INIT;
    }
    if (dwt_configure(&radio_config) != DWT_SUCCESS) {
        return PORT_ERR_CONFIG;
    }
    spi_set_fast();

    dwt_configuretxrf(&tx_config);
    port_set_antenna_delay(antenna_delay);
    dwt_setpanid(PAN_ID);
    dwt_setaddress16(MY_ADDR);
    dwt_configciadiag(DW_CIA_DIAG_LOG_ALL);
    return PORT_OK;
}

const char *port_status_text(PortStatus status)
{
    switch (status) {
    case PORT_OK:
        return "OK";
    case PORT_ERR_DEV_ID:
        return "DEV_ID no reconocido";
    case PORT_ERR_NOT_READY:
        return "el chip no sale del reset";
    case PORT_ERR_INIT:
        return "fallo en dwt_initialise";
    case PORT_ERR_CONFIG:
        return "fallo en dwt_configure (PLL)";
    }
    return "?";
}

void port_set_antenna_delay(uint16_t delay)
{
    antenna_delay = delay;
    dwt_setrxantennadelay(delay);
    dwt_settxantennadelay(delay);
}

uint16_t port_antenna_delay()
{
    return antenna_delay;
}
