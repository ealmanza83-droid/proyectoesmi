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

## 4. Filtrado del sesgo del giroscopio (calibración en marcha) — `movimiento.py` (`MedidorGiro._aprender_sesgo`)

**Filtro paso bajo exponencial (media móvil exponencial)**:

```
α = dt / τ          con τ = constante_sesgo_s = 5.0 s
sesgo[k+1] = sesgo[k] + α · (objetivo[k] − sesgo[k])
```

La novedad es **de dónde sale `objetivo`**: una **ventana deslizante de
extremos** (`deque` de `(tiempo, velocidad)`, `ventana_sesgo_s = 1.0 s`) da
`v_min` y `v_max` de la ventana, y con ellos se clasifica el estado actual
(`_clase_sesgo`):

| Estado | Condición | `objetivo` aprendido |
|---|---|---|
| `girando` | `|velocidad_sostenida| ≥ 5 °/s` (gate) | **no aprende** |
| `plano` | `v_max − v_min ≤ 5 °/s` (`velocidad_reposo_dps`) | `velocidad` actual (marcha plana: `v ≈ sesgo`) |
| `oscilando` | `v_max − v_min ≥ 10 °/s` **y** `v_min < v < v_max` estricto | `punto_medio = (v_max + v_min)/2` |
| arranque | ventana aún incompleta (`primera_muestra`) | **no aprende** |

*Por qué ventana y no envolvente ataque/release:* el punto medio de los
extremos es **independiente de la fase** de la oscilación de la marcha — la
marcha sí es un movimiento casi sinusoidal de ±10 °/s, y cualquier detector
fase-acoplado aprendía el "centro" equivocado. Además, aprender solo con
`min < v < max` excluye los instantes en que la velocidad está en un extremo
(el valor en el extremo, que es `sesgo ± amplitud`, sesgaría el promedio).

*Valvula de seguridad* (`recalibracion_sesgo_s = 20.0 s`): si el estado lleva
20 s `plano and girando` a la vez (sesgo ya desviado > 5 °/s, que abre el
gate sin que la señal real supere la zona muerta), aprende igualmente. Un giro
real de 90° a la mínima velocidad tarda < 18 s, así que no se "recalibra"
en medio de un giro intencionado.

Es la forma discreta de una **ecuación diferencial de primer orden**
`db/dt = (ω − b)/τ`: aprende el offset aunque la temperatura lo desplace, sin
depender de una calibración fija.

---

## 5. Integración del ángulo (giro acumulado) — `movimiento.py` (`MedidorGiro.avanzar`)

**Integración de Euler (rectángulos) de la velocidad angular**, con **zona
muerta (dead-band)**:

```
si |velocidad − sesgo| > umbral (5 °/s):
    θ_derecha   += |velocidad − sesgo| · dt
    θ_izquierda += |velocidad − sesgo| · dt
```

Equivalente a `θ = ∫ ω dt ≈ Σ ωₖ·Δt`.

La **zona muerta** (`velocidad_minima_giro_dps = 5 °/s`) filtra el temblor de
la mano: por debajo no es un giro. Luego en `aplicacion.py` se compara
`ángulo ≥ objetivo − grados_compensacion_giro` (90°, 180° o los 90° de cada
tramo, con 12° de adelanto) para dar por completado el giro. El adelanto
absorbe el giro que se da mientras suena la frase final y la instrucción
siguiente (ambas bloquean el bucle): era el desfase de ~10° que se notaba
al girar.

**Ángulo neto del giro pedido** (`MedidorGiro.avanzar`) — el giro hecho al
lado **contrario se resta**, no se ignora:

```
θ_neto = θ_derecha   − θ_izquierda      (si se pide girar a la derecha)
θ_neto = θ_izquierda − θ_derecha        (si se pide girar a la izquierda)
completado si θ_neto ≥ objetivo − grados_compensacion_giro (12°)
```

El rumbo acumulado se normaliza al rango `[-180°, 180°)` y las
diferencias se calculan por el arco angular más corto:

```
Δθ = ((θ_actual − θ_referencia + 180°) mod 360°) − 180°
```

