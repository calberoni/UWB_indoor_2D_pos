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
    // El texto lleva -1 para un fallo, como el registro; solo el binario usa 0xFFFF.
    console_printf("%u,%ld,%ld,%ld,%u,%u,%u,%lu\n", (unsigned)record.seq,
                   (long)record.distance_mm[0], (long)record.distance_mm[1],
                   (long)record.distance_mm[2], (unsigned)record.quality[0],
                   (unsigned)record.quality[1], (unsigned)record.quality[2],
                   (unsigned long)record.t_ms);
}

#endif // ROLE == ROLE_TAG
