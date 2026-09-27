// Nodo UWB: el mismo proyecto para las dos anclas y el tag. El rol se elige al
// compilar (ver config.h y el Makefile).

#include <Arduino.h>
#include <stdlib.h>
#include <string.h>

#include "config.h"
#include "console.h"
#include "dw3000_port.h"
#include "ranging.h"

#if ROLE == ROLE_TAG
#include "telemetry.h"
#endif

static PortStatus radio_status = PORT_ERR_DEV_ID;
static bool raw_enabled;

// ---------------------------------------------------------------- LED RGB

// En el Nano 33 BLE el LED RGB se enciende con nivel bajo.
static uint32_t flash_until_ms;

static void led_set(bool red, bool green, bool blue)
{
    digitalWrite(LEDR, red ? LOW : HIGH);
    digitalWrite(LEDG, green ? LOW : HIGH);
    digitalWrite(LEDB, blue ? LOW : HIGH);
}

static void led_begin()
{
    pinMode(LEDR, OUTPUT);
    pinMode(LEDG, OUTPUT);
    pinMode(LEDB, OUTPUT);
    led_set(false, false, false);
}

static void led_flash()
{
    flash_until_ms = millis() + 20;
}

// Rojo intermitente rápido: la radio no arranca. Verde lento: listo.
// Destello azul: ranging correcto.
static void led_update()
{
    uint32_t now = millis();
    if (radio_status != PORT_OK) {
        led_set((now / 150) % 2 == 0, false, false);
    } else if ((int32_t)(flash_until_ms - now) > 0) {
        led_set(false, false, true);
    } else {
        led_set(false, now % 2000 < 60, false);
    }
}

// ---------------------------------------------------------------- Radio

static void print_info()
{
    console_printf("# INFO rol=%s dir=0x%04X dev_id=0x%08lX ant=%u radio=%s\n", ROLE_NAME,
                   (unsigned)MY_ADDR, (unsigned long)port_read_dev_id(),
                   (unsigned)port_antenna_delay(), port_status_text(radio_status));
}

static void start_radio()
{
    radio_status = port_init_radio();
    ranging_reset_stats();
    print_info();
}

// Mientras la radio no arranque se reintenta cada 2 s, imprimiendo lo que se
// lee, para poder revisar el cableado con el nodo encendido.
static void retry_radio()
{
    static uint32_t last_try_ms;
    if (millis() - last_try_ms >= 2000) {
        last_try_ms = millis();
        start_radio();
    }
}

static void print_stats()
{
    const RangingStats &stats = ranging_stats();
    console_printf("# STAT rol=%s ok=%lu timeout=%lu error=%lu late=%lu\n", ROLE_NAME,
                   (unsigned long)stats.ok, (unsigned long)stats.timeout,
                   (unsigned long)stats.error, (unsigned long)stats.late);
}

static void print_raw(const RangingRaw &raw)
{
    console_printf("# RAW %lu %lu %lu %lu %lu %lu %ld\n", (unsigned long)raw.poll_tx,
                   (unsigned long)raw.resp_rx, (unsigned long)raw.final_tx,
                   (unsigned long)raw.poll_rx, (unsigned long)raw.resp_tx,
                   (unsigned long)raw.final_rx, (long)raw.distance_mm);
}

// ---------------------------------------------------------------- Ciclo del tag

#if ROLE == ROLE_TAG

static uint32_t period_ms = 1000 / DEFAULT_RATE_HZ;
static uint32_t next_cycle_ms;
static uint16_t cycle_seq;

static void run_cycle()
{
    RangingRaw raw_a, raw_b;
    RangingResult a = ranging_initiate(ADDR_ANCHOR_A, &raw_a);
    delayMicroseconds(INTER_RANGING_GAP_US);
    RangingResult b = ranging_initiate(ADDR_ANCHOR_B, &raw_b);

    Record record;
    record.seq = cycle_seq++;
    record.d_a = a.distance_mm;
    record.d_b = b.distance_mm;
    record.q_a = a.quality;
    record.q_b = b.quality;
    record.t_ms = millis();
    telemetry_send(record);

    if (raw_enabled) {
        if (a.distance_mm >= 0) {
            print_raw(raw_a);
        }
        if (b.distance_mm >= 0) {
            print_raw(raw_b);
        }
    }
    if (a.distance_mm >= 0 && b.distance_mm >= 0) {
        led_flash();
    }
}