Así una deriva o varias vueltas no convierten una corrección de, por
ejemplo, `+217°` a la izquierda en una orden de seguir girando en el
mismo sentido cuando la persona ya cruzó el rumbo de referencia.

*Por qué:* la instrucción es "gira 90° a la izquierda" y lo que importa es
la **rotación real pendiente** respecto de la orientación en la que se
anunció. Si antes de girar a la izquierda se giraron 30° a la derecha, hacen
falta 120° de izquierda para acabar mirando donde toca. Con la regla vieja
(mirar solo el acumulado del sentido pedido) esos 30° se ignoraban: el giro
se daba por completo con la persona **30° desviada** y el resto de la ruta
arrancaba mal orientado. Un `θ_neto` negativo significa literalmente "estás
girando al lado contrario" (la etiqueta de diagnóstico lo muestra).

---

## 6. Rumbo relativo — `guia/navegacion/rumbo.py`

```
g_filtrada[k] = g_filtrada[k−1] + (1 − exp(−dt/τg)) · (a[k] − g_filtrada[k−1])
ω_vertical[k] = −(g_filtrada · gyro[k]) / |g_filtrada|
direccion[k] = (ω_vertical[k] − sesgo) · signo_derecha
rumbo[k] = rumbo[k−1] + direccion[k]·dt
            solo si |sostenida[k]| ≥ velocidad_minima_giro_dps
rumbo = 0 al confirmar una señal  (re-anclaje)
```

Como el MPU6050 **no tiene magnetómetro**, no hay norte: la referencia de 0°
la da la señal orientadora enfrente. La deriva del giroscopio se corrige
re-anclando en cada señal.

La vertical se estima filtrando el acelerómetro con `tau_gravedad_giro_s`
(0,5 s por defecto) antes de proyectar el giroscopio. Usar la aceleración
instantánea hacía que la aceleración lateral de cada paso inclinara el eje
de proyección y convirtiera parte del balanceo en un giro falso. El rumbo
usa esta proyección filtrada y conserva la velocidad instantánea corregida
para integrar los grados del giro.

**Gate de giro sostenido** (`MedidorGiro.en_muestra`): la velocidad
proyectada pasa primero por un EMA (`tau_sostenida_giro_s = 0.5 s`):

```
sostenida[k] = sostenida[k−1] + min(dt/0,5, 1)·(direccion[k] − sostenida[k−1])
```

y el rumbo **solo integra** cuando `|sostenida| ≥ 5 °/s`. Así el balanceo
alternante no abre por sí solo la puerta de integración, y la proyección no
se contamina con la aceleración lateral de los pasos.

**Vigilancia de línea recta** — `aplicacion.py` (`_vigilar_desvio`):

```
desvío = |rumbo_actual − rumbo_al_empezar_a_caminar|
si desvío ≥ 35° (desvio_maximo_grados) durante ≥ 1,5 s (confirmacion_desvio_s) → aviso
al volver por debajo de 0,6·35° (liberacion_desvio_factor) → "enderezaste"
```

**Avance proyectado**: cada paso nuevo aporta su longitud por la proyección
del rumbo sobre el eje del tramo. El umbral de 35° sigue activando avisos,
pero no descarta avance:

```
desvio = |rumbo_actual − rumbo_al_empezar_a_caminar|
avance += L_paso · max(0, cos(desvio))
si desvio > desvio_credito_max_grados (75°): avance += 0
pasos_efectivos = avance / zancada_vigente
```

Si la cámara confirma centrada la señal interior esperada, prevalece esa
referencia visual: el rumbo se reancla y los pasos nuevos aportan su zancada
completa. La señal visible y el camino libre siguen siendo requisitos para
cerrar el tramo; el avance estimado nunca anuncia por sí solo la llegada.

La **confirmación temporal** evita avisos falsos por un paso torcido o un
balanceo puntual; la **histéresis de liberación** evita el efecto
aviso/contraviso en el umbral. Los pasos desviados aportan avance longitudinal
reducido por el coseno del ángulo, en vez de congelarse al cruzar el umbral
de aviso.

---

## 7. Contador de pasos — `movimiento.py` (`ContadorPasos`)

