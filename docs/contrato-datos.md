# Contrato de datos

Formatos que comparten el firmware, el bridge, el simulador y el dashboard. Cualquier cambio aquí obliga a cambiar las piezas que lo usan.

El sistema tiene tres anclas, identificadas como `a`, `b` y `c`, y un tag. El cálculo de la posición está descrito en [posicionamiento.md](posicionamiento.md).

## 1. Registro de un ciclo (tag → Mac)

Un registro por ciclo de ranging, con los mismos campos por USB serie y por BLE.

| Campo | Tipo | Unidad | Descripción |
| --- | --- | --- | --- |
| seq | uint16 | — | Contador de ciclo; vuelve a 0 después de 65535 |
| d_a | — | mm | Distancia al ancla A; fallo si el ranging no terminó |
| d_b | — | mm | Distancia al ancla B |
| d_c | — | mm | Distancia al ancla C |
| q_a | uint8 | — | Calidad de la señal de A, 0–255 |
| q_b | uint8 | — | Calidad de la señal de B |
| q_c | uint8 | — | Calidad de la señal de C |
| t_ms | uint32 | ms | `millis()` del tag al enviar |

Las distancias válidas van de 0 a 65534 mm. Un ranging fallido se representa como −1 en texto y como 0xFFFF en binario.

### USB serie

115200 baudios, una línea por ciclo terminada en `\n`:

```
seq,d_a,d_b,d_c,q_a,q_b,q_c,t_ms
1234,2940,3120,-1,210,198,0,123456
```

Toda línea que empieza por `#` es un mensaje de depuración o la respuesta a un comando, y no es un registro. El bridge debe ignorar las que no reconozca.

Comandos que acepta el firmware, uno por línea:

| Comando | Respuesta | Efecto |
| --- | --- | --- |
| `SYNC` | `# SYNC <t_ms>` | Devuelve el `millis()` actual, para estimar el desfase de relojes |
| `INFO` | `# INFO rol=... dir=... dev_id=... ant=... radio=...` | Estado del nodo; `radio=` va al final y puede llevar espacios |
| `STAT` | `# STAT rol=... ok=... timeout=... error=... late=...` | Contadores de ranging |
| `ID <n>` | `# ID lecturas=... fallos=... ultimo=...` | Lee DEV_ID n veces; comprueba el cableado SPI |
| `ANT <valor>` | `# ANT <valor>` | Fija el antenna delay de TX y RX |
| `RATE <hz>` | `# RATE <hz>` | Cambia la tasa del ciclo (solo tag) |
| `RAW <0\|1>` | `# RAW <0\|1>` | Activa la impresión de timestamps como `# RAW <ancla> <6 timestamps> <mm>` |

En `# RAW`, `<ancla>` es `a`, `b` o `c`. El tag deja a 0 los tres timestamps del ancla, que no conoce.

### BLE

| Elemento | Valor |
| --- | --- |
| Nombre del dispositivo | `UWB-TAG` |
| Servicio | `76360001-61ff-4c6a-8d40-75579102b404` |
| Característica (notify) | `76360002-61ff-4c6a-8d40-75579102b404` |

La característica lleva 15 bytes en little-endian con los campos en el orden de la tabla. Las distancias van como uint16, con 0xFFFF para un fallo. En Python: `struct.unpack("<HHHHBBBI", payload)`. Con 15 bytes cabe en una notificación con el MTU mínimo de BLE (20 bytes de datos).

## 2. Log de sesión

Fichero `logs/AAAA-MM-DD_HHMMSS.csv`, con cabecera, uno por sesión. Lo escribe el bridge al llegar la primera muestra (nunca en modo replay) y lo genera también el simulador.

```
t_host_ms,seq,d_a,d_b,d_c,q_a,q_b,q_c,t_ms,x,y,zone
1790000000123,1234,2940,3120,-1,210,198,0,123456,2.310,1.870,mesa
```

| Columna | Unidad | Descripción |
| --- | --- | --- |
| t_host_ms | ms | Hora del Mac al recibir la muestra (epoch) |
| seq … t_ms | — | El registro crudo, sin filtrar ni corregir con el offset; −1 para un fallo |
| x, y | m | Posición calculada, también en ciclos con `valid: false`; vacías si no había posición |
| zone | — | Zona activa; vacía si ninguna |

El replay solo usa `t_host_ms` y las columnas crudas: vuelve a filtrar y a calcular la posición.

## 3. Mensajes del bridge al dashboard

JSON por WebSocket en `ws://localhost:8765`. Todos llevan un campo `type`.

### `config`

Se envía al conectar cada cliente. El dashboard lo acepta en cualquier momento y reinicia la estela.

