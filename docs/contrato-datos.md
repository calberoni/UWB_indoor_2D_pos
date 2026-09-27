# Contrato de datos

Formatos que comparten el firmware, el bridge, el simulador y el dashboard. Cualquier cambio aquí obliga a cambiar las piezas que lo usan.

## 1. Registro de un ciclo (tag → Mac)

Un registro por ciclo de ranging, con los mismos campos por USB serie y por BLE.

| Campo | Tipo | Unidad | Descripción |
| --- | --- | --- | --- |
| seq | uint16 | — | Contador de ciclo; vuelve a 0 después de 65535 |
| d_a | int32 | mm | Distancia al ancla A; −1 si el ranging falló |
| d_b | int32 | mm | Distancia al ancla B; −1 si falló |
| q_a | uint8 | — | Calidad de la señal de A, 0–255 |
| q_b | uint8 | — | Calidad de la señal de B, 0–255 |
| t_ms | uint32 | ms | `millis()` del tag al enviar |

### USB serie

115200 baudios, una línea por ciclo terminada en `\n`:

```
seq,d_a,d_b,q_a,q_b,t_ms
1234,2940,3120,210,198,123456
```

Toda línea que empieza por `#` es un mensaje de depuración o la respuesta a un comando, y no es un registro. El bridge debe ignorar las que no reconozca.

Comandos que acepta el firmware, uno por línea:

| Comando | Respuesta | Efecto |
| --- | --- | --- |
| `SYNC` | `# SYNC <t_ms>` | Devuelve el `millis()` actual, para estimar el desfase de relojes |
| `ANT <valor>` | `# ANT <valor>` | Fija el antenna delay de TX y RX |
| `RATE <hz>` | `# RATE <hz>` | Cambia la tasa del ciclo (solo tag) |
| `RAW <0\|1>` | `# RAW <0\|1>` | Activa la impresión de timestamps crudos como líneas `# RAW ...` |

### BLE

| Elemento | Valor |
| --- | --- |
| Nombre del dispositivo | `UWB-TAG` |
| Servicio | `76360001-61ff-4c6a-8d40-75579102b404` |
| Característica (notify) | `76360002-61ff-4c6a-8d40-75579102b404` |

La característica lleva 16 bytes en little-endian con los campos en el orden de la tabla. En Python: `struct.unpack("<HiiBBI", payload)`.

## 2. Log de sesión

Fichero `logs/<fecha>.csv`, con cabecera. Lo escribe el bridge y lo genera también el simulador.

```
t_host_ms,seq,d_a,d_b,q_a,q_b,t_ms,x,y,zone
1790000000123,1234,2940,3120,210,198,123456,2.310,1.870,mesa
```

| Columna | Unidad | Descripción |
| --- | --- | --- |
| t_host_ms | ms | Hora del Mac al recibir la muestra (epoch) |
| seq … t_ms | — | El registro crudo, sin filtrar |
| x, y | m | Posición calculada; vacías si no había posición válida |
| zone | — | Zona activa; vacía si ninguna |

El replay solo usa `t_host_ms` y las columnas crudas: vuelve a filtrar y a calcular la posición.

## 3. Mensajes del bridge al dashboard

JSON por WebSocket en `ws://localhost:8765`. Todos llevan un campo `type`.

### `config`

Se envía una vez, al conectar cada cliente.

```json
{
  "type": "config",
  "source": "serial",
  "anchors": {"a": {"x": 0.0, "y": 0.0}, "b": {"x": 4.0, "y": 0.0}, "height_m": 1.8},
  "tag": {"height_m": 1.2},
  "room": {"x_min_m": -0.5, "x_max_m": 4.5, "y_min_m": 0.0, "y_max_m": 4.0},
  "zones": [{"name": "mesa", "x_m": 0.3, "y_m": 1.0, "width_m": 1.2, "height_m": 0.8}],
  "display": {"uncertainty_m": 0.2, "wifi_uncertainty_m": 3.0, "trail_s": 3.0}
}
```

`source` vale `serial`, `ble` o `replay`.

### `sample`

Uno por ciclo recibido.

```json
{
  "type": "sample",
  "seq": 1234,
  "x": 2.31, "y": 1.87,
  "d_a": 2.94, "d_b": 3.12,
  "r_a": 2.88, "r_b": 3.06,
  "q_a": 210, "q_b": 198,
  "rate_hz": 9.8,
  "latency_ms": 84,
  "lost": 3,
  "zone": "mesa",
  "valid": true
}
```

| Campo | Unidad | Descripción |
| --- | --- | --- |
| x, y | m | Posición filtrada; `null` si todavía no hay posición |
| d_a, d_b | m | Distancias filtradas, en 3D, tal como las mide la radio; `null` si no hay |
| r_a, r_b | m | Las mismas distancias proyectadas al plano horizontal; son los radios de los arcos que se dibujan |
| q_a, q_b | — | Calidad de la última muestra cruda |
| rate_hz | Hz | Muestras recibidas por segundo, media del último segundo |
| latency_ms | ms | Retraso estimado entre el `t_ms` del tag y la recepción en el Mac |
| lost | — | Ciclos perdidos desde el arranque, según los saltos de `seq` |
| zone | — | Nombre de la zona que contiene el punto, o `null` |
| valid | — | `false` si en este ciclo se descartó alguna de las dos distancias |

## 4. Estimación de la latencia

El tag y el Mac tienen relojes distintos, así que antes de restar hay que estimar el desfase.

- **Por serie:** el bridge envía `SYNC`, anota la hora de envío `t0` y de respuesta `t1`, y calcula `desfase = (t0 + t1) / 2 − t_ms_tag`. Se repite cada 10 s y se conserva la medida con menor `t1 − t0`.
- **Por BLE y en replay:** no hay canal de vuelta. El desfase se toma como el mínimo de `t_host − t_ms` en los últimos 30 s, de modo que la latencia publicada es el retraso por encima del mejor caso observado.

En ambos casos `latency_ms = t_host − (t_ms + desfase)`.