**Pasa-banda del paso** (biquad RBJ del cookbook, sin scipy): la
oscilación de la aceleración al caminar está entre 0,5 y 5 Hz; todo lo
demás (la gravedad, la postura que cambia, el balanceo lento, el
temblor) queda fuera de banda y se anula solo:

```
H(z) = pasa-banda de ganancia pico 0 dB, centro = √(f_baja · f_alta) ≈ 1,58 Hz
f_baja = frecuencia_paso_min_hz = 0,5 Hz     f_alta = frecuencia_paso_max_hz = 5,0 Hz
alpha = sin(w₀) · sinh(ln2/2 · log₂(f_alta/f_baja))      (Q del cookbook RBJ)
rediseñar coeficientes solo si |fs_nueva - fs_anterior| / fs_anterior >= 0,05
```

Las variaciones pequeñas de `dt` por la latencia de I²C no deben rediseñar
el filtro ni reiniciar su estado: hacerlo en cada muestra impide que la
señal acumule la oscilación necesaria para detectar pasos. El 5 % es
configurable con `variacion_recalculo_filtro_paso_pct`.

El filtro **no tiene entrada en continua**: una postura que sube 0,10 g
de golpe no produce ningún pico, así que no hace falta perseguir una
media local. Arranca "sembrado" en continuo (`x₁=x₂=muestra`,
`y₁=y₂=0`), de modo que un sensor quieto en 1,15 g no genera
transitorio ni pico fantasma. Ojo: el pasa-banda tiene ceros en DC y en
Nyquist, por eso una oscilación justo a fs/2 no aparece (las pruebas
usan ciclos de 4 muestras a 10 Hz = 2,5 Hz, y no de 2).

**Umbral adaptativo** sobre la señal ya filtrada:

```
umbral = max(umbral_paso_min_g ; factor_umbral_paso · media_últimos_picos)
       = max(0,09 g ; 0,45 · media de los ≤5 últimos picos CONTADOS)
```

Los picos **contados** alimentan la media al cerrarse el pico (bajar a
la mitad del umbral), así que el umbral queda a la mitad de lo que de
verdad produce la marcha de esa persona: si camina suave baja solo —el
piso de 0,09 g cubre el ruido del sensor—, y si va vigoroso sube, con
lo que el balanceo y el temblor se quedan fuera.

**Histéresis (borde)**: un paso cuenta cuando la señal filtrada cruza
el umbral de abajo hacia arriba, y se rearma al bajar a la mitad — así
una aceleración sostenida no genera "pasos fantasma". Añade un
**tiempo refractario** de 0,18 s (`intervalo_paso_s`), por debajo del
mínimo fisiológico entre pasos.

**Validación de cadencia**: se tolera que el ritmo cambie. La cadencia
solo rechaza un pico demasiado cercano al anterior (probable doble
conteo); los intervalos más lentos se aceptan mientras no excedan el
límite de pausa:

```
si intervalo > intervalo_paso_max_s (1,5 s)         → guardar pico pendiente
si intervalo < (1 − cadencia_tolerancia) · mediana
  con ≥ cadencia_intervalos_minimos (3) intervalos   → descarte "cadencia"
cadencia_tolerancia = 0,70: se descartan intervalos < 30 % de la mediana
```

Así los cambios de paso no pierden pisadas: por ejemplo, intervalos de
0,44 s tras un ritmo previo de 0,82 s cuentan. Si el intervalo supera
1,5 s, el primer pico queda **pendiente** y reinicia la referencia del
ritmo; el siguiente pico de marcha dentro de la ventana acredita ambos.
Si no aparece otro pico dentro de esa ventana, el candidato pendiente
se descarta. Así, mover el pie mientras se escucha o esquiva un
obstáculo no suma un paso, y el primer paso al reanudar no se pierde.
El primer paso de un tramo nuevo (`reiniciar`) siempre cuenta,
todavía sin cadencia con que juzgarlo.

**Rebasado del ritmo**: tras `rechazos_rebasar` = 3 picos demasiado
cercanos seguidos, la cadencia ya no describe a la persona (cambio de
paso): se olvida (`_ritmo.clear()`) y el siguiente pico arranca un
ritmo nuevo.

El reloj del contador es la **suma de los `dt`** recibidos (no
`time.monotonic`): la detección es reproducible en las pruebas y no
depende de la carga de la cámara.

