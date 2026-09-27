// Puerto serie USB: salida con formato y lectura de comandos por líneas.
#pragma once

void console_begin();

// No bloquea si no hay nadie escuchando al otro lado del USB.
void console_printf(const char *format, ...) __attribute__((format(printf, 1, 2)));

// Devuelve una línea completa, sin el fin de línea, o NULL si todavía no hay.
// El puntero es válido hasta la siguiente llamada.
const char *console_read_line();
