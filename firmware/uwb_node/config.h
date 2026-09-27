// Configuración común a los tres nodos: rol, pines, radio, direcciones y tiempos.
#pragma once

#include <stdint.h>

// ---------------------------------------------------------------- Rol

#define ROLE_ANCHOR_A 1
#define ROLE_ANCHOR_B 2
#define ROLE_TAG      3

// El Makefile fija el rol con -DROLE=...; este valor solo se usa al compilar
// desde el IDE de Arduino.
#ifndef ROLE
#define ROLE ROLE_TAG
#endif

#define ADDR_TAG      0x0001
#define ADDR_ANCHOR_A 0x00A1
#define ADDR_ANCHOR_B 0x00A2
#define PAN_ID        0xDECA

#if ROLE == ROLE_ANCHOR_A
#define ROLE_NAME "ANCLA_A"
#define MY_ADDR   ADDR_ANCHOR_A
#elif ROLE == ROLE_ANCHOR_B
#define ROLE_NAME "ANCLA_B"
#define MY_ADDR   ADDR_ANCHOR_B
#elif ROLE == ROLE_TAG
#define ROLE_NAME "TAG"
#define MY_ADDR   ADDR_TAG
#else
#error "ROLE debe ser ROLE_ANCHOR_A, ROLE_ANCHOR_B o ROLE_TAG"
#endif

// ---------------------------------------------------------------- Pines (Nano 33 BLE)

// SCK = D13, MOSI = D11, MISO = D12: los fija el objeto SPI del core.
// D13 es también LED_BUILTIN, así que el estado se señaliza con el LED RGB.
#define PIN_DW_CS     10
#define PIN_DW_RST    9
#define PIN_DW_WAKEUP 8
#define PIN_DW_IRQ    2 // cableado, pero el firmware consulta el estado por SPI

// El DW3000 exige SPI lento hasta que la configuración termina.
#define SPI_SLOW_HZ 2000000UL
#define SPI_FAST_HZ 8000000UL

// ---------------------------------------------------------------- Radio

#define UWB_CHANNEL       5
#define UWB_PREAMBLE_CODE 9

// Valores de Qorvo para el canal 5. No subir la potencia sin medir antes.
#define UWB_TX_PGDLY 0x34
#define UWB_TX_POWER 0xFDFDFDFDUL

// Antenna delay de TX y de RX, en unidades de tiempo del DW3000 (15.65 ps).
// Valor final de cada nodo tras la calibración; ver docs/calibracion.md.
#if ROLE == ROLE_ANCHOR_A
#define ANTENNA_DELAY 16385
#elif ROLE == ROLE_ANCHOR_B
#define ANTENNA_DELAY 16385
#else
#define ANTENNA_DELAY 16385
#endif

// ---------------------------------------------------------------- Tiempos del ranging

// Retardo entre recibir una trama y emitir la respuesta programada. Tiene que
// cubrir la lectura de timestamps y la escritura de la respuesta por SPI; si es
// corto, la transmisión llega tarde y el contador "late" de STAT sube.
#define REPLY_DELAY_US 3000UL

// Ventanas de escucha, en microsegundos UWB (1.0256 us).
#define RX_AFTER_REPLY_TIMEOUT_UUS (REPLY_DELAY_US + 1500UL)
#define RX_REPORT_TIMEOUT_UUS      5000UL

// Límite por software de cualquier espera, por si el chip deja de responder.
#define WAIT_GUARD_US 12000UL

// ---------------------------------------------------------------- Ciclo del tag

#define DEFAULT_RATE_HZ 10
#define MAX_RATE_HZ     30

// Pausa entre el ranging con A y el ranging con B, para que el ancla B haya
// descartado la última trama de A y vuelva a estar escuchando.
#define INTER_RANGING_GAP_US 1500UL

// ---------------------------------------------------------------- Índice de calidad

// La potencia de primer camino se escala linealmente a 0-255 entre estos límites.
#define QUALITY_FP_MIN_DBM (-105)
#define QUALITY_FP_MAX_DBM (-75)

// ---------------------------------------------------------------- Telemetría

#define SERIAL_BAUD 115200
#define BLE_NAME         "UWB-TAG"
#define BLE_SERVICE_UUID "76360001-61ff-4c6a-8d40-75579102b404"
#define BLE_CHAR_UUID    "76360002-61ff-4c6a-8d40-75579102b404"