*Por qué banda + umbral adaptativo y no línea base:* la línea base
antigua subía con cada paso y los valles quedaban por debajo del umbral
→ pasos perdidos; la media local corrigió eso pero perseguía la
postura. El pasa-banda bloquea la continua de raíz y el umbral se
autoescala con los picos reales, sin ninguna línea base que mantener.

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
fracción = clip(d / d_alerta, 0, 1)        d_alerta = 300 cm
intervalo = 0,10 s + fracción · (1,20 s − 0,10 s)
```

O sea: `intervalo(d) = 0,10 + (d/300)·1,10` segundos. A 3 m suena cada 1,2 s;
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
- **Anuncio único por encuentro de obstáculos**
  (`Aplicacion._anunciar_obstaculos`): el detector reporta lo que se ve en el
  frame (sin enfriamiento propio) y la aplicación anuncia **una sola vez** cada
  objeto mientras siga a la vista. El aviso se re-arma cuando el frame queda
  limpio durante `enfriamiento_objeto_s` = 4 s (`t_ahora − t_limpio ≥ 4 s`), de
  modo que un parpadeo de la cámara no lo repite y un objeto nuevo sí se
  anuncia enseguida.
- **Tres fragmentos de la imagen**: el frame se divide en 3 partes iguales
  (izquierda, centro, derecha); el fragmento central va de `1/3` a `2/3`
  del ancho (`obstaculo_centro_min/max`) y la clasificación usa la
  **caja completa** del objeto (`posicion_en_frame`): para ser *centro*
  la caja tiene que **caber entera** en el fragmento (`x₁ ≥ ⅓·ancho` y
  `x₂ ≤ ⅔·ancho`); si se sale de un solo lado es de ese lado —aunque el
  centro `(x₁+x₂)/2` caiga dentro, que era el obstáculo "no totalmente
  en medio" contado como central—; si se sale de los dos lados (un
  frontal muy cerca que ocupa el central entero) sí es centro. Dando
  pasos (`NAVEGANDO`, `CINCO_PASOS`) solo se considera ese fragmento
  (`mirar_solo_centro_en_marcha` = True); fuera de esos estados la
  cámara no anuncia **obstáculo alguno** (la persona gira sobre su
  propio eje para buscar la señal). La señal orientadora no se
  recorta: se lee con los 3 fragmentos en todo momento.
- **Prioridad de la voz**: los obstáculos solo hablan si la guía lleva
  `silencio_para_obstaculos_s` = 4 s sin hablar
  (`t_ahora − t_última_voz ≥ 4 s ⇒ puede anunciar`); si no, el aviso espera
  la siguiente vuelta del bucle. Así una instrucción (giro, pasos, llegada)
  jamás se atrasa ni se corta por un objeto.

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
- **Pasos del tramo**: `tramo.pasos` (el conteo con el que se midió la
  casa), salvo que se pase `zancada_m` y el tramo tenga `distancia_m`:
  entonces `max(1, int(distancia/zancada + 0,5))` (ver §15).

---

## 13. Guía de giros por voz — `guia/aplicacion.py` (`_mostrar_giro`)

**Sin anuncios por fracciones del ángulo objetivo**: al 50 % y al 75 %
antes se hablaba ("Vas por la mitad", "Ya casi listo") y la frase
llegaba tarde: la persona sigue girando mientras la voz suena, así que
cuando terminaba ya había girado más de lo pedido y el giro se
deshacía. Queda un solo anuncio:

```
si ángulo ≥ objetivo − 12    → "Puedes parar. …"  (orden primero; el corte
                                se adelanta grados_compensacion_giro = 12°
                                para absorber el giro de la frase siguiente)
```

---

## 14. Ventana de llegada del tramo — `guia/aplicacion.py` (`_valorar_llegada_del_tramo`)

Los pasos son lazo abierto (la zancada cambia entre personas y ritmos, y
el error se acumula), así que el tramo **no** se da por terminado en
"N pasos" exactos. Con `N` = pasos esperados del tramo:

```
v = zancada_m / mediana(intervalos_recientes)
d_reacción = v · tiempo_reaccion_llegada_s
d_frenado  = v² / (2 · desaceleracion_llegada_m_s2)
margen     = min(d_reacción + d_frenado,
                 distancia_tramo · anticipacion_llegada_max_fraccion)
