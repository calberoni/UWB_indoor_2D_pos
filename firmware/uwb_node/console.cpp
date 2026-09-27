#include "console.h"

#include <Arduino.h>
#include <stdarg.h>
#include <stdio.h>

#include "config.h"

static char line[64];
static uint8_t line_len;

void console_begin()
{
    Serial.begin(SERIAL_BAUD);
}

void console_printf(const char *format, ...)
{
    char buffer[160];
    va_list args;
    va_start(args, format);
    int length = vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    if (length <= 0) {
        return;
    }
    if ((size_t)length >= sizeof(buffer)) {
        length = sizeof(buffer) - 1;
    }
    Serial.write((const uint8_t *)buffer, (size_t)length);
}

const char *console_read_line()
{
    while (Serial.available() > 0) {
        int c = Serial.read();
        if (c == '\n' || c == '\r') {
            if (line_len == 0) {
                continue;
            }
            line[line_len] = '\0';
            line_len = 0;
            return line;
        }
        if (line_len < sizeof(line) - 1) {
            line[line_len++] = (char)c;
        }
    }
    return NULL;
}
