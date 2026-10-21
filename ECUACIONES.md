# Ecuaciones del sistema — Guía de Orientación

Inventario completo de las ecuaciones y modelos matemáticos usados en cada
parte del código, en el orden en que se ejecutan.

> Nota: la carpeta `antiguo/` contiene versiones antiguas con otras ecuaciones
> (promedios, desviación estándar, calibración del giroscopio en
> `experimentos/caracterizacions.py`), pero **no forma parte del programa
> actual**.

---

## 1. Escalado de lecturas del MPU6050 — `guia/hardware/mpu6050.py`

**Conversión de registros crudos a unidades físicas** (líneas 58-63):

```
a [g]    = registro_crudo / 16384      (rango ±2 g  → 65536 LSB / 4 g)
ω [°/s]  = registro_crudo / 131        (rango ±250 °/s → 65536 LSB / 500 °/s)
```

Los divisores son las **sensibilidades del datasheet** (`ESCALA_ACELEROMETRO`
y `ESCALA_GIROSCOPIO`, líneas 17-18).

**Magnitud de la aceleración** (línea 35, `magnitud_aceleracion`):

```
|a| = √(ax² + ay² + az²)
```

Clave física: **en reposo |a| = 1 g sin importar la postura** (de pie,
sentada o tumbada). Esto lo usan después el contador de pasos y el detector
de caídas.

---

## 2. Muestreo y paso de integración — `guia/navegacion/movimiento.py` (`MuestreadorMPU`)

**Paso temporal** (línea 65):

```
dt = min(t_ahora − t_anterior, DT_MAXIMO = 1.0 s)
```

Se muestrea a **100 Hz** (`intervalo_muestreo_mpu_s = 0.01 s`,
`configuracion.py` línea 53). El tope de 1 s protege contra bloqueos del bus
I2C sin perder ángulos reales.

**Tasa real de muestreo** (líneas 76-77):
`tasa = muestras_transcurridas / tiempo_transcurrido`.

---

## 3. Velocidad de giro sobre la vertical — `movimiento.py` (`MedidorGiro._velocidad_de_giro`, líneas 108-119)

**Ecuación central del proyecto: proyección vectorial** del vector giroscopio
`ω⃗` sobre el versor de la gravedad `û = a⃗/|a⃗|`:

```
ω_vertical = (ax·gx + ay·gy + az·gz) / √(ax² + ay² + az²)
```

Es el **producto punto** `a⃗ · ω⃗ / |a⃗|`. Luego se aplica el signo de la
regla de la mano derecha (línea 117):

```
ω_derecha = −ω_vertical
```

*Por qué:* así el giro funciona igual con el módulo **plano, de canto o
invertido** (la componente que cuenta es siempre la del eje hacia arriba), en
vez de usar `gz` a secas.

**Condición de respaldo** (línea 111): si `|a| < 0,5 g` (caída libre, muestra
rota) se asume montaje plano y se usa `−gz`.

---

## 4. Filtrado del sesgo del giroscopio (calibración en marcha) — `movimiento.py` líneas 134-137

**Filtro paso bajo exponencial (media móvil exponencial)**:

```
α = dt / τ          con τ = constante_sesgo_s = 5.0 s
sesgo[k+1] = sesgo[k] + α · (ω[k] − sesgo[k])
```

Solo se actualiza cuando `|ω − sesgo| ≤ 5 °/s` (rango de reposo,
`velocidad_reposo_dps`). Es la forma discreta de una **ecuación diferencial de
primer orden** `db/dt = (ω − b)/τ`: aprende el offset aunque la temperatura lo
desplace, sin depender de una calibración fija.

---

## 5. Integración del ángulo (giro acumulado) — `movimiento.py` líneas 139-151

**Integración de Euler (rectángulos) de la velocidad angular**, con **zona
muerta (dead-band)**:

```
si |ω − sesgo| > umbral (5 °/s):
    θ_derecha   += |ω − sesgo| · dt      (línea 143)
    θ_izquierda += |ω − sesgo| · dt      (línea 145)
    rumbo       += dirección · dt        (línea 151)
```

