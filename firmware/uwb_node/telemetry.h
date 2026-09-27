// Envío del registro de cada ciclo por BLE y por USB serie. Solo el tag.
#pragma once

#include "record.h"

// Arranca BLE y empieza a anunciarse. Devuelve false si la radio BLE no arranca;
// en ese caso la telemetría sigue saliendo por serie.
bool telemetry_begin();

void telemetry_poll();
void telemetry_send(const Record &record);