umbral_aviso = ventana_llegada_inicio − margen / distancia_tramo

avance ≥ umbral_aviso
    → aviso para reducir el paso (no cierra el tramo)

avance ≥ ventana_llegada_inicio
    ∧ la cámara ve la señal interior de la zona de cierre
    → el tramo se cierra  (re-localización VERIFICADA)

fin = max(ventana_llegada_fin,
          1 + sigmas_cierre · incertidumbre / distancia_tramo)
avance ≥ fin  sin esa señal
    → se detiene la marcha y se busca la señal del punto intermedio
      girando hacia ella; los pasos brutos también abren la búsqueda
      al alcanzar ceil(N · fin), por si el desvío invalida su proyección
```

El aviso se limita a adelantarse como máximo `anticipacion_llegada_max_fraccion`
(0,15 del tramo por defecto). Si aún no hay cadencia medida, se estima
con `periodo_paso_llegada_s`. El cálculo anticipa reacción y desaceleración;
no altera el umbral mínimo de 70 % para cerrar con cámara. El umbral de
búsqueda anticipada usa `avance + sigmas_inicio_busqueda · incertidumbre`;
la incertidumbre puede ensanchar el límite máximo, pero nunca sustituye
la señal ni anuncia una llegada. Si el tramo no tiene distancia conocida,
se conserva el aviso en el umbral de `ventana_llegada_inicio`.

En un tramo intermedio, alcanzar el límite superior sin ver su señal
detiene el contador y pide buscarla girando hacia el lado de la ruta. Esto
solo abre la búsqueda: no confirma la zona ni permite ejecutar el siguiente
giro. La cámara debe reconocer la señal interior esperada para continuar.
El límite de pasos brutos evita caminar indefinidamente cuando un desvío
severo hace que la proyección longitudinal no avance.

La **zona de cierre** es `_camino[tramos_completados + 1]` (la siguiente
del camino, la que este tramo debe alcanzar): verla **no** es una
desviación y `_seguir_senal` la excluye de la replanificación (antes,
verla dos veces seguidas — la puerta enfrente mientras se camina —
provocaba un "Te saliste de la ruta" falso). La llegada al destino sigue
siendo un caso aparte: solo la señal **interior del destino** la anuncia.

---

## 15. Zancada adaptativa — `guia/dominio/zancada.py`

La zancada (metros que avanza la persona con un paso) no es constante:
cambia con el calzado, el ritmo y la pendiente, y de ella salen los
pasos de cada tramo (`pasos = round(distancia / zancada)`). La semilla
es `0,60 m`, medida como 30,2 m / 50 pasos.

**Medida** — solo en un tramo cerrado **por la cámara** dentro de la
ventana de llegada (§14): solo ahí se recorrió entera la distancia
medida del mapa, así que `distancia/pasos` es de verdad la zancada.

```
medida_k  = distancia_m / pasos_acreditados_k
medida_k ← clip(medida_k, zancada_min_m, zancada_max_m)   # 0,30 … 0,90
zancada_k = α · medida_k + (1 − α) · zancada_{k−1}        # α = zancada_ema_alfa = 0,30
```

Solo se aprende si el tramo fue limpio: sin obstáculo central anunciado,
sin corrección de desvío y con desvío máximo menor que
`desvio_aprendizaje_max_grados` (20°). El cierre sin señal nunca aprende,
porque no se sabe dónde acabó el tramo y `distancia/pasos` no sería la
zancada.

**Ajuste experimental opcional por pico** — desactivado por defecto:

```
L_k = zancada_EMA · clip((pico_k / media_picos) ** 0.25, 0.75, 1.25)
```

El contador expone el pico filtrado de cada paso. Para evaluar CSV sin
activar la opción en la guía:
`python pruebas/evaluar_pasos.py caminata.csv --pasos-reales N --distancia-real-m D`.
El evaluador compara el error de distancia constante y por pico e informa
la correlación pico/longitud. La salida solo recomienda activarlo cuando
el CSV se marca explícitamente como de ojos vendados (`--ojos-vendados`)
y el error por pico es menor; hacen falta varios recorridos para calibrar.

**Uso** — `PlanificadorRutas.planificar(origen, destino, zancada_m)`:

```
sin zancada medida → pasos = tramo.pasos                       # semilla del mapa
con zancada medida → pasos = max(1, int(distancia/zancada + 0,5))
```

`Aplicacion._zancada_para_plan()` pasa `None` mientras
`zancada.aprendida` sea falso: el primer plan sigue anunciando los pasos
del mapa (11 / 8 / 31) y el cambio llega gradual, porque `α = 0,30`
deja que tres o cuatro tramos midan sin que un tramo raro descuadre el
plan siguiente.

---

## 16. Evaluación offline del contador de pasos

`pruebas/registrar_caminata.py` registra el tiempo monotónico, las seis
componentes del MPU6050 y la última distancia disponible del HC-SR04. En
`pruebas/evaluar_pasos.py`, cada fila se reconstruye como `LecturaMPU` y
se reproduce con el mismo `ContadorPasos`; `dt` procede de los tiempos
del archivo (para la primera fila se usa `intervalo_muestreo_mpu_s`):

```
pasos_detectados = ContadorPasos(reproducir(CSV))
error_% = |pasos_detectados − pasos_reales| / pasos_reales · 100
```

El valor de ultrasonido se conserva para inspección, pero no interviene
en la detección de pasos. Una muestra con tiempo no creciente o columnas
incompletas invalida el archivo en vez de producir una evaluación parcial.

---

## 17. Distancia a una señal por cámara

Con el modelo de cámara pinhole y la altura real impresa `H` en metros,
la altura detectada `h` en píxeles y la focal calibrada `f` en píxeles:

```
d_m = f_px · H_m / h_px
f_px = d_m · h_px / H_m       (calibración a distancia conocida)
```

La aplicación acumula las últimas `distancia_mediana_frames` medidas por
clase y usa su mediana; tras `distancia_mediana_perdidas` frames sin la
clase, vacía el historial para no reutilizar una distancia antigua. Una
caja de señal menor que `alto_minimo_senal_px` (20 px), una focal `<= 0`,
una altura física no positiva o una caja sin altura produce `None`, no una
distancia inventada. Si `focal_px` no está calibrada, el arranque avisa.
`altura_senal_m` debe medirse para la impresión usada y coincidir con
`--altura-real-m` de `pruebas/calibrar_focal.py`.

La corrección del filtro usa la distancia a la señal interior esperada:
`x_medida = distancia_tramo − d_m`, con
`varianza_medicion_senal_m2`. El mapa actual solo declara señales de zona
como destino/cierre del tramo; no declara hitos visuales intermedios, por
lo que no hay una segunda clase de señal que corregir. Si el mapa incorpora
hitos, cada uno debe declarar su ubicación en el tramo y usar una varianza
mayor que la señal de cierre.

---

## 18. Odómetro ultrasónico frontal por tramo (opcional)

Para un tramo configurado con distancia de referencia al final
`d_fin_cm`, se necesitan `N` lecturas nuevas válidas. Las lecturas que
difieran de la mediana actual más que `salto_maximo_ultrasonido_cm` se
rechazan antes de entrar al historial:

```
d_estable[k] = mediana(últimas N lecturas aceptadas)
avance = clip((d_inicio − d_estable) / 100, 0, (d_inicio − d_fin) / 100)
```

`d_inicio` se fija con las primeras `N` lecturas estables del tramo. No
se vuelve a alimentar el filtro con la misma muestra: el HC-SR04 expone
un número de secuencia para distinguir medidas nuevas. El avance es una
medición auxiliar de posición, no una confirmación visual ni un anuncio
de llegada. Su corrección solo se fusiona si el mapa conoce la longitud
del tramo, la posición estimada está a `distancia_maxima_m` (4 m) o menos
del final y no hay un obstáculo central/recentemente anunciado. La
posición inicial del odómetro se ancla a la posición estimada al obtener
la primera lectura estable; así no se interpreta el primer eco como el
inicio del tramo.

---

## 19. Estimador de posición por tramo (Kalman 1D)

El estado `x` es la distancia recorrida y `P` su varianza. Cada nuevo
paso acreditado predice avance usando la zancada aprendida; después se
corrige con medidas independientes disponibles (señal interior de
cierre y/o odómetro frontal estable):

```
x⁻ = x + Δpasos · zancada
Δavance = Δpasos · zancada
P⁻ = P + factor · (Δpasos · varianza_proceso_paso_m2
                   + (error_relativo_zancada · Δavance)²)