Equivalente a `θ = ∫ ω dt ≈ Σ ωₖ·Δt`.

La **zona muerta** (`velocidad_minima_giro_dps = 5 °/s`) filtra el temblor de
la mano: por debajo no es un giro. Luego en `aplicacion.py` se compara
`ángulo ≥ objetivo` (90°, 180° o los 90° de cada tramo) para dar por
completado el giro.

---

## 6. Rumbo relativo — `guia/navegacion/rumbo.py`

```
rumbo[k] = rumbo[k−1] + ω·dt     (línea 31, acumulación)
rumbo = 0 al confirmar una señal  (línea 33, "anclaje")
```

Como el MPU6050 **no tiene magnetómetro**, no hay norte: la referencia de 0°
la da la señal orientadora enfrente. La deriva del giroscopio se corrige
re-anclando en cada señal.

**Vigilancia de línea recta** — `aplicacion.py` líneas 610, 624-640:

```
desvío = |rumbo_actual − rumbo_al_empezar_a_caminar|
si desvío ≥ 25° (desvio_maximo_grados) → aviso con corrección = round(|desvío|) en sentido opuesto
```

Y los **pasos no se acreditan** mientras `desvío ≥ 25°` (líneas 610-614).

---

## 7. Contador de pasos — `movimiento.py` (`ContadorPasos`, líneas 187-218)

**Umbrales dinámicos sobre la línea base** (líneas 197-199):

```
umbral_alto = reposo + Δ        con Δ = delta_paso_g = 0.12 g
umbral_bajo = reposo + 0.5·Δ
```

**Histéresis (trigger de Schmitt) en modo borde** (líneas 201-211): un paso
cuenta solo cuando `|a|` **cruza** el umbral alto de abajo hacia arriba, y se
rearma al bajar del umbral bajo — así una aceleración sostenida no genera
"pasos fantasma". Añade un **tiempo refractario** de 0,35 s
(`intervalo_paso_s`).

**Línea base adaptativa** (líneas 217-218), otro filtro paso bajo:

```
α = min(dt / τ, 1)   con τ = tau_reposo_paso_s = 2.0 s
reposo += (|a| − reposo) · α
```

Se actualiza solo fuera de picos, para seguir la deriva lenta sin subir con
cada paso.

---

## 8. Detección de caídas — `guia/navegacion/caida.py` (líneas 78-84)

**Dos condiciones** (se activa si se cumple alguna):

```
impacto:  | |a| − 1,0 g | ≥ 2,5 g   (umbral_caida_g)        → magnitud muy lejos del reposo
salto:    | |a| − ref    | ≥ 1,5 g   (umbral_cambio_caida_g) → cambio brusco respecto a hace 0,3 s
```

**Salvaguardas** (líneas 86-93): requiere `≥ 2 muestras seguidas` (mata
glitches del bus I2C), **periodo de gracia** de 8 s al arrancar y
**enfriamiento** de 15 s tras cada alerta.

---

## 9. Ultrasonido HC-SR04 — `guia/hardware/ultrasonido.py`

**Distancia por tiempo de vuelo del eco** (línea 348, constante línea 33):

```
d [cm] = t [µs] / 58
```

Derivación física: `d = v·t/2` con `v ≈ 343 m/s` (sonido en el aire) → ida y
vuelta de 1 cm = `2·0,01/343 s ≈ 58 µs`. El pulso de TRIG dura ≥ 10 µs
(línea 34).

**Ecuación del ritmo de pitidos** (`intervalo_de_beep`, líneas 53-54) —
**interpolación lineal con recorte (clamp)**:

```
fracción = clip(d / d_alerta, 0, 1)        d_alerta = 200 cm
intervalo = 0,10 s + fracción · (1,20 s − 0,10 s)
```

O sea: `intervalo(d) = 0,10 + (d/200)·1,10` segundos. A 2 m suena cada 1,2 s;
rozando el obstáculo, cada 0,1 s; más lejos = silencio (`None`). Es una
**relación lineal inversa entre cercanía y frecuencia de pitido**.

---

## 10. Síntesis de audio — `guia/audio/alarma.py` (línea 62)

