# Calibración del antenna delay

Sin calibrar, el DW3000 mide entre 20 y 60 cm de más: la señal tarda un tiempo en recorrer la antena y la electrónica, y ese tiempo se suma al de vuelo. El *antenna delay* es el valor que el chip resta para compensarlo.

## Qué se ajusta

El error depende de la suma de los retardos de los dos nodos del par, así que midiendo un par no se puede saber cuánto corresponde a cada uno. Como la demo solo usa los pares TAG–A, TAG–B y TAG–C, basta con que cada par mida bien:

1. **Par TAG–A:** se ajusta el delay del tag.
2. **Pares TAG–B y TAG–C:** el delay del tag ya está fijado. Se ajusta el del ancla, o se deja como está y el residuo se anota en el `offset_cm` de esa ancla en `config.yaml`.

Una unidad de delay son 15.65 ps, es decir 4.69 mm. Subir una unidad el delay de TX y de RX de **un** nodo acorta la distancia medida en 4.69 mm.

## Procedimiento

Condiciones: los dos nodos a la misma altura, antenas verticales, línea de visión, a más de 1 m de paredes y objetos metálicos. La distancia se mide con cinta entre los centros de las dos antenas.

1. Tag conectado al Mac por USB; ancla A con su powerbank a 2.00 m.

   ```
   .venv/bin/python tools/calibrate.py --port $(tools/nodes.py port tag) --anchor a --distance 2.00 --apply
   ```

2. Copiar el valor que imprime a `ANTENNA_DELAY` del tag en `firmware/uwb_node/config.h` y grabar el tag: `make flash ROLE=tag`.
3. Comprobar a 4.00 m, sin `--apply`. Si el error pasa de 10 cm, repetir el ajuste a 4.00 m y quedarse con el punto medio de los dos valores.
4. Cambiar el ancla A por la B, a 2.00 m, y medir sin `--apply` (después, lo mismo con la C y `--anchor c`):

   ```
   .venv/bin/python tools/calibrate.py --port $(tools/nodes.py port tag) --anchor b --distance 2.00
   ```

   Si el error es menor de 2 cm no hay que hacer nada. Si no, anotar en `config.yaml` la corrección que propone la herramienta, o conectar el ancla por USB en lugar del tag y repetir el paso 1 con su `--anchor`.

## Resultados

| Nodo | Antenna delay | Fecha | Distancia de calibración | Error final | σ |
| --- | --- | --- | --- | --- | --- |
| TAG | 16385 (sin calibrar) | — | — | — | — |
| ANCLA_A | 16385 (sin calibrar) | — | — | — | — |
| ANCLA_B | 16385 (sin calibrar) | — | — | — | — |
| ANCLA_C | 16385 (sin calibrar) | — | — | — | — |