K  = P⁻ / (P⁻ + R)
x  = x⁻ + K · (z − x⁻)
P  = (1 − K) · P⁻
incertidumbre = √P
```

`factor = inflar_incertidumbre_esquive` mientras haya un obstáculo central
anunciado o una corrección de desvío activa; en marcha normal vale 1. La
semilla inicial de zancada es `0,60 m` (30,2 m / 50 pasos medidos).
La posición se limita a `[0, distancia_tramo]` cuando el mapa conoce el
largo. Una lectura de señal da `z = distancia_tramo − distancia_a_señal`;
el HC-SR04 aporta `z = avance_ultrasonico`. `R` usa la varianza
configurada para esa fuente. El aviso de búsqueda puede empezar cuando
`x + sigmas_inicio_busqueda·√P` alcanza la ventana; el cierre del tramo
usa el avance estimado y **requiere además la señal interior esperada**.
Ni los pasos ni el filtro anuncian la llegada al destino.

---

## 20. Puertas como guía visual y medición auxiliar

La clase YOLO `door` es independiente de `SENALES_ORIENTADORAS`: una
puerta puede orientar un giro o aportar distancia, pero no puede asignar
la zona. Si la señal aún no está confirmada, el animal detectado dentro
de la caja de puerta tiene prioridad; la aplicación anuncia la posición
horizontal una vez por encuentro y re-arma el aviso tras varios frames
sin verla.

La puerta usa sus dimensiones físicas configurables, con la altura como
medida primaria y el ancho como respaldo:

```
d_puerta = f_px · altura_puerta_m / alto_px
       o  f_px · ancho_puerta_m / ancho_px
