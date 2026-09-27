#include "config.h"

#if ROLE == ROLE_TAG

#include "telemetry.h"

#include <ArduinoBLE.h>

#include "console.h"

static BLEService service(BLE_SERVICE_UUID);
static BLECharacteristic record_characteristic(BLE_CHAR_UUID, BLERead | BLENotify, RECORD_LEN,
                                               true);
static bool ble_ready;

bool telemetry_begin()
{
    if (!BLE.begin()) {
        return false;
    }
    BLE.setLocalName(BLE_NAME);
    BLE.setDeviceName(BLE_NAME);
    // Intervalo de conexión de 7.5 a 15 ms, para que la notificación no espere.
    BLE.setConnectionInterval(0x0006, 0x000C);
    service.addCharacteristic(record_characteristic);
    BLE.addService(service);
    BLE.setAdvertisedService(service);

    uint8_t empty[RECORD_LEN] = {0};
    record_characteristic.writeValue(empty, sizeof(empty));
    BLE.advertise();
    ble_ready = true;
    return true;
}

void telemetry_poll()
{
    if (ble_ready) {
        BLE.poll();
    }
}

void telemetry_send(const Record &record)
{
    if (ble_ready && BLE.connected()) {
        uint8_t payload[RECORD_LEN];
        record_pack(&record, payload);
        record_characteristic.writeValue(payload, sizeof(payload));
    }
    console_printf("%u,%ld,%ld,%u,%u,%lu\n", (unsigned)record.seq, (long)record.d_a,
                   (long)record.d_b, (unsigned)record.q_a, (unsigned)record.q_b,
                   (unsigned long)record.t_ms);
}

#endif // ROLE == ROLE_TAG