```json
{
  "type": "config",
  "source": "serial",
  "anchors": [
    {"id": "a", "x": 0.0, "y": 0.0, "z": 1.8},
    {"id": "b", "x": 4.0, "y": 0.0, "z": 1.8},
    {"id": "c", "x": 2.0, "y": 4.0, "z": 1.8}
  ],
  "tag": {"height_m": 1.2},
  "room": {"x_min_m": -0.5, "x_max_m": 4.5, "y_min_m": 0.0, "y_max_m": 4.0},
  "zones": [{"name": "mesa", "x_m": 0.3, "y_m": 1.0, "width_m": 1.2, "height_m": 0.8}],
  "display": {"uncertainty_m": 0.2, "wifi_uncertainty_m": 3.0, "trail_s": 3.0}
}
```

`source` vale `serial`, `ble` o `replay`. Las anclas llegan en el orden de `config.yaml`; `z` es su altura sobre el suelo.

### `sample`

Uno por ciclo recibido.

```json
{
  "type": "sample",
  "seq": 1234,
  "x": 2.31, "y": 1.87,
  "err_m": 0.06,
  "anchors_used": 3,
  "ranges": {
    "a": {"d": 2.94, "r": 2.88, "q": 210, "ok": true},
    "b": {"d": 3.12, "r": 3.06, "q": 198, "ok": true},
    "c": {"d": null, "r": null, "q": 0, "ok": false}
  },
  "rate_hz": 9.8,
  "latency_ms": 84,
  "lost": 3,
  "zone": "mesa",
  "valid": true
}
```

| Campo | Unidad | Descripción |
| --- | --- | --- |
| x, y | m | Posición estimada; `null` si no hay |
| err_m | m | Error estimado de la posición (ver posicionamiento.md); `null` si no hay posición |
| anchors_used | — | Anclas que han entrado en el cálculo de este ciclo: 2 o 3; 0 si no hay posición |
| ranges.\<id\>.d | m | Distancia filtrada y corregida con el offset del ancla, en 3D; `null` si todavía no hay |
| ranges.\<id\>.r | m | La misma distancia proyectada al plano del tag; es el radio del arco que se dibuja. `null` si no hay |
| ranges.\<id\>.q | — | Calidad de la última muestra cruda de esa ancla |
| ranges.\<id\>.ok | — | `true` si la muestra de este ciclo se aceptó; `false` si falló o el filtro la descartó |
| rate_hz | Hz | Inverso del intervalo medio entre las muestras del último segundo; `null` hasta tener dos muestras |
| latency_ms | ms | Retraso estimado entre el `t_ms` del tag y la recepción en el Mac |
| lost | — | Ciclos perdidos desde el arranque, según los saltos de `seq` |
| zone | — | Nombre de la zona que contiene el punto, o `null` |
| valid | — | `false` si la posición de este ciclo es dudosa: menos de 3 anclas, o residuo por encima de `positioning.max_residual_m` |

`ranges` tiene una entrada por cada ancla de `config`, con las mismas claves.

Con `ok: false`, `d` y `r` conservan el último valor filtrado mientras la última muestra aceptada de esa ancla tenga menos de `positioning.max_age_s`; después pasan a `null`. Es el mismo criterio con el que el ancla entra o no en el cálculo. Tras un reinicio (del tag o del filtro de un ancla), `d` y `r` son `null` durante las 3 primeras muestras aunque `ok` sea `true`.

Cuando el tag deja de enviar, el bridge no publica nada. El dashboard considera que no hay señal tras 1 s sin mensajes `sample`.

## 4. Estimación de la latencia

El tag y el Mac tienen relojes distintos, así que antes de restar hay que estimar el desfase.

- **Por serie:** el bridge envía `SYNC`, anota la hora de envío `t0` y de respuesta `t1`, y calcula `desfase = (t0 + t1) / 2 − t_ms_tag`. Se repite cada 10 s y se conserva la medida con menor `t1 − t0` de los últimos 60 s. Hasta la primera respuesta se usa el método de BLE.
- **Por BLE y en replay:** no hay canal de vuelta. El desfase se toma como el mínimo de `t_host − t_ms` en los últimos 30 s, de modo que la latencia publicada es el retraso por encima del mejor caso observado.

En ambos casos `latency_ms = t_host − (t_ms + desfase)`, limitado a 0. Si `t_ms` retrocede, el tag se ha reiniciado: se descartan el desfase, los filtros, la posición anterior y la referencia de `seq`.

`latency_ms` mide el transporte hasta el Mac. No incluye el retraso del filtro ni el del dibujo.