```

La mediana de `distancia_mediana_frames` suaviza el rango. Una puerta
centrada puede corregir el filtro con `z = distancia_tramo − d_puerta`
(varianza `varianza_medicion_puerta_m2`). El seguimiento asocia detecciones
por centro horizontal y cuenta solo una vez cuando cada caja entra al
fragmento central. Si el tramo espera `M` puertas y ya se pasaron `k`,
la corrección débil es `z = D·k/(M+1)`. Ninguna de estas medidas produce
`Senal`, avanza identidad de zona ni cierra un tramo sin confirmación de
la señal interior.

La llegada a una señal interior esperada se confirma al reconocerla con
la cámara y tener el camino libre; no es necesario centrar la caja en la
imagen. Si hay una lectura ultrasónica estable menor que
`distancia_puerta_min_cm` (45 cm), se pide detenerse y alejarse. Si la
señal se reconoce, no se exige estar a menos de 80 cm: esa distancia solo
se usa como guía de acercamiento cuando la persona ya giró hacia la señal,
pero la cámara todavía no la reconoce. Si el ultrasonido no tiene una
lectura estable, la cámara sigue siendo la referencia. Una detección de
obstáculo frontal también bloquea la confirmación:

Si una medida salta más de `salto_maximo_ultrasonido_puerta_cm`, se
mantiene como candidata y solo se adopta el nuevo rango después de
varias lecturas consistentes. El pitido conserva la última distancia
válida durante `retencion_eco_ultrasonido_s` ante una pérdida breve de
eco; esa retención solo mantiene la alerta sonora y nunca valida una
llegada.

Una lectura aislada que salta más de `salto_maximo_ultrasonido_puerta_cm`
se retiene como candidata; el nuevo rango se adopta solo si varias
lecturas consecutivas lo confirman. Las pérdidas breves de eco mantienen
el pitido por `retencion_eco_ultrasonido_s`, pero nunca se usan para
confirmar una llegada.

```
d_ultra = mediana(d_1, ..., d_M) si hay lecturas estables; ⊥ si no
confirmar = señal_interior_esperada
            ∧ (d_ultra no disponible ∨ d_ultra ≥ distancia_puerta_min_cm)
            ∧ camino_frontal_libre
            ∧ tiempo_desde_aviso_obstáculo ≥ espera_llegada_tras_obstaculo_s
