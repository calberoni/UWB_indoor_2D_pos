# Cálculo de la posición 2D

El tag mide su distancia a tres anclas fijas. Con tres distancias la posición en el plano es única, siempre que las anclas no estén alineadas. Este documento describe cómo la calcula el bridge a partir de las distancias crudas.

## Geometría

Cada ancla tiene una posición conocida `(x_i, y_i)` en el plano de la sala y una altura `z_i`. El tag se lleva a una altura `h` constante. Todo se lee de `config.yaml`.

Las anclas se colocan formando un triángulo que rodee la zona de la demo, por ejemplo dos en una pared y la tercera en la pared de enfrente. Cuanto más cerca de 60° estén los ángulos del triángulo, menor es el error. Con las tres casi alineadas el cálculo pierde precisión en la dirección perpendicular a la línea; el bridge lo detecta y lo refleja en `err_m`.

## Pasos por ciclo

### 1. Filtro por ancla

Cada ancla tiene su propio filtro: mediana de las últimas 5 muestras válidas y después media exponencial con α = 0.4. Una muestra se descarta si el ranging falló, si su calidad es menor que `filter.min_quality`, o si se aleja más de `filter.max_jump_m` de la mediana de las aceptadas. Tras `filter.max_jump_rejects` descartes seguidos por salto, se acepta que el tag se ha movido y el filtro se reinicia con esas muestras.

Tras cualquier reinicio (arranque, reinicio del tag, o un ancla sin muestras aceptadas durante `positioning.max_age_s`) el filtro no da valor hasta reunir 3 muestras, y la primera salida es su mediana. Así un outlier aislado no entra en la media exponencial, y un ancla que vuelve tras un rato no reaparece con la distancia de hace segundos. Cuesta unos 0.2 s sin distancia de esa ancla.

Al resultado se le suma el offset de calibración del ancla.

### 2. Proyección al plano del tag

Las distancias medidas son 3D. Con `Δh_i = z_i − h`:

```
r_i = √(d_i² − Δh_i²)      (0 si d_i < |Δh_i|)
```

### 3. Qué anclas entran

Entra en el cálculo cada ancla con distancia filtrada disponible **y** muestra aceptada en los últimos `positioning.max_age_s` segundos. Así un ancla tapada durante un rato no sigue arrastrando la posición con su último valor.

### 4a. Tres anclas: mínimos cuadrados

Se busca el punto `p` que minimiza

```
Σ (‖p − a_i‖ − r_i)²
```

por Gauss-Newton, partiendo de la posición del ciclo anterior o, si no la hay, de la solución lineal (restando la ecuación del primer círculo a las otras dos, queda un sistema lineal de 2×2). Hasta 10 iteraciones, o hasta que el paso sea menor de 1 mm. Si el residuo sale alto, se repite desde la solución lineal y se queda la mejor.

El residuo se normaliza por los grados de libertad, porque la posición ya absorbe dos de las distancias:

```
residuo = √( Σ (‖p − a_i‖ − r_i)² / (N − 2) )
```

Con tres anclas solo sobra una distancia, así que un ancla con sesgo no siempre se nota. En simulación, un sesgo de +0.8 m en un ancla supera el umbral de 0.30 m en un 80 % de la sala; en el resto, sobre todo hacia el centro del triángulo, el sesgo se reparte en la posición y pasa inadvertido. Dividiendo por N en lugar de N − 2 solo se detectaba en un 35 %. Si pasa de `positioning.max_residual_m`, las distancias no son coherentes entre sí (típicamente un rebote con una ancla sin línea de visión) y el ciclo se marca `valid: false`, pero la posición se publica.

### 4b. Dos anclas: intersección con desambiguación

Si solo hay dos anclas, los dos círculos se cortan en dos puntos simétricos respecto a la recta que las une. Se elige:

1. Si solo uno de los dos cae dentro de la sala (con `positioning.room_margin_m` de margen), ese.
2. Si los dos caen dentro, el más cercano a la posición anterior, siempre que esa posición tenga menos de `positioning.max_age_s` segundos.
3. Si no se puede decidir, no hay posición en este ciclo.

Si los círculos no se cortan, se toma el punto de la recta entre las dos anclas en la proporción de sus radios. El ciclo siempre se marca `valid: false`.

**Límite conocido.** Si el tag cruza la recta que une las dos anclas que quedan, las dos soluciones se juntan en el cruce y la regla de cercanía puede quedarse en la rama simétrica. En simulación, con un ancla caída 3 s durante un paseo, el punto llega a saltar hasta 3.7 m cuando el recorrido cruza esa recta. Con dos distancias no hay información para evitarlo. Lo que ayuda es colocar las anclas para que la zona de la demo quede dentro del triángulo y lejos de sus lados.

### 4c. Menos de dos anclas

No hay posición.

### 5. Error estimado

```
err_m = HDOP × max(positioning.range_sigma_m, residuo)
```

`HDOP = √(traza((HᵀH)⁻¹))`, donde `H` tiene una fila por ancla usada con el vector unitario de `a_i` a `p`. Con tres anclas bien repartidas HDOP ronda 1.2, y crece al salir del triángulo. Con dos anclas se calcula igual, con dos filas, y crece mucho cerca de la recta que las une. Se limita a 20, para que `err_m` siga siendo un número dibujable cuando es infinito.

El dashboard dibuja el círculo de incertidumbre con radio `max(display.uncertainty_m, err_m)`.

## Parámetros

En `config.yaml`, sección `positioning`:

| Clave | Por defecto | Descripción |
| --- | --- | --- |
| range_sigma_m | 0.05 | Error típico de una distancia filtrada; mínimo para `err_m` |
| max_residual_m | 0.30 | Por encima, el ciclo se marca dudoso |
| max_age_s | 0.5 | Antigüedad máxima de la última muestra aceptada de un ancla, y de la posición anterior para desambiguar |
| room_margin_m | 0.30 | Margen alrededor de la sala al decidir entre dos soluciones |