**Onda senoidal discreta**:

```
s[i] = A · sin(2π · f · i / fs)
```

con `A = 15000`, `fs = 44100 Hz`, `f = 1000 Hz` (beep de proximidad, 80 ms) y
`f = 1200 Hz` (alarma de caída continua, 1 s) — el tono más agudo distingue
la caída de la proximidad.

---

## 11. Visión con YOLO — `guia/vision/detector.py`

No hay ecuación clásica, pero sí **umbral estadístico y lógica de conteo**:

- **Umbral de confianza**: solo se acepta una detección si `confianza ≥ 0,60`
  (`confianza_minima`, líneas 116, 127). El NMS (non-maximum suppression) lo
  resuelve el modelo por dentro.
- **Confirmación de señal** (`ConfirmadorSenal`, líneas 64-83): se necesita
  una **racha de ≥ 3 detecciones seguidas** de la misma clase, tolerando
  **≤ 2 frames perdidos** sin romper la racha. Es un detector de estado
  discreto que reduce falsos positivos (forma: `racha ≥ requeridas`).
- **Enfriamiento de obstáculos** (línea 162): no se repite un anuncio si
  `t_ahora − t_último < 4 s`.

---

## 12. Planificación de rutas — `guia/dominio/rutas.py`

- **Búsqueda en anchura (BFS)** sobre el grafo de zonas
  (`_camino_mas_corto`, líneas 150-167): con **peso uniforme 1 por tramo**,
  BFS da el **camino con mínimo número de tramos** en tiempo `O(V + E)`.
- **Regla de simetría del camino de vuelta** (líneas 96-109): mismos pasos,
  giros **espejados** (`derecha ↔ izquierda`) y en orden inverso — recorrer al
  revés un pasillo recto.
- **Cada giro del mapa = 90°** (`GRADOS_POR_GIRO`, línea 15, cuatro
  orientaciones cardinales).

---

## 13. Guía de giros por voz — `guia/aplicacion.py` (líneas 443-445)

**Anuncios por fracciones del ángulo objetivo**:

```
si ángulo ≥ objetivo · 0,50  → "Vas por la mitad"
si ángulo ≥ objetivo · 0,75  → "Ya casi listo"
si ángulo ≥ objetivo          → "Giro completo"   (90°, 180° o 90° del tramo)
```

---

## Resumen en tabla

| Parte del sistema | Ecuación | Archivo |
|---|---|---|
| Escalado del sensor | `raw/16384` (g), `raw/131` (°/s) | `hardware/mpu6050.py` |
| Magnitud de aceleración | `√(ax²+ay²+az²)` | `hardware/mpu6050.py:35` |
| Giro sobre la vertical | `a⃗·ω⃗/|a⃗|` (producto punto) | `navegacion/movimiento.py:110-119` |
| Filtrado de sesgo | `b += α(ω−b)`, `α = dt/τ` | `movimiento.py:136-137` |
| Integración de ángulo | `θ += ω·dt` con dead-band 5°/s | `movimiento.py:143-151` |
| Pasos | umbrales `reposo+Δ` / `reposo+0,5Δ` (histéresis) + línea base `α = dt/2` | `movimiento.py:197-218` |
| Caída | `\|\|a\|−1g\| ≥ 2,5g` o salto `≥ 1,5g` | `navegacion/caida.py:78-84` |
| Distancia ultrasónica | `d = t[µs]/58` (`d = v·t/2`, v=343 m/s) | `hardware/ultrasonido.py:348` |
| Ritmo de pitidos | `0,10 + (d/200)·1,10` s (lineal) | `ultrasonido.py:53-54` |
| Audio | `s[i] = A·sin(2π f i/fs)` | `audio/alarma.py:62` |
| Confirmación de señal | racha ≥ 3, tolera 2 fallos, conf ≥ 0,60 | `vision/detector.py` |
| Desvío en línea recta | `\|θ−θ₀\| ≥ 25°` → corregir `round(\|desvío\|)` | `aplicacion.py:610-641` |
| Rutas | BFS (camino mínimo, peso 1) + giros espejo | `dominio/rutas.py` |