```

En el estado de encarado, si la persona ya giró y aún no reconoce la señal,
una lectura superior a `distancia_maxima_senal_cm` (80 cm) da un aviso para
avanzar despacio mientras continúa la búsqueda. Si la cámara sí reconoce la
señal, se ancla el rumbo y sigue la ruta independientemente de que la
distancia supere 80 cm.

Los pasos y una advertencia de obstáculo no confirman la llegada; tras un
aviso se espera el intervalo configurado aunque la señal aparezca en el
frame siguiente. Si la señal aún no aparece, la aproximación final
continúa lentamente hasta `distancia_puerta_max_cm` (140 cm por defecto)
y entonces vuelve a buscarla con la cámara. La clase `door` es opcional:
solo se exige y se advierte su ausencia cuando `usar_puertas=True`.

---

## Resumen en tabla

| Parte del sistema | Ecuación | Archivo |
|---|---|---|
| Escalado del sensor | `raw/16384` (g), `raw/131` (°/s) | `hardware/mpu6050.py` |
| Magnitud de aceleración | `√(ax²+ay²+az²)` | `hardware/mpu6050.py:35` |
| Giro sobre la vertical | `a⃗·ω⃗/|a⃗|` (producto punto) | `navegacion/movimiento.py` |
| Filtrado de sesgo | `b += α(objetivo−b)`; objetivo = plano→`v`, oscilando→`(max+min)/2` | `movimiento.py` (`_aprender_sesgo`) |
| Gate de rumbo | EMA `τ=0,5 s`, integra solo con `\|sostenida\| ≥ 5°/s` | `movimiento.py` (`en_muestra`) |
| Integración de ángulo | `θ += ω·dt` con dead-band 5°/s | `movimiento.py` (`paso`) |
| Pasos | pasa-banda `0,5–5 Hz` + umbral `máx(0,09 g; 0,45·media de picos)` con histéresis, refractario 0,18 s y cadencia validada | `movimiento.py` (`ContadorPasos`) |
| Caída | `\|\|a\|−1g\| ≥ 2,5g` o salto `≥ 1,5g` | `navegacion/caida.py:78-84` |
| Distancia ultrasónica | `d = t[µs]/58` (`d = v·t/2`, v=343 m/s) | `hardware/ultrasonido.py:348` |
| Ritmo de pitidos | `0,10 + (d/300)·1,10` s (lineal) | `ultrasonido.py:53-54` |
| Audio | `s[i] = A·sin(2π f i/fs)` | `audio/alarma.py:62` |
| Confirmación de señal | racha ≥ 3, tolera 2 fallos, conf ≥ 0,60 | `vision/detector.py` |
| Desvío en línea recta | `\|θ−θ₀\| ≥ 35°` durante 1,5 s → corregir; libera a 0,6·35° | `aplicacion.py` (`_vigilar_desvio`) |
| Ventana de llegada del tramo | `fin=max(1,30; 1+sigmas_cierre·σ/D)`; solo la cámara cierra | `aplicacion.py` (`_valorar_llegada_del_tramo`) |
| Zancada adaptativa | EMA de `D/pasos_efectivos`, solo tramo limpio; semilla 0,60 m | `dominio/zancada.py` |
| Distancia visual | `d=f·H/h`; alto mínimo 20 px; mediana por clase | `vision/detector.py` |
| Odómetro opcional | `x_inicio + clip((d_inicio−d_actual)/100, 0, d_inicio−d_fin)` dentro de últimos 4 m | `aplicacion.py`, `navegacion/odometro.py` |
| Posición por tramo | Predicción por pasos + corrección Kalman `K=P/(P+R)`; `σ=√P` | `navegacion/posicion.py` |
| Puertas | Rango por `f·H/h`; conteo por caja centrada, una vez por rastro | `vision/detector.py`, `navegacion/puertas.py` |
| Rutas | BFS (camino mínimo, peso 1) + giros espejo | `dominio/rutas.py` |