static void tag_loop()
{
    uint32_t now = millis();
    if ((int32_t)(now - next_cycle_ms) < 0) {
        return;
    }
    next_cycle_ms += period_ms;
    // Si el ciclo se ha retrasado más de un periodo no se intenta recuperar.
    if ((int32_t)(now - next_cycle_ms) > 0) {
        next_cycle_ms = now + period_ms;
    }
    run_cycle();
}

#else

// ---------------------------------------------------------------- Bucle del ancla

static void anchor_loop()
{
    static uint32_t last_stats_ms;
    RangingRaw raw;
    if (ranging_respond(&raw)) {
        led_flash();
        if (raw_enabled) {
            print_raw(raw);
        }
    }
    if (millis() - last_stats_ms >= 5000) {
        last_stats_ms = millis();
        print_stats();
    }
}

#endif

// ---------------------------------------------------------------- Comandos

// Lee DEV_ID varias veces seguidas y cuenta las lecturas que no corresponden a
// un DW3000. Es la comprobación del cableado SPI.
static void check_dev_id(long count)
{
    if (count < 1) {
        count = 1;
    }
    if (count > 10000) {
        count = 10000;
    }
    uint32_t last = 0;
    long bad = 0;
    for (long i = 0; i < count; i++) {
        last = port_read_dev_id();
        if ((last & 0xFFFFFF0FUL) != 0xDECA0302UL) {
            bad++;
        }
    }
    console_printf("# ID lecturas=%ld fallos=%ld ultimo=0x%08lX\n", count, bad,
                   (unsigned long)last);
}

static void handle_command(const char *line)
{
    char name[8];
    long value = 0;
    int fields = sscanf(line, "%7s %ld", name, &value);
    if (fields < 1) {
        return;
    }

    if (strcmp(name, "SYNC") == 0) {
        console_printf("# SYNC %lu\n", (unsigned long)millis());
    } else if (strcmp(name, "INFO") == 0) {
        print_info();
    } else if (strcmp(name, "STAT") == 0) {
        print_stats();
    } else if (strcmp(name, "ID") == 0) {
        check_dev_id(fields == 2 ? value : 100);
    } else if (strcmp(name, "ANT") == 0 && fields == 2 && value >= 0 && value <= 65535) {
        port_set_antenna_delay((uint16_t)value);
        console_printf("# ANT %u\n", (unsigned)port_antenna_delay());
    } else if (strcmp(name, "RAW") == 0 && fields == 2) {
        raw_enabled = value != 0;
        console_printf("# RAW %d\n", raw_enabled ? 1 : 0);
#if ROLE == ROLE_TAG
    } else if (strcmp(name, "RATE") == 0 && fields == 2 && value >= 1 && value <= MAX_RATE_HZ) {
        period_ms = 1000 / (uint32_t)value;
        next_cycle_ms = millis() + period_ms;
        console_printf("# RATE %ld\n", value);
#endif
    } else {
        console_printf("# ERROR comando no valido: %s\n", line);
    }
}

// ---------------------------------------------------------------- Arduino

void setup()
{
    console_begin();
    led_begin();
    port_begin();
    start_radio();
#if ROLE == ROLE_TAG
    if (!telemetry_begin()) {
        console_printf("# ERROR BLE no arranca; la telemetria sale solo por serie\n");
    }
    next_cycle_ms = millis() + period_ms;
#endif
}

void loop()
{
    const char *line = console_read_line();
    if (line != NULL) {
        handle_command(line);
    }
    led_update();
#if ROLE == ROLE_TAG
    telemetry_poll();
#endif

    if (radio_status != PORT_OK) {
        retry_radio();
        return;
    }
#if ROLE == ROLE_TAG
    tag_loop();
#else
    anchor_loop();
#endif
}
