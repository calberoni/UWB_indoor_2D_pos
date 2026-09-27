// Capa de portabilidad entre el driver de Qorvo y el Nano 33 BLE: SPI, reset,
// wakeup y arranque del chip.
#pragma once

#include <stdint.h>

enum PortStatus : uint8_t {
    PORT_OK = 0,
    PORT_ERR_DEV_ID,    // el identificador leído no es el de un DW3000
    PORT_ERR_NOT_READY, // el chip no llega al estado IDLE_RC tras el reset
    PORT_ERR_INIT,      // falla dwt_initialise
    PORT_ERR_CONFIG,    // falla dwt_configure (el PLL no engancha)
};

// Configura pines y SPI. Se llama una vez en setup().
void port_begin();

// Reset, identificación y configuración de radio. Se puede repetir.
PortStatus port_init_radio();

const char *port_status_text(PortStatus status);

// Identificador leído directamente por SPI, sin pasar por el driver. Sirve
// para diagnosticar el cableado aunque el arranque haya fallado.
uint32_t port_read_dev_id();

void port_set_antenna_delay(uint16_t delay);
uint16_t port_antenna_delay();
