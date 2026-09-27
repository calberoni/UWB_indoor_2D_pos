// DS-TWR de cuatro mensajes. El tag inicia (POLL), el ancla responde
// (RESPONSE), el tag envía sus tres timestamps (FINAL) y el ancla calcula la
// distancia y la devuelve (REPORT).
#pragma once

#include <stdint.h>

struct RangingResult {
    int32_t distance_mm; // -1 si el ranging falló
    uint8_t quality;     // 0-255, a partir de la potencia de primer camino
};

// Timestamps de un intercambio (32 bits bajos), para depurar con RAW 1.
// El tag solo conoce los suyos y deja a cero los del ancla.
struct RangingRaw {
    uint32_t poll_tx, resp_rx, final_tx; // reloj del tag
    uint32_t poll_rx, resp_tx, final_rx; // reloj del ancla
    int32_t distance_mm;
};

struct RangingStats {
    uint32_t ok;      // intercambios completos
    uint32_t timeout; // no llegó la trama esperada
    uint32_t error;   // trama recibida con error o con contenido inesperado
    uint32_t late;    // la transmisión programada llegó tarde
};

const RangingStats &ranging_stats();
void ranging_reset_stats();

// Tag: un intercambio completo con el ancla indicada. Bloquea unos milisegundos.
// Si el intercambio termina bien y raw no es NULL, lo rellena.
RangingResult ranging_initiate(uint16_t anchor_addr, RangingRaw *raw);

// Ancla: atiende el receptor. Devuelve true si ha completado un intercambio, y
// en ese caso rellena raw si no es NULL. Hay que llamarla continuamente.
bool ranging_respond(RangingRaw *raw);
