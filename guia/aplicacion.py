"""Orquestador del asistente: maquina de estados, voz y navegacion.

Esta es la unica pieza que coordina todo.  Cada fase del recorrido es
un estado (:class:`Estado`) con su propio manejador, de modo que el
bucle principal siempre hace lo mismo: mirar, escuchar, actuar y
mostrar.

Hilos en juego (la aplicacion solo ORQUESTA; el trabajo pesado corre
en paralelo para que la camara nunca congele nada):

* ``muestreador-mpu``  -> lee el MPU6050 100 veces por segundo y
  reparte cada lectura entre MedidorGiro (grados girados + rumbo),
  ContadorPasos y DetectorCaida.  Integrar en hilo propio evita
  perder grados de giro cuando la camara/YOLO saturan el bucle.
* ``alerta-proximidad`` -> vigila el HC-SR04 en su propio hilo y
  acelera los pitidos a medida que se acerca un obstaculo.
* ``microfono``        -> Vosk escucha en su propio hilo y publica
  transcripciones en una cola; el modo actual (despertador, destino,
  comando, silencio) decide que se publica y con que gramatica.
* ``bucle principal``  -> camara + YOLO, eventos de voz, caidas,
  obstaculos y el manejador del estado actual (un solo punto de
  decision: ``self._manejadores[self.estado]``).

Flujo completo (estados)::

    GIRO_INICIAL ──90°──> BUSCAR_SENAL ──ve señal interior──> ESPERANDO_DESTINO
         ▲                    │    ▲                              │ pide destino
         │              no la ve   │                         ENCARAR_SENAL
         │                    ▲    │                              │ ve la señal
    CINCO_PASOS          MEDIA_VUELTA                      NAVEGANDO
    (5 pasos y reintentar)  (180°)                          (giros y pasos
                                                             de la ruta)
                                                                   │
                                                          pasos completados
                                                                   v
                                                        CONFIRMANDO_LLEGADA
                                                        (ver la señal del
                                                         destino: la llegada
                                                         NUNCA se anuncia
                                                         solo por pasos)
                                                                   │
                                                              "Has llegado"
                                                                   v
                                                        ESPERANDO_DESTINO

Durante NAVEGANDO, cada señal interior vista sirve de GPS a pie: si es
el destino se verifica la llegada; si esta en la ruta se avanza; si
esta detras o fuera de ella (la persona se devolvio o se desvio), se
replanifica desde la ubicacion real.
"""

from __future__ import annotations

import queue
import time
from pathlib import Path
from typing import Callable

from .audio.alarma import ReproductorBeep
from .audio.locutor import Locutor
from .audio.microfono import Microfono, ModoMicrofono
from .configuracion import Configuracion
from .dominio.catalogo import (
    LADO_INTERIOR,
    LADO_PASILLO,
    Senal,
    senal_de_clase,
)
from .dominio.comandos import (
    TipoComando,
    contiene_despertador,
    extraer_destino,
    quitar_despertador,
    reconocer,
)
from .dominio.estados import Estado
from .dominio.rutas import (
    Accion,
    GIRO_SENAL_NORMAL,
    Instruccion,
    PlanificadorRutas,
)
from .dominio.zancada import Zancada
from .hardware.mpu6050 import MPU6050
from .hardware.ultrasonido import AlertaProximidad, SensorUltrasonico
from .navegacion.caida import DetectorCaida
from .navegacion.movimiento import ContadorPasos, MedidorGiro, MuestreadorMPU
from .navegacion.odometro import OdometroUltrasonico
from .navegacion.posicion import EstimadorPosicionTramo
from .navegacion.puertas import ContadorPuertas
from .navegacion.registro import RegistroCaminata
from .navegacion.rumbo import Rumbo
from .version import obtener_version
from .vision.detector import (
    EN_MEDIO_DEL_CAMINO,
    Deteccion,
    DetectorVisual,
    ResultadoVision,
    dibujar_detecciones,
)
from .vision.interfaz import TECLA_SALIR, VentanaDiagnostico

# Enfoque de busqueda: girar, mirar, media vuelta, mirar, 5 pasos.
MAX_INTENTOS_DESDE_GIRO = 2

# Cada cuantos segundos se avisa por consola el avance del giro.
# Sirve para diagnosticar en el Raspberry (consola) si la velocidad
# de giro tiene el signo correcto o si el giro esta atascado en cero.
AVISO_GIRO_S = 1.0

# Margen, tras el aviso del desvio, antes de volver a recordar la
# correccion si no se esta mejorando.  El aviso mismo tarda varios
# segundos en sonar y la persona necesita reaccion y tiempo de giro
# despues de oirlo; el reloj cuenta desde que termina de sonar.
RECORDATORIO_CORRECCION_S = 15.0

# Estados en que los obstaculos estan ACTIVOS: la persona camina la
# ruta (dando pasos).  En los demas estados (giro inicial, busqueda
# y encarada de la senal, media vuelta...) gira sobre su propio eje
# o esta quieta, y la camara NO anuncia obstaculo alguno: solo la
# senal orientadora se sigue leyendo, con los 3 fragmentos de la
# imagen.  En marcha ademas solo cuenta el fragmento central (ver
# ``mirar_solo_centro_en_marcha``).
_ESTADOS_DE_MARCHA = frozenset({Estado.NAVEGANDO, Estado.CINCO_PASOS})


def _unir_nombres(nombres: list[str]) -> str:
    """Une una lista de nombres en espanol: 'silla', 'silla y mesa'."""
    if len(nombres) == 1:
        return nombres[0]
    return ", ".join(nombres[:-1]) + " y " + nombres[-1]


class Aplicacion:
    """Ciclo de vida completo del asistente de orientacion."""

    def __init__(
        self,
        *,
        config: Configuracion,
        camara,
        mpu: MPU6050,
        sensor: SensorUltrasonico,
        alarma: ReproductorBeep,
        locutor: Locutor,
        microfono: Microfono,
        detector: DetectorVisual,
        planificador: PlanificadorRutas | None = None,
        ventana: VentanaDiagnostico | None = None,
        registro_caminata: Path | None = None,
    ):
        self.config = config
        self.camara = camara
        self.mpu = mpu
        self.sensor = sensor
        self.alarma = alarma
        self.locutor = locutor
        self.microfono = microfono
        self.detector = detector

        # Pitidos de proximidad: se silencian solos mientras la voz habla
        # (``locutor.hablando`` es el evento compartido entre ambos hilos).
        self.alerta = AlertaProximidad(sensor, alarma, locutor.hablando)

        self.estado = Estado.GIRO_INICIAL
        self.ubicacion: str | None = None
        self.lado_puerta: str | None = None
        """Lado de la puerta desde el que se vio la senal (o ``None``).

        Solo la cara interior de la puerta confirma "la persona esta
        en la zona": desde el lado del pasillo esta al otro lado de
        la puerta y la ruta no debe arrancar.
        """
        self.destino: str | None = None

        # Movimiento: el muestreador lee el MPU6050 a 100 Hz y reparte
        # cada lectura entre el medidor de giro, el contador de pasos
        # y el detector de caidas.  Integrar en un hilo propio evita
        # que la carga de la camara haga perder grados de giro.
        # El rumbo acumula la orientacion continua (relativa a la
        # ultima senal vista) con los mismos giros ya depurados.
        self.rumbo = Rumbo()
        self.giro = MedidorGiro(config, rumbo=self.rumbo)
        self.pasos = ContadorPasos(config)
        self._odometro_ultrasonico = OdometroUltrasonico(config)
        self._medidor_distancia_puerta = OdometroUltrasonico(
            config,
            muestras_requeridas=config.muestras_ultrasonido_puerta,
            salto_maximo_cm=config.salto_maximo_ultrasonido_puerta_cm,
        )
        self._distancia_puerta_estable_cm: float | None = None
        self._ultima_secuencia_distancia_puerta = getattr(
            sensor, "secuencia_medicion", None
        )
        self.estimador_posicion = EstimadorPosicionTramo(config)
        self._contador_puertas = ContadorPuertas(config)
        self._puerta_guiada = False
        self._puertas_ausentes = 0
        self._pasos_estimador_previos = 0
        self._ultima_medicion_ultra_aplicada: float | None = None
        # Zancada de la persona: se mide en cada tramo que cierra la
        # camara y de ella salen los pasos del plan siguiente (ver
        # dominio/zancada.py).  Arranca con la semilla del mapa.
        self.zancada = Zancada(
            inicial=config.zancada_m,
            alfa=config.zancada_ema_alfa,
            minimo=config.zancada_min_m,
            maximo=config.zancada_max_m,
        )
        self.caida = DetectorCaida(config)
        self._registro_caminata = (
            RegistroCaminata(registro_caminata, sensor)
            if registro_caminata is not None
            else None
        )
        oyentes_mpu = [
            self.giro.en_muestra,
            self.pasos.en_muestra,
            self.caida.en_muestra,
        ]
        if self._registro_caminata is not None:
            oyentes_mpu.append(self._registro_caminata.en_muestra)
        self.muestreador = MuestreadorMPU(
            mpu,
            config,
            oyentes_mpu,
        )

        # Navegacion
        self._planificador = planificador or PlanificadorRutas()
        self._plan: list[Instruccion] = []
        self._indice_plan = 0
        self._instruccion_actual: Instruccion | None = None
        self._ruta_pendiente: list[Instruccion] | None = None
        self._instruccion_encarar_dada = False
        # Zona e instante de la ultima senal interior confirmada con
        # la camara (busqueda, espera del destino o encarada).  Si
        # hace poco y el rumbo no ha girado desde entonces, la senal
        # sigue enfrente y la ruta arranca sin pedir encarar
        # (ver _iniciar_encarada).
        self._senal_enfrente: tuple[str, float] | None = None
        # Lado en el que girar para encarar la senal: depende del
        # sentido de la ruta (ver PlanificadorRutas.lado_encarada).
        self._lado_encarada: str = GIRO_SENAL_NORMAL
        self._rumbo_al_caminar = 0.0
        self._ultima_alerta_desvio: float | None = None
        # Momento en que el desvio cruzo el umbral: el aviso solo sale
        # si se sostiene ``confirmacion_desvio_s`` (ver _vigilar_desvio).
        self._inicio_desvio: float | None = None
        # Correccion guiada del rumbo: el giro de vuelta a la recta se
        # sigue en vivo (no con un numero fijo) hasta decir "para".
        self._corrigiendo = False
        self._desvio_inicial = 0.0
        self._pasos_acreditados = 0
        self._pasos_previos = 0
        # Zonas del plan en curso: sirven para seguir el avance y para
        # detectar si la persona se devolvio (y replanificar a tiempo).
        self._camino: list[str] = []
        self._indice_zona = 0
        # Avance por tramos: al terminar cada tramo (los pasos
        # completados) falta encarar la senal de la zona alcanzada
        # ANTES del giro del siguiente tramo, igual que al arrancar.
        self._tramos_completados = 0
        self._falta_encarar = False
        # Llegada del tramo por VENTANA (ver _valorar_llegada_del_tramo):
        # la zona que cierra el tramo en curso (la siguiente del camino)
        # y el aviso de "ya casi llegas", dado una sola vez por tramo.
        self._zona_cierre_tramo: str | None = None
        self._aviso_ventana_dado = False
        # Distancia medida del tramo en curso (0.0 si el mapa no la
        # tiene): al cerrarlo con la camara es con lo que se mide la
        # zancada de la persona.
        self._distancia_tramo = 0.0
        self._distancia_frontal_inicio_cm: float | None = None
        self._avance_ultrasonico_m: float | None = None
        self._ultima_secuencia_ultrasonica: int | None = None
        self._inicio_llegada = 0.0

        # Busqueda de senal
        self._intento = 0
        self._inicio_busqueda = 0.0

        # Voz
        self._fin_ventana_comando = 0.0
        self._en_pausa = False
        # Momento en que la voz libero el canal (0.0 = nunca hablo).
        # Los obstaculos esperan ``silencio_para_obstaculos_s`` desde
        # ahi: la navegacion manda y un aviso jamas atrasa una
        # indicacion de la ruta.
        self._ultima_voz_en = 0.0

        self._etiqueta = ""
        self._ultimo_aviso_giro = 0.0
        # Enfriamiento de los avisos hablados que dependen de lo que
        # se ve con la camara (puerta del lado del pasillo, senal de
        # otra zona): una senal confirmada se repite en varios frames.
        self._ultima_advertencia: dict[str, float] = {}
        # Sincronizacion camara + ultrasonido: si el eco ya estaba en
        # rango no se vuelve a explicar, y un anuncio de la camara
        # reciente cuenta como la misma explicacion.
        self._cerca_antes = False
        self._ultimo_anuncio_obstaculo = 0.0
        self._ultima_alerta_obstaculo_en = 0.0
        # Un obstaculo se anuncia UNA vez por encuentro (antes se
        # repetia cada pocos segundos mientras siguiera a la vista y
        # la voz, que bloquea el bucle, tapaba las indicaciones de la
        # ruta).  Estas claves guardan lo que ya se dijo y desde
        # cuando no queda nada a la vista: el aviso se re-arma solo
        # cuando el frame estuvo limpio ``enfriamiento_objeto_s``.
        self._medio_anunciado: tuple[str, ...] = ()
        self._medio_libre_desde: float | None = None
        self._lado_anunciado: tuple[str, ...] = ()
        self._lado_libre_desde: float | None = None
        self._ventana = ventana or VentanaDiagnostico()

        self._manejadores: dict[Estado, Callable[["Aplicacion", ResultadoVision], None]] = {
            Estado.GIRO_INICIAL: Aplicacion._en_giro_inicial,
            Estado.BUSCAR_SENAL: Aplicacion._en_buscar_senal,
            Estado.MEDIA_VUELTA: Aplicacion._en_media_vuelta,
            Estado.CINCO_PASOS: Aplicacion._en_cinco_pasos,
            Estado.ESPERANDO_DESTINO: Aplicacion._en_esperando_destino,
            Estado.ENCARAR_SENAL: Aplicacion._en_encarar_senal,
            Estado.NAVEGANDO: Aplicacion._en_navegando,
            Estado.CONFIRMANDO_LLEGADA: Aplicacion._en_confirmando_llegada,
        }

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    def ejecutar(self) -> None:
        """Arranca los hilos, corre el bucle y garantiza la limpieza."""
        version = obtener_version()
        print()
        print("======================================")
        print("INICIANDO SISTEMA")
        print(f"Version del codigo: {version}")
        print("======================================")

        try:
            self.muestreador.start()
            self.alerta.start()
            self.microfono.iniciar()
            time.sleep(2)

            self.microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)
            self.giro.reiniciar()
            self._hablar(
                f"Gira {self.config.grados_giro_inicial} grados a la derecha "
                "suavemente para buscar la señal orientadora."
            )
            # Mientras se manipulaba el equipo no se miran caidas.
            self.caida.reiniciar_gracia()
            self._bucle()
        except KeyboardInterrupt:
            print()
            print("Sistema detenido por el usuario.")
        finally:
            self.estado = Estado.DETENIDO
            self.cerrar()

    def cerrar(self) -> None:
        """Detiene hilos y libera los recursos que usa la aplicacion."""
        print()
        print("Cerrando sistema...")
        try:
            self.muestreador.detener()
            self.alerta.detener()
            self.microfono.detener()
        finally:
            if self._registro_caminata is not None:
                self._registro_caminata.cerrar()
        self.alarma.esperar()
        self._ventana.cerrar()
        self.locutor.limpiar_cache()
        print("Sistema cerrado correctamente.")

    # ------------------------------------------------------------------
    # Bucle principal
    # ------------------------------------------------------------------
    def _bucle(self) -> None:
        while True:
            frame = self.camara.capturar()
            resultado = self.detector.analizar(frame)
            distancia_cm = self.sensor.distancia_cm
            self._actualizar_distancia_puerta(distancia_cm)

            self._procesar_voz()
            self._vigilar_caida()

            # Si la alarma de caida se apago sola (se acabo el tiempo),
            # el micro no puede quedarse para siempre escuchando solo
            # "silencia": vuelve al despertador.
            if (
                self.microfono.modo is ModoMicrofono.SILENCIO
                and not self.alarma.alarma_activa
            ):
                self.microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)

            # Un solo punto de decision: el manejador del estado actual.
            self._manejadores[self.estado](self, resultado)

            # Los obstaculos van DESPUES de la navegacion y ademas
            # esperan el silencio de la voz: asi una instruccion (giro,
            # pasos, llegada) nunca se atrasa ni se corta por un aviso
            # de objeto.
            self._anunciar_obstaculos(resultado, distancia_cm)

            tecla = 0
            if self._ventana.activa:
                dibujar_detecciones(
                    frame,
                    resultado,
                    centro_min=self.config.obstaculo_centro_min,
                    centro_max=self.config.obstaculo_centro_max,
                )
                tecla = self._ventana.mostrar(
                    frame,
                    distancia_cm=distancia_cm,
                    estado=self.estado,
                    detalle=self._etiqueta,
                    ubicacion=self.ubicacion,
                    destino=self.destino,
                )
            if tecla == TECLA_SALIR:
                break

    # ------------------------------------------------------------------
    # Voz
    # ------------------------------------------------------------------
    def _hablar(self, texto: str, cambiar_a: ModoMicrofono | None = None) -> None:
        """Habla sin que el microfono se escuche a si mismo.

        Si no se indica ``cambiar_a``, se vuelve al modo que estaba
        activo (util para no romper la escucha de destino).

        Al terminar queda registrado el momento en que quedo libre el
        canal de voz: hasta ``silencio_para_obstaculos_s`` despues no
        se anuncia un obstaculo (ver ``_anunciar_obstaculos``), que
        asi nunca se come una instruccion de la ruta.
        """
        modo_anterior = self.microfono.modo
        self.microfono.cambiar_modo(ModoMicrofono.APAGADO)
        self.locutor.hablar(texto)
        self.microfono.cambiar_modo(cambiar_a if cambiar_a is not None else modo_anterior)
        self._ultima_voz_en = time.monotonic()

    def _advertecer(self, clave: str, texto: str) -> None:
        """Habla un aviso solo una vez por encuentro (con enfriamiento).

        Una senal confirmada se mantiene visible varios frames seguidos:
        sin enfriamiento, la misma advertencia se repetiria en cada
        frame mientras la persona tenga la puerta a la vista.
        """
        ahora = time.monotonic()
        if (
            ahora - self._ultima_advertencia.get(clave, 0.0)
            < self.config.enfriamiento_objeto_s
        ):
            return
        self._ultima_advertencia[clave] = ahora
        print("AVISO:", texto)
        self._hablar(texto)

    def _procesar_voz(self) -> None:
        """Atiende los eventos del microfono sin bloquear el video."""
        self._cerrar_ventana_de_comando_expirada()

        while True:
            try:
                evento = self.microfono.eventos.get_nowait()
            except queue.Empty:
                return

            if evento.modo is ModoMicrofono.DESPERTADOR:
                orden = quitar_despertador(evento.texto)
                if orden:
                    self._ejecutar_comando(orden)
                else:
                    # "Raspi" solo: abrimos una ventana corta de ordenes.
                    self._hablar("Sí, dime.", cambiar_a=ModoMicrofono.COMANDO)
                    self._fin_ventana_comando = (
                        time.monotonic() + self.config.ventana_comando_s
                    )

            elif evento.modo is ModoMicrofono.DESTINO:
                # Tambien sirve una orden dicha con "Raspi"
                # (ej. "Raspi, ve a la sala").
                if contiene_despertador(evento.texto):
                    orden = quitar_despertador(evento.texto)
                    if orden:
                        self._ejecutar_comando(orden)
                    self.microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)
                    continue

                destino = extraer_destino(evento.texto)
                if destino is not None:
                    self._asignar_destino(destino)
                else:
                    self._hablar(
                        "No reconozco ese lugar. Puedes pedir inicio, "
                        "sala, baño o cuarto."
                    )

            elif evento.modo is ModoMicrofono.COMANDO:
                self._fin_ventana_comando = 0.0
                self._ejecutar_comando(evento.texto)
                self.microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)

            elif evento.modo is ModoMicrofono.SILENCIO:
                # Con la alarma de caida sonando solo se escucha para
                # apagarla (ya no hace falta la palabra "Raspi").
                self._ejecutar_comando(evento.texto)

    def _cerrar_ventana_de_comando_expirada(self) -> None:
        if self._fin_ventana_comando and time.monotonic() > self._fin_ventana_comando:
            self._fin_ventana_comando = 0.0
            if self.microfono.modo is ModoMicrofono.COMANDO:
                self.microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)

    def _ejecutar_comando(self, texto: str) -> None:
        """Interpreta una orden dicha por la persona."""
        comando = reconocer(texto)

        if comando.tipo is TipoComando.DESTINO:
            self._asignar_destino(comando.valor)

        elif comando.tipo is TipoComando.PARAR:
            self._en_pausa = True
            self._hablar("Alto. Navegación en pausa.")

        elif comando.tipo is TipoComando.REANUDAR:
            self._en_pausa = False
            self._hablar("Navegación reanudada.")

        elif comando.tipo is TipoComando.SILENCIAR:
            # Apaga la alarma continua de una caida si esta sonando.
            if self.alarma.alarma_activa:
                self.alarma.silenciar_alarma()
                self.microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)
                self._hablar("Alarma apagada.")

        elif comando.tipo is TipoComando.GIRAR_DERECHA:
            self._hablar("Gira a la derecha suavemente.")

        elif comando.tipo is TipoComando.GIRAR_IZQUIERDA:
            self._hablar("Gira a la izquierda suavemente.")

        elif comando.tipo is TipoComando.DEVOLVERME:
            self._devolverme()

        elif comando.tipo is TipoComando.OTRA_RUTA:
            self._otra_ruta()

        else:
            self._hablar("No entendí la orden.")

    def _devolverme(self) -> None:
        """Orden de volver a la zona anterior por la que paso la ruta."""
        if self.estado not in (Estado.NAVEGANDO, Estado.CONFIRMANDO_LLEGADA):
            self._hablar("Ahora no hay ninguna ruta en curso.")
            return
        indice = self._indice_zona - 1
        if indice < 0 or indice >= len(self._camino):
            self._hablar(f"Antes de {self.ubicacion} no hay otra zona en la ruta.")
            return
        # El plan se recalcula desde la ubicacion real (no desde donde
        # el contador de pasos cree que esta la persona).
        self._asignar_destino(self._camino[indice])

    def _otra_ruta(self) -> None:
        """Orden de recalcular la ruta hacia el mismo destino actual."""
        if self.estado not in (Estado.NAVEGANDO, Estado.CONFIRMANDO_LLEGADA):
            self._hablar("Ahora no hay ninguna ruta en curso.")
            return
        if self.destino is None:
            self._hablar("No hay ningún destino en curso.")
            return
        self._asignar_destino(self.destino)

    # ------------------------------------------------------------------
    # Destino y navegacion
    # ------------------------------------------------------------------
    def _zancada_para_plan(self) -> float | None:
        """Zancada que usa el planificador al calcular los pasos.

        ``None`` mientras no haya ningun tramo cerrado por la camara:
        entonces mandan los pasos del mapa (el conteo con el que se
        midio la casa).  En cuanto se mide la primera, manda esa
        zancada (EMA, ver :class:`Zancada`).
        """
        return self.zancada.metros if self.zancada.aprendida else None

    def _asignar_destino(self, destino: str) -> None:
        """Valida el destino pedido y arranca la ruta si existe.

        Si el destino no sirve (es la ubicacion actual, la zona es
        desconocida o no hay ruta), se vuelve a preguntar a donde
        quiere ir: antes se volvia al modo despertador y la siguiente
        indicacion se quedaba sin reconocer.
        """
        self.destino = destino
        print("DESTINO:", destino)

        if destino == self.ubicacion:
            self._volver_a_preguntar(f"Ya estás en {destino}.")
        elif self.ubicacion is None:
            self._volver_a_preguntar(
                "Todavía no sé en qué zona estamos. "
                "Acércate a una señal orientadora."
            )
        elif self.lado_puerta == LADO_PASILLO:
            # Los tramos del mapa se midieron de frente a la senal
            # interior: del lado del pasillo los giros saldrian dados
            # al reves.  Primero hay que cruzar la puerta.
            self._volver_a_preguntar(
                f"Estás del lado del pasillo de la puerta de "
                f"{self.ubicacion}. Cruza hacia dentro y vuelve a "
                "pedir la ruta."
            )
        else:
            plan = self._planificador.planificar(
                self.ubicacion, destino, self._zancada_para_plan()
            )
            if plan is None:
                self._volver_a_preguntar(
                    f"La ruta hacia {destino} todavía no está configurada."
                )
            else:
                self._hablar(f"Entendido. Vamos a {destino}.")
                self.microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)
                self._iniciar_encarada(plan)

    def _senal_enfrente_reciente(self) -> bool:
        """¿La senal interior de la zona actual sigue estando enfrente?

        Cierto si se confirmo hace poco (la busqueda o la espera del
        destino, ver ``encarada_validez_s``) y el rumbo no ha girado
        mas alla de ``encarada_max_giro_grados`` desde ese anclaje:
        entonces la orientacion de referencia sigue vigente y no hace
        falta pedir encarar.
        """
        if self._senal_enfrente is None:
            return False
        zona, instante = self._senal_enfrente
        return (
            zona == self.ubicacion
            and time.monotonic() - instante <= self.config.encarada_validez_s
            and abs(self.rumbo.grados) <= self.config.encarada_max_giro_grados
        )

    def _iniciar_encarada(self, plan: list[Instruccion]) -> None:
        """Prepara la ruta: antes hay que encarar la senal de la zona.

        Las instrucciones del mapa se dan "de frente a la senal
        interior de la puerta" (asi se midieron los pasos).  Si la
        camara no la ve ahora, se pide girar despacio hasta
        encontrarla antes de arrancar.

        Excepcion: si la senal de la zona se acaba de confirmar y la
        persona no ha girado desde entonces, ya la tiene enfrente y
        la ruta arranca directo (ver ``_senal_enfrente_reciente``):
        pedirla otra vez la dejaria girando sin salida, porque las
        senales de las demas zonas estan a pasos de distancia.
        """
        if self._senal_enfrente_reciente():
            print(
                "ENCARADA AUTOMATICA: la senal de",
                self.ubicacion,
                "sigue enfrente",
            )
            # Se da por encarada y se ancla: la tolerancia de giro
            # acota el error con el que se afirma "la tienes enfrente".
            self.rumbo.anclar()
            self._iniciar_navegacion(plan)
            return
        self._ruta_pendiente = plan
        self._instruccion_encarar_dada = False
        # De que lado girar para encarar la senal: las imagenes estan
        # a la derecha del pasillo en el sentido en que se midio la
        # casa, y en el viaje de vuelta le quedan al otro lado.
        self._lado_encarada = self._planificador.lado_encarada(
            self.ubicacion, self.destino
        )
        self.estado = Estado.ENCARAR_SENAL

    def _encarar_zona_alcanzada(self) -> None:
        """Encara la senal de la zona alcanzada al terminar un tramo.

        Los pasos del tramo dejan a la persona en la siguiente zona,
        normalmente mirando hacia donde vino (al terminar el giro del
        tramo anterior y corregir el desvio).  Como las senales van
        del lado derecho del pasillo, hay que decirle que gire hasta
        verla ANTES del giro del siguiente tramo: si no, el giro de
        la ruta se daria contra una orientacion que no es la medida.

        No hay plan nuevo que cargar (``_ruta_pendiente`` queda en
        ``None``): la ruta en curso sigue donde estaba y al confirmar
        la senal se retoma desde la instruccion siguiente.
        """
        if self._camino:
            indice = min(self._tramos_completados, len(self._camino) - 1)
            self.ubicacion = self._camino[indice]
            self._indice_zona = indice
        self._ruta_pendiente = None
        self._instruccion_encarar_dada = False
        # El tramo anterior ya quedo acreditado: si la senal de la zona
        # sigue a la vista al retomar, _seguir_senal no debe leer esos
        # pasos como "se devolvio" y replanificar sin mas.
        self._pasos_acreditados = 0
        self._pasos_previos = 0
        self._lado_encarada = self._planificador.lado_encarada(
            self.ubicacion, self.destino
        )
        self.estado = Estado.ENCARAR_SENAL

    def _en_encarar_senal(self, resultado: ResultadoVision) -> None:
        """Arranca la ruta al confirmar la senal interior enfrente.

        Solo sirve como referencia la senal de LA zona actual y por su
        cara interior: la de otra zona, o la del lado del pasillo,
        no indican el frente medido y se avisa sin arrancar.
        """
        senal = resultado.senal
        if senal is not None:
            if senal.zona != self.ubicacion:
                # Senal de otra zona: no vale como "frente" (antes se
                # anclaba el rumbo con cualquier senal y el resto de
                # la ruta salia orientada al reves).
                self._advertecer(
                    f"otra:{senal.zona}",
                    f"Veo la señal de {senal.zona}, pero tu zona es "
                    f"{self.ubicacion}. Gira despacio a la "
                    f"{self._lado_encarada} hasta encontrar "
                    "la señal de tu zona.",
                )
            elif senal.lado != LADO_INTERIOR:
                # Misma zona pero al otro lado de la puerta.
                self.lado_puerta = senal.lado
                self._advertecer(
                    f"pasillo:{senal.zona}",
                    f"Estás del lado del pasillo de la puerta de "
                    f"{senal.zona}. Cruza hacia dentro para comenzar.",
                )
            else:
                self.lado_puerta = senal.lado
                self.rumbo.anclar()
                self._senal_enfrente = (senal.zona, time.monotonic())
                print("RUMBO ANCLADO: señal de", senal.zona, "enfrente")
                plan = self._ruta_pendiente
                self._ruta_pendiente = None
                if self._instruccion_encarar_dada:
                    self._hablar("La señal está frente a ti.")
                if plan is None:
                    # Encarada intermedia (fin de tramo): no hay plan
                    # nuevo; la ruta en curso sigue y el giro del
                    # siguiente tramo espera en el indice.
                    self.estado = Estado.NAVEGANDO
                else:
                    self._iniciar_navegacion(plan)
                return

        self._etiqueta = "Encarando la senal orientadora"
        if not self._instruccion_encarar_dada:
            self._instruccion_encarar_dada = True
            self._hablar(
                "Para saber hacia dónde miras, primero encárate con la "
                "señal de esta zona. Gira despacio a la "
                f"{self._lado_encarada} hasta que te diga que la tienes "
                "frente a ti."
            )

    def _volver_a_preguntar(self, motivo: str) -> None:
        """Explica el problema y vuelve a pedir el destino."""
        self.destino = None
        self._hablar(motivo)
        self._hablar("¿A dónde quieres ir?", cambiar_a=ModoMicrofono.DESTINO)

    def _iniciar_navegacion(self, plan: list[Instruccion]) -> None:
        self._reiniciar_plan(plan)

    def _reiniciar_plan(self, plan: list[Instruccion]) -> None:
        """Carga un plan nuevo hacia el destino desde la ubicacion actual."""
        self._plan = plan
        self._indice_plan = 0
        self._instruccion_actual = None
        self._en_pausa = False
        self._pasos_acreditados = 0
        self._pasos_previos = 0
        self._zona_cierre_tramo = None
        self._aviso_ventana_dado = False
        # Zonas del recorrido: la primera es donde se parte, y cada
        # senal interior confirmada va avanzando por la lista.
        self._camino = self._planificador.camino(self.ubicacion, self.destino) or []
        self._indice_zona = 0
        self._tramos_completados = 0
        self._falta_encarar = False
        self.estado = Estado.NAVEGANDO

    def _seguir_senal(self, resultado: ResultadoVision) -> bool:
        """Corrige ubicacion y rumbo con lo que se ve en el camino.

        El plan es una estimacion a pasos con deriva posible: cada
        senal interior confirmada dice donde esta la persona de
        verdad.  Si es el destino, la llegada queda verificada; si
        esta en la ruta, avanza el indice de zona; si esta detras o
        fuera de la ruta (se devolvio o tomo otro camino), se
        replanifica en vez de seguir instrucciones viejas.
        Devuelve ``True`` si el frame quedo resuelto.
        """
        senal = resultado.senal
        if senal is None:
            return False

        if senal.lado != LADO_INTERIOR:
            # Cara del pasillo: solo orienta.  La del destino avisa
            # que la puerta esta aqui y que hay que entrar.
            if senal.zona == self.destino:
                self._advertecer(
                    f"pasillo:{senal.zona}",
                    f"Estás frente a la puerta de {self.destino}, del "
                    "lado del pasillo. Entra hacia dentro.",
                )
            else:
                self._advertecer(
                    f"pasillo:{senal.zona}",
                    f"Frente a ti está la puerta de {senal.zona}, del "
                    "lado del pasillo.",
                )
            return False

        if senal.zona == self.destino and not self._senal_en_rango_de_llegada(
            resultado, senal
        ):
            self._pedir_acercamiento_a_senal(resultado, senal)
            return False

        if (
            senal.zona == self._zona_cierre_tramo
            and not self._senal_en_rango_de_llegada(resultado, senal)
        ):
            self._pedir_acercamiento_a_senal(resultado, senal)
            return False

        # Senal interior: la persona esta en esa zona (o muy cerca de
        # su puerta) y mas o menos de frente: rumbo de referencia.
        self.rumbo.anclar()
        self._rumbo_al_caminar = self.rumbo.grados
        self.ubicacion = senal.zona
        self.lado_puerta = senal.lado

        if senal.zona == self.destino:
            print("LLEGADA verificada:", senal.zona)
            self._anunciar_llegada()
            return True

        indice = (
            self._camino.index(senal.zona) if senal.zona in self._camino else -1
        )

        if indice == self._indice_zona:
            # Sigue en la zona de la que partio el tramo: si aun no
            # habia avanzado, es solo un reanclaje (arranco viendo su
            # propia puerta); si ya hay pasos dados, se devolvio.
            if self._pasos_acreditados == 0:
                return False
        elif indice == self._indice_zona + 1:
            # Avance normal: acaba de entrar en la siguiente zona.
            self._indice_zona = indice
            print("AVANCE de ruta: ahora en", senal.zona)
            return False

        # Detras del plan o fuera de el: el tramo ya no sirve.
        if senal.zona == self._zona_cierre_tramo:
            # La senal de la zona que CIERRA el tramo (la siguiente
            # del camino, hacia la que se camina) no es una
            # desviacion: la puerta se ve desde la recta del tramo y
            # puede repetirse en frames seguidos.  Replanificar aqui
            # mandaria "Te saliste de la ruta" sin haberse salido.
            return False
        self._indice_zona = max(indice, 0)
        self._replanificar_desde()
        return True

    def _replanificar_desde(self) -> None:
        """Rehace la ruta desde la ubicacion real (la persona se desvio)."""
        destino = self.destino
        plan = self._planificador.planificar(
            self.ubicacion, destino, self._zancada_para_plan()
        )
        self._instruccion_actual = None  # el tramo en curso se descarta
        if plan is None:
            self._hablar(
                f"Estás en {self.ubicacion} y no hay ruta hacia {destino}."
            )
            self._abandonar_ruta()
            return
        self._hablar(
            f"Te saliste de la ruta hacia {destino}. Estás en "
            f"{self.ubicacion}. Vamos de nuevo."
        )
        self._reiniciar_plan(plan)

    def _abandonar_ruta(self) -> None:
        """Deja la navegacion sin mentir y vuelve a pedir destino."""
        self.destino = None
        self._plan = []
        self._indice_plan = 0
        self._instruccion_actual = None
        self.estado = Estado.ESPERANDO_DESTINO
        self._hablar("¿A dónde quieres ir?", cambiar_a=ModoMicrofono.DESTINO)

    def _empezar_a_confirmar_llegada(self) -> None:
        """Tras los pasos medidos, falta VER la senal del destino.

        Los pasos se acreditan en linea recta, pero la deriva puede
        dejar a la persona a un lado de la puerta: anunciar la
        llegada solo por el contador seria dar una indicacion errada.
        """
        self.estado = Estado.CONFIRMANDO_LLEGADA
        self._inicio_llegada = time.monotonic()
        # La puerta del destino esta en el mismo lado que las demas:
        # en el sentido del viaje la imagen se encara a la derecha,
        # y en el de vuelta a la izquierda.
        lado = self._planificador.lado_encarada(self.ubicacion, self.destino)
        self._hablar(
            f"Para confirmar la llegada, gira despacio a la {lado} hasta "
            f"encontrar la señal de {self.destino}."
        )

    def _en_confirmando_llegada(self, resultado: ResultadoVision) -> None:
        """Solo anuncia la llegada si ve la senal interior del destino."""
        self._etiqueta = f"Confirmando llegada a {self.destino}"
        senal = resultado.senal

        if senal is not None and senal.zona == self.destino:
            if senal.lado == LADO_INTERIOR:
                if not self._senal_en_rango_de_llegada(resultado, senal):
                    self._pedir_acercamiento_a_senal(resultado, senal)
                    return
                print("LLEGADA verificada:", senal.zona)
                self._anunciar_llegada()
                return
            # La puerta del destino esta aqui, pero del lado del
            # pasillo: que cruce hacia dentro para confirmar.
            self.lado_puerta = senal.lado
            self._advertecer(
                f"pasillo:{senal.zona}",
                f"Estás frente a la puerta de {self.destino}, pero del "
                "lado del pasillo. Entra hacia dentro.",
            )
            return

        if senal is not None and senal.lado == LADO_INTERIOR:
            # Senal de otra zona: se paso de largo.
            print("LLEGADA falsa: la senal vista es de", senal.zona)
            self.ubicacion = senal.zona
            self.lado_puerta = senal.lado
            self.rumbo.anclar()
            self._hablar(f"Parece que pasaste de largo. Estás en {senal.zona}.")
            self._abandonar_ruta()
            return

        # Sin senal del destino todavia: sin anuncio (para no mentir),
        # pero con un recordatorio periodico por si quedo a un lado.
        if (
            time.monotonic() - self._inicio_llegada
            >= self.config.tiempo_llegada_s
        ):
            self._inicio_llegada = time.monotonic()
            lado = self._planificador.lado_encarada(
                self.ubicacion, self.destino
            )
            self._hablar(
                f"Todavía no veo la señal de {self.destino}. Gira "
                f"despacio a la {lado} hasta encontrarla."
            )

    def _anunciar_llegada(self) -> None:
        """Da la llegada por VERIFICADA: se vio la senal del destino."""
        destino = self.destino
        self.ubicacion = destino
        self.lado_puerta = LADO_INTERIOR
        self.destino = None
        self._plan = []
        self._indice_plan = 0
        self._camino = []
        self._indice_zona = 0
        self._instruccion_actual = None
        self.estado = Estado.ESPERANDO_DESTINO

        self._hablar(f"Has llegado a {destino}.")
        self._hablar("¿A dónde quieres ir?", cambiar_a=ModoMicrofono.DESTINO)

    # ------------------------------------------------------------------
    # Obstaculos y seguridad
    # ------------------------------------------------------------------
    def _anunciar_obstaculos(
        self, resultado: ResultadoVision, distancia_cm: float | None
    ) -> None:
        """Nombra lo que esta EN MEDIO del camino, esperando su turno.

        La voz es UN solo canal y la navegacion manda: ``_hablar``
        bloquea el bucle hasta que termina de sonar, asi que si los
        obstaculos hablaban antes que las instrucciones (iban en la
        misma vuelta del bucle) las indicaciones de la ruta se
        atrasaban o se "cortaban" a mitad de frase.  Por eso AQUI se
        mira primero si la voz estuvo callada
        ``silencio_para_obstaculos_s``: si no, no se dice nada y el
        aviso se intenta en la proxima vuelta (el objeto sigue a la
        vista, asi que no se pierde).

        Ademas solo se anuncia lo que puede estar en la DIRECCION que
        se recorre:

        * SOLO EN LA NAVEGACION: mientras la persona camina la ruta.
          Girando sobre su propio eje (giro inicial, encarada, media
          vuelta) o esperando la senal, la camara no anuncia nada:
          estaria nombrando cosas que el giro deja a un lado y solo
          llena la voz.  Los pitidos del ultrasonido no dependen de
          aqui: siguen sonando por seguridad;
        * en medio del camino Y en ``obstaculos_en_voz``: los lados no
          se dicen (``anunciar_lados``) y "persona" tampoco, porque no
          bloquean el paso y llenaban la voz de avisos que hacian
          detenerse sin motivo;
        * una sola vez por encuentro: mientras siga a la vista no se
          repite y el aviso se re-arma cuando el frame queda limpio
          ``enfriamiento_objeto_s`` (un parpadeo de YOLO no lo repite,
          un objeto nuevo si se anuncia);
        * si el eco entra en rango con el objeto ya nombrado, la frase
          sale con la distancia (los pitidos quedan explicados);
        * solo ultrasonido (algo que la camara no nombra): al entrar
          en rango se explica una sola vez que los pitidos significan
          "hay algo enfrente".
        """
        # SOLO EN LA NAVEGACION: fuera de ella (girando sobre su
        # propio eje para buscar la senal, encarando, esperando) no
        # se anuncia obstaculo alguno.  Los pitidos del ultrasonido
        # siguen por su cuenta: esto apaga a la CAMARA.
        if self.estado not in _ESTADOS_DE_MARCHA:
            return

        ahora = time.monotonic()

        # PRIORIDAD DE LA NAVEGACION: la guia acabo de hablar (giro,
        # pasos, llegada, encarada, caida...) -> el obstaculo espera
        # su turno.  Nunca al reves: un aviso jamas atrasa una
        # indicacion de la ruta.
        if (
            ahora - self._ultima_voz_en
            < self.config.silencio_para_obstaculos_s
        ):
            return

        # MARCHA: solo cuenta el fragmento central de la imagen (aqui
        # solo se llega con la navegacion activa).  Los objetos de
        # los otros dos fragmentos se descartan por completo: no
        # estan en la direccion que se recorre y sus avisos solo
        # generan confusion.
        solo_centro = self.config.mirar_solo_centro_en_marcha

        # Lo que se ve AHORA en el frame, separando lo que SI se puede
        # anunciar (en medio y en la lista de voz) de lo que solo se
        # reconoce (los lados).
        en_medio: list[str] = []
        lados: dict[str, list[str]] = {}
        for obstaculo in resultado.obstaculos:
            if solo_centro and obstaculo.lado != EN_MEDIO_DEL_CAMINO:
                # Fragmento izquierdo o derecho: fuera, sin contar
                # para nada (ni anuncio ni re-armado de lados).
                continue
            nombre = obstaculo.nombre
            if obstaculo.lado == EN_MEDIO_DEL_CAMINO:
                if nombre in self.config.obstaculos_en_voz and nombre not in en_medio:
                    en_medio.append(nombre)
            else:
                nombres = lados.setdefault(obstaculo.lado, [])
                if nombre not in nombres:
                    nombres.append(nombre)
        lados_vistos = tuple(
            f"{lado}:{nombre}"
            for lado, nombres in lados.items()
            for nombre in nombres
        )

        cerca = (
            distancia_cm is not None
            and distancia_cm <= self.config.distancia_alerta_cm
        )
        entrada_eco = cerca and not self._cerca_antes

        # Re-arma: si el frame queda sin nada del lado correspondiente
        # durante el enfriamiento, el proximo encuentro vuelve a
        # anunciar.
        if en_medio:
            self._medio_libre_desde = None
        elif self._medio_libre_desde is None:
            self._medio_libre_desde = ahora
        elif ahora - self._medio_libre_desde >= self.config.enfriamiento_objeto_s:
            self._medio_anunciado = ()
            self._medio_libre_desde = None

        if lados_vistos:
            self._lado_libre_desde = None
        elif self._lado_libre_desde is None:
            self._lado_libre_desde = ahora
        elif ahora - self._lado_libre_desde >= self.config.enfriamiento_objeto_s:
            self._lado_anunciado = ()
            self._lado_libre_desde = None

        # En medio: se anuncia si aparece algo nuevo, o si el eco
        # entra en rango con el objeto ya nombrado (entonces la frase
        # se da con la distancia y los pitidos quedan explicados).
        nuevos = [n for n in en_medio if n not in self._medio_anunciado]
        if nuevos or (entrada_eco and en_medio):
            articulo = "un obstáculo" if len(en_medio) == 1 else "obstáculos"
            nombres = _unir_nombres(en_medio)
            if cerca:
                # La camara y el eco hablan del mismo obstaculo: una
                # sola frase con nombre y distancia.
                self._hablar(
                    f"Alto, en medio del camino hay {articulo}: "
                    f"{nombres}, {self._distancia_en_palabras(distancia_cm)}."
                )
            else:
                self._hablar(
                    f"Alto, en medio del camino hay {articulo}: {nombres}."
                )
            self._medio_anunciado = tuple(en_medio)
            self._ultimo_anuncio_obstaculo = ahora
            self._registrar_alerta_obstaculo()

        # Los lados: por defecto NO se anuncian (no estan en la
        # direccion que se recorre y los avisos de al lado hacian
        # detenerse sin motivo).  Con ``anunciar_lados`` se dicen en
        # una sola frase SIN "Alto": informan pero no frenan el paso.
        if self.config.anunciar_lados and any(
            clave not in self._lado_anunciado for clave in lados_vistos
        ):
            partes = [
                f"a tu {lado}: {_unir_nombres(nombres)}"
                for lado, nombres in lados.items()
            ]
            if len(partes) == 1:
                frase = f"Hay un obstáculo {partes[0]}."
            else:
                frase = "Hay obstáculos " + ", ".join(partes) + "."
            self._hablar(frase)
            self._lado_anunciado = lados_vistos
            self._registrar_alerta_obstaculo()

        # El ultrasonido pitia sin que la camara nombre nada: explica
        # los pitidos una sola vez al entrar en el rango de alerta.
        # Si la camara acaba de anunciar algo es el mismo obstaculo y
        # la frase no se repite.
        if cerca and not self._cerca_antes:
            if (
                ahora - self._ultimo_anuncio_obstaculo
                >= self.config.enfriamiento_objeto_s
            ):
                self._advertecer(
                    "eco",
                    "Hay algo enfrente. Los pitidos indican qué tan "
                    "cerca está.",
                )
                self._registrar_alerta_obstaculo()
        self._cerca_antes = cerca

    def _registrar_alerta_obstaculo(self) -> None:
        self._ultima_alerta_obstaculo_en = time.monotonic()

    @staticmethod
    def _distancia_en_palabras(distancia_cm: float) -> str:
        """'a unos 45 centimetros' por debajo de 1 m, 'a unos 1.5 metros'."""
        if distancia_cm < 100.0:
            return f"a unos {int(round(distancia_cm))} centímetros"
        return f"a unos {distancia_cm / 100.0:.1f} metros"

    def _vigilar_caida(self) -> None:
        """Si el acelerometro detecto una caida, alarma constante + voz.

        El beep de la caida es continuo (no intermitente) y tiene
        prioridad: se apaga con la orden "silencia" o solo.
        """
        if not self.caida.hay_caida:
            return

        self.caida.reconocer()
        self.alarma.activar_alarma(self.config.duracion_alarma_caida_s)
        self._hablar(
            "Caida detectada. Di silencia cuando te hayas levantado.",
        )
        # Con el tono sonando, la gramatica se reduce a la orden que lo
        # apaga: se reconoce sin palabras clave y mucho mas rapido.
        self.microfono.cambiar_modo(ModoMicrofono.SILENCIO)

    def _mostrar_giro(self, sentido: str, angulo: float, objetivo: float) -> None:
        """Etiqueta de diagnostico y aviso de consola del giro.

        La consola muestra los grados netos que faltan del giro pedido y
        la velocidad de giro actual (positiva = derecha).  Si al girar
        a la derecha la velocidad sale negativa, el ``signo_derecha``
        esta invertido; si los grados salen NEGATIVOS, la persona se
        esta girando al lado contrario y esos grados se descuentan.

        Durante el giro NO se habla: los avisos al 50% y al 75%
        ("Vas por la mitad", "Ya casi listo") tardaban mas que la
        persona en girar, asi que cuando terminaba de decir la frase ya
        habia girado mas de lo pedido y el giro se deshacia.  La unica
        voz del giro es la frase final con la orden de parar.
        """
        self._etiqueta = (
            f"Giro {sentido}: {angulo:.1f}/{objetivo} grados"
            f"{' (al lado contrario)' if angulo < 0.0 else ''}"
        )
        ahora = time.monotonic()
        if ahora - self._ultimo_aviso_giro >= AVISO_GIRO_S:
            self._ultimo_aviso_giro = ahora
            velocidad = self.giro.velocidad_derecha
            print(
                f"GIRO {sentido}: {angulo:.1f}/{objetivo} grados"
                f" | vel {velocidad:+.1f} dps"
            )

    # ------------------------------------------------------------------
    # Manejadores de estado
    # ------------------------------------------------------------------
    def _iniciar_busqueda(self, intento: int) -> None:
        self._intento = intento
        self.estado = Estado.BUSCAR_SENAL
        self.detector.reiniciar_confirmacion()
        self._inicio_busqueda = time.monotonic()
        self._hablar("Buscando la señal durante un minuto.")

    def _en_giro_inicial(self, resultado: ResultadoVision) -> None:
        objetivo = self.config.grados_giro_inicial
        angulo = self.giro.avanzar(derecha=True)
        self._mostrar_giro("derecha", angulo, objetivo)

        # El corte se adelanta ``grados_compensacion_giro``: mientras
        # suena la frase final la persona sigue girando (y luego
        # viene "Buscando la señal...", que tambien bloquea), asi que
        # cortar en el objetivo exacto dejaba el giro ~10 grados
        # pasado.  "Puedes parar" va PRIMERO en la frase: es la orden
        # y cuanto antes se oiga, antes se deja de girar.
        if angulo >= objetivo - self.config.grados_compensacion_giro:
            self._hablar(f"Puedes parar. Ya giraste {objetivo:.0f} grados.")
            self._iniciar_busqueda(intento=1)

    def _en_buscar_senal(self, resultado: ResultadoVision) -> None:
        self._etiqueta = f"Buscando senal - intento {self._intento}"

        senal = resultado.senal
        self._guiar_con_puerta(resultado)
        if senal is not None:
            self.rumbo.anclar()  # se mira la senal: rumbo conocido
            if senal.lado == LADO_INTERIOR:
                # La cara interior confirma que la persona esta en la
                # zona (los tramos de ruta se midieron desde ahi).
                self.ubicacion = senal.zona
                self.lado_puerta = senal.lado
                # Acaba de confirmar donde esta: si pide el destino
                # sin moverse, la encarada queda resuelta.
                self._senal_enfrente = (senal.zona, time.monotonic())
                print("SEÑAL ENCONTRADA:", self.ubicacion)
                self._hablar(f"Estás en {self.ubicacion}.")
                self.estado = Estado.ESPERANDO_DESTINO
                self._hablar("¿A dónde quieres ir?", cambiar_a=ModoMicrofono.DESTINO)
                return
            # Cara del pasillo: la persona esta al otro lado de la
            # puerta, no dentro de la zona (antes cualquier senal
            # fijaba la ubicacion y la ruta partia de un origen falso).
            print("PUERTA vista del lado del pasillo:", senal.zona)
            self._advertecer(
                f"pasillo:{senal.zona}",
                f"Frente a ti está la puerta de {senal.zona}, pero "
                "estás del lado del pasillo. Entra por ella o acércate "
                "a otra señal para saber dónde estás.",
            )
            return

        agotado = (
            time.monotonic() - self._inicio_busqueda
            >= self.config.tiempo_busqueda_s
        )
        if not agotado:
            return

        if self._intento < MAX_INTENTOS_DESDE_GIRO:
            # Primera vez sin senal: media vuelta y volver a mirar.
            self.estado = Estado.MEDIA_VUELTA
            self.giro.reiniciar()
            self._hablar(
                "No encontré la señal. Da media vuelta a la izquierda suavemente."
            )
        else:
            # Segunda vez sin senal: cambiar de lugar y reintentar.
            self.estado = Estado.CINCO_PASOS
            self._empezar_a_caminar()
            self._hablar(
                f"No encontré la señal. Camina {self.config.pasos_objetivo} pasos."
            )

    def _en_media_vuelta(self, resultado: ResultadoVision) -> None:
        objetivo = self.config.grados_media_vuelta
        angulo = self.giro.avanzar(derecha=False)
        self._mostrar_giro("izquierda", angulo, objetivo)

        # Igual que en el giro inicial: el corte se adelanta para
        # absorber el giro que se da mientras suena la frase.
        if angulo >= objetivo - self.config.grados_compensacion_giro:
            self._hablar(f"Puedes parar. Ya giraste {objetivo:.0f} grados.")
            self._iniciar_busqueda(intento=2)

    def _en_cinco_pasos(self, resultado: ResultadoVision) -> None:
        objetivo = self.config.pasos_objetivo
        dados = self._creditar_pasos(resultado)
        self._etiqueta = f"Caminando - {dados}/{objetivo} pasos"
        self._guiar_con_puerta(resultado)
        inicio = objetivo * self.config.ventana_llegada_inicio
        fin = objetivo * self.config.ventana_llegada_fin

        # Mientras camina tambien mira la camara: dentro de la
        # ventana, cualquier senal resuelve la busqueda (dice en que
        # zona esta la persona); fuera de ella se espera llegar al
        # tope de la ventana.
        if dados >= inicio and resultado.senal is not None:
            self._en_buscar_senal(resultado)
            return

        if dados >= fin:
            self._hablar(
                f"{objetivo} pasos completados. Puedes parar. Gira "
                f"{self.config.grados_giro_inicial} grados a la derecha "
                "suavemente para buscar la señal."
            )
            self.giro.reiniciar()
            self.estado = Estado.GIRO_INICIAL
            return

        self._vigilar_desvio(resultado)

    def _en_esperando_destino(self, resultado: ResultadoVision) -> None:
        self._etiqueta = "Esperando destino"
        senal = resultado.senal
        if (
            senal is not None
            and senal.lado == LADO_INTERIOR
            and senal.zona == self.ubicacion
        ):
            # La senal de la zona sigue enfrente mientras la persona
            # piensa el destino: el rumbo se reancla (se mira la
            # senal) y queda registrada como "todavia frente" para
            # la encarada posterior (ver _iniciar_encarada).
            self.rumbo.anclar()
            self._senal_enfrente = (senal.zona, time.monotonic())

    def _en_navegando(self, resultado: ResultadoVision) -> None:
        if self._en_pausa:
            self._etiqueta = "Navegacion en pausa"
            return

        # Las senales del camino corrigen la ubicacion real: si la
        # persona se devolvio o tomo otro camino, se replanifica en
        # vez de seguir instrucciones viejas (o se anuncia la llegada
        # si la senal del destino esta a la vista).
        if self._seguir_senal(resultado):
            return

        if self._instruccion_actual is None:
            if self._indice_plan >= len(self._plan):
                self._empezar_a_confirmar_llegada()
                return

            siguiente = self._plan[self._indice_plan]
            if self._falta_encarar and siguiente.es_giro:
                # Acaba de terminar un tramo y quedan mas: la persona
                # esta de espalda a la senal de la zona alcanzada (las
                # imagenes van del lado derecho del pasillo), asi que
                # antes del giro del siguiente tramo hay que encararla.
                self._falta_encarar = False
                self._encarar_zona_alcanzada()
                return

            self._falta_encarar = False
            self._instruccion_actual = siguiente
            self._indice_plan += 1

            if self._instruccion_actual.accion is Accion.LLEGADA:
                self._empezar_a_confirmar_llegada()
                return

            self._anunciar_instruccion(self._instruccion_actual)
            if self._instruccion_actual.es_giro:
                self.giro.reiniciar()
            else:
                self._empezar_a_caminar()

        instruccion = self._instruccion_actual

        if instruccion.es_giro:
            derecha = instruccion.accion is Accion.GIRAR_DERECHA
            sentido = "derecha" if derecha else "izquierda"
            angulo = self.giro.avanzar(derecha=derecha)
            self._mostrar_giro(sentido, angulo, float(instruccion.cantidad))
            # Mismo adelanto que en los giros de la busqueda: la
            # persona sigue girando mientras suena la frase y la
            # instruccion siguiente, asi que el corte va antes del
            # objetivo y "Puedes parar" sale primero.
            if angulo >= (
                float(instruccion.cantidad)
                - self.config.grados_compensacion_giro
            ):
                self._hablar("Puedes parar. Giro completo.")
                self._instruccion_actual = None

        elif instruccion.es_pasos:
            dados = self._creditar_pasos(resultado)
            self._etiqueta = f"Caminando: {dados}/{instruccion.cantidad} pasos"
            self._actualizar_odometro_ultrasonico(instruccion)
            self._actualizar_posicion_tramo(resultado, instruccion, dados)
            self._valorar_llegada_del_tramo(
                resultado, dados, instruccion.cantidad
            )
            if self._instruccion_actual is None:
                return
            if self._deteccion_senal_cierre(resultado) is not None:
                return
            self._guiar_con_senal(resultado)
            self._vigilar_desvio(resultado)
            if (
                self._instruccion_actual is not None
                and self._aviso_ventana_dado
                and resultado.senal is None
            ):
                self._guiar_con_puerta(resultado)

    def _valorar_llegada_del_tramo(
        self, resultado: ResultadoVision, dados: int, objetivo: int
    ) -> None:
        """Ventana de llegada del tramo: manda la camara, no el contador.

        Los pasos son un metodo de lazo abierto (la zancada cambia y el
        error se acumula), asi que el tramo ya NO se da por terminado
        exacto en "N pasos".  Quien verifica que termino es la camara
        con la senal interior de la zona de cierre (la siguiente del
        camino):

        * antes del ``ventana_llegada_inicio`` se anticipa una sola vez
          el aviso para reducir el paso, estimando reacción y frenado;
          el adelanto está acotado y no cierra el tramo;
        * entre el 70 % y el ``ventana_llegada_fin`` (130 %) esa
          lectura da el tramo por completado (re-localizacion
          verificada: nunca se anuncia sin verla);
        * pasados los 130 % sin senal, los tramos intermedios siguen
          el flujo de siempre. En el tramo final, si el ultrasonido
          estable indica que aun esta lejos, se mantiene la marcha
          lenta hasta el rango de puerta y entonces se busca la senal.
        """
        fraccion_pasos = dados / objetivo if objetivo > 0 else 0.0
        fraccion_posicion = (
            self.estimador_posicion.posicion_m / self._distancia_tramo
            if self._distancia_tramo > 0
            else 0.0
        )
        fraccion_avance = max(fraccion_pasos, fraccion_posicion)
        inicio = self.config.ventana_llegada_inicio
        fin = self.config.ventana_llegada_fin
        senal = resultado.senal

        if (
            senal is not None
            and senal.lado == LADO_INTERIOR
            and senal.zona == self._zona_cierre_tramo
            and not self._senal_en_rango_de_llegada(resultado, senal)
        ):
            self._pedir_acercamiento_a_senal(resultado, senal)
            return

        if (
            fraccion_avance >= inicio
            and senal is not None
            and senal.lado == LADO_INTERIOR
            and senal.zona == self._zona_cierre_tramo
        ):
            print(
                f"TRAMO cerrado por camara: senal de {senal.zona} vista "
                f"con {dados} pasos acreditados"
            )
            self._hablar("Ya veo la señal. Puedes parar.")
            self._aprender_zancada(dados)
            self._cerrar_tramo(dados)
            return

        umbral_aviso = self._umbral_aviso_llegada()
        if fraccion_avance >= umbral_aviso and not self._aviso_ventana_dado:
            self._aviso_ventana_dado = True
            self._hablar(
                "Reduce el paso; te indicaré dónde detenerte."
            )

        tiempo_parado = self.pasos.segundos_desde_ultimo_paso
        detenido_en_meta = dados >= objetivo and tiempo_parado >= 2.5
        if fraccion_avance >= fin or detenido_en_meta:
            if (
                senal is not None
                and senal.lado == LADO_INTERIOR
                and senal.zona == self.destino
                and not self._senal_en_rango_de_llegada(resultado, senal)
            ):
                self._pedir_acercamiento_a_senal(resultado, senal)
                return
            if (
                self._zona_cierre_tramo == self.destino
                and senal is None
                and self._distancia_puerta_estable_cm is not None
            ):
                if any(
                    obstaculo.lado == EN_MEDIO_DEL_CAMINO
                    for obstaculo in resultado.obstaculos
                ):
                    return
                if (
                    self._distancia_puerta_estable_cm
                    > self.config.distancia_puerta_max_cm
                ):
                    self._advertecer(
                        f"busqueda-distancia:{self.destino}",
                        "Aún no veo la señal. Avanza despacio hacia la puerta; "
                        "te indicaré cuándo detenerte para buscarla.",
                    )
                    return
                if (
                    self._distancia_puerta_estable_cm
                    < self.config.distancia_puerta_min_cm
                ):
                    self._hablar(
                        "Detente. Estás a menos de "
                        f"{self.config.distancia_puerta_min_cm:.0f} "
                        "centímetros. Retrocede despacio y buscaré la señal."
                    )
                    self._empezar_a_confirmar_llegada()
                    return
                self._hablar(
                    "Detente. Estás cerca de la puerta. Buscaré la señal "
                    f"interior de {self.destino}."
                )
                self._empezar_a_confirmar_llegada()
                return
            print(
                f"TRAMO: {dados} pasos acreditados de {objetivo} "
                "esperados sin ver la senal de cierre"
            )
            self._hablar(
                "Detente. Ya recorriste los pasos previstos, pero todavía "
                "no confirmé la señal. Voy a buscarla."
            )
            self._cerrar_tramo(dados)

    def _umbral_aviso_llegada(self) -> float:
        """Adelanta el aviso con el ritmo de marcha, sin mover el cierre."""
        if self._distancia_tramo <= 0:
            return self.config.ventana_llegada_inicio

        periodo_paso = (
            self.pasos.cadencia_s or self.config.periodo_paso_llegada_s
        )
        if (
            periodo_paso <= 0
            or self.config.tiempo_reaccion_llegada_s < 0
            or self.config.desaceleracion_llegada_m_s2 <= 0
            or not (
                0
                <= self.config.anticipacion_llegada_max_fraccion
                <= self.config.ventana_llegada_inicio
            )
        ):
            raise ValueError("Parámetros de anticipación de llegada inválidos")
        velocidad = self.zancada.metros / periodo_paso
        distancia_reaccion = velocidad * self.config.tiempo_reaccion_llegada_s
        distancia_frenado = velocidad**2 / (
            2.0 * self.config.desaceleracion_llegada_m_s2
        )
        margen = min(
            distancia_reaccion + distancia_frenado,
            self._distancia_tramo
            * self.config.anticipacion_llegada_max_fraccion,
        )
        return max(
            0.0,
            self.config.ventana_llegada_inicio - margen / self._distancia_tramo,
        )

    def _aprender_zancada(self, dados: int) -> None:
        """Mide la zancada con el tramo que acaba de cerrar la camara.

        Solo quien VERIFICA el tramo sirve para medir: la distancia
        del mapa solo se recorre entera si la camara vio la senal de
        cierre dentro de la ventana.  El cierre por pasos de mas (130
        % sin senal) no mide nada, porque ahi no se sabe donde acabo
        el tramo de verdad.
        """
        medida = self.zancada.medir(self._distancia_tramo, dados)
        if medida is None:
            return
        print(
            f"ZANCADA: {self._distancia_tramo:.1f} m en {dados} pasos "
            f"-> {medida:.3f} m/paso (EMA)"
        )

    def _cerrar_tramo(self, dados: int) -> None:
        """Da el tramo por terminado: toca encarar la zona alcanzada.

        Deja en consola lo que senso el contador: la diferencia entre
        crudos y acreditados (y los intervalos de la linea PASO)
        muestran si entraron pasos de mas.
        """
        print(f"TRAMO: {dados} pasos acreditados de {self.pasos.avanzar()} crudos")
        self._tramos_completados += 1
        self._falta_encarar = True
        self._instruccion_actual = None

    def _empezar_a_caminar(self) -> None:
        """Reinicia el tramo a pie: pasos, rumbo de partida y avisos."""
        self.pasos.reiniciar()
        # La referencia de la linea recta se fija con el primer paso
        # (ver _creditar_pasos): el residuo del giro anterior, la
        # reaccion al "ya giraste, puedes parar", no debe contaminarla.
        self._rumbo_al_caminar = self.rumbo.grados
        self._ultima_alerta_desvio = None
        self._corrigiendo = False
        self._desvio_inicial = 0.0
        self._pasos_previos = 0
        self._pasos_acreditados = 0
        # Quien cierra este tramo es la camara viendo la senal de la
        # zona SIGUIENTE del camino (la de cierre).  Se fija aqui, al
        # tomar la instruccion CAMINAR, para que _seguir_senal sepa
        # que ver esa senal no es una desviacion y para que la
        # ventana de llegada sepa que senal buscar.
        self._zona_cierre_tramo = (
            self._camino[self._tramos_completados + 1]
            if self._tramos_completados + 1 < len(self._camino)
            else None
        )
        self._aviso_ventana_dado = False
        # Distancia medida del tramo que empieza: sirve para medir la
        # zancada cuando la camara lo cierre.
        self._distancia_tramo = (
            self._instruccion_actual.distancia_m
            if self._instruccion_actual is not None
            else 0.0
        )
        self.estimador_posicion.reiniciar(self._distancia_tramo)
        self._pasos_estimador_previos = 0
        self._ultima_medicion_ultra_aplicada = None
        self._odometro_ultrasonico.reiniciar()
        self._medidor_distancia_puerta.reiniciar()
        self._distancia_puerta_estable_cm = None
        self._ultima_secuencia_distancia_puerta = getattr(
            self.sensor, "secuencia_medicion", None
        )
        self._contador_puertas.reiniciar()
        self._puerta_guiada = False
        self._puertas_ausentes = 0
        self._distancia_frontal_inicio_cm = None
        self._avance_ultrasonico_m = None
        self._ultima_secuencia_ultrasonica = getattr(
            self.sensor, "secuencia_medicion", None
        )

    def _actualizar_odometro_ultrasonico(
        self, instruccion: Instruccion
    ) -> None:
        """Integra solo lecturas nuevas y estables en los tramos habilitados."""
        distancia_fin_cm = instruccion.distancia_frontal_fin_cm
        if distancia_fin_cm is None or distancia_fin_cm <= 0:
            return

        secuencia = getattr(self.sensor, "secuencia_medicion", None)
        if secuencia is None or secuencia == self._ultima_secuencia_ultrasonica:
            return
        self._ultima_secuencia_ultrasonica = secuencia
        estable_cm = self._odometro_ultrasonico.observar(
            self.sensor.distancia_cm
        )
        if estable_cm is None:
            return

        if self._distancia_frontal_inicio_cm is None:
            if estable_cm <= distancia_fin_cm:
                return
            self._distancia_frontal_inicio_cm = estable_cm
            self._avance_ultrasonico_m = 0.0
            print(f"ODOMETRO HC-SR04: inicio estable a {estable_cm:.1f} cm")
            return

        avance = max(
            0.0, (self._distancia_frontal_inicio_cm - estable_cm) / 100.0
        )
        distancia_objetivo = max(
            0.0,
            (self._distancia_frontal_inicio_cm - distancia_fin_cm) / 100.0,
        )
        if self._distancia_tramo > 0:
            distancia_objetivo = min(distancia_objetivo, self._distancia_tramo)
        self._avance_ultrasonico_m = min(avance, distancia_objetivo)

    def _actualizar_posicion_tramo(
        self, resultado: ResultadoVision, instruccion: Instruccion, dados: int
    ) -> None:
        """Predice por pasos y corrige con mediciones verificables."""
        nuevos = max(0, dados - self._pasos_estimador_previos)
        self._pasos_estimador_previos = dados
        self.estimador_posicion.predecir(nuevos, self.zancada.metros)

        senal = resultado.senal
        if (
            senal is not None
            and senal.lado == LADO_INTERIOR
            and senal.zona == self._zona_cierre_tramo
            and self._distancia_tramo > 0
        ):
            deteccion = next(
                (
                    deteccion
                    for deteccion in resultado.detecciones
                    if deteccion.es_senal
                    and senal_de_clase(deteccion.nombre) == senal
                    and deteccion.distancia_m is not None
                ),
                None,
            )
            if deteccion is not None:
                medicion = self._distancia_tramo - deteccion.distancia_m
                self.estimador_posicion.corregir(
                    medicion, self.config.varianza_medicion_senal_m2
                )

        if (
            self._avance_ultrasonico_m is not None
            and self._avance_ultrasonico_m
            != self._ultima_medicion_ultra_aplicada
        ):
            self.estimador_posicion.corregir(
                self._avance_ultrasonico_m,
                self.config.varianza_medicion_ultrasonido_m2,
            )
            self._ultima_medicion_ultra_aplicada = self._avance_ultrasonico_m

        if self._distancia_tramo > 0:
            for puerta in resultado.puertas:
                if puerta.posicion != "centro" or puerta.distancia_m is None:
                    continue
                self.estimador_posicion.corregir(
                    self._distancia_tramo - puerta.distancia_m,
                    self.config.varianza_medicion_puerta_m2,
                )
                break

        pasadas_antes = self._contador_puertas.pasadas
        pasadas = self._contador_puertas.actualizar(
            (puerta.centro_x_px, puerta.posicion == "centro")
            for puerta in resultado.puertas
        )
        if (
            instruccion.puertas_en_tramo > 0
            and pasadas > pasadas_antes
            and self._distancia_tramo > 0
        ):
            medicion = self._distancia_tramo * pasadas / (
                instruccion.puertas_en_tramo + 1
            )
            self.estimador_posicion.corregir(
                medicion, self.config.varianza_medicion_puerta_m2
            )

    def _guiar_con_puerta(self, resultado: ResultadoVision) -> None:
        """Da una sola indicación direccional por puerta visible."""
        if not resultado.puertas:
            self._puertas_ausentes += 1
            if self._puertas_ausentes > self.config.frames_puerta_perdida:
                self._puerta_guiada = False
            return

        self._puertas_ausentes = 0
        if resultado.senal is not None or self._puerta_guiada:
            return
        if (
            time.monotonic() - self._ultima_voz_en
            < self.config.silencio_para_obstaculos_s
        ):
            return

        puerta = max(
            resultado.puertas,
            key=lambda deteccion: deteccion.alto_px * deteccion.ancho_px,
        )
        if puerta.posicion in ("izquierda", "derecha"):
            self._hablar(
                f"La puerta está a la {puerta.posicion}. Gira despacio a la "
                f"{puerta.posicion} hacia ella para buscar la señal."
            )
        elif self._distancia_puerta_estable_cm is None:
            self._hablar(
                "La puerta está centrada. Avanza despacio; te indicaré "
                "cuándo detenerte mientras busco la señal."
            )
        elif self._distancia_puerta_estable_cm > self.config.distancia_puerta_max_cm:
            self._hablar(
                "La puerta está centrada. Avanza despacio hacia ella; "
                "te indicaré cuándo detenerte para buscar la señal."
            )
        elif self._distancia_puerta_estable_cm < self.config.distancia_puerta_min_cm:
            self._hablar(
                "Estás demasiado cerca de la puerta. Detente y aléjate "
                "un poco."
            )
        else:
            self._hablar(
                "La puerta está centrada a "
                f"{round(self._distancia_puerta_estable_cm)} centímetros. "
                "Detente mientras busco la señal interior."
            )
        self._puerta_guiada = True

    def _actualizar_distancia_puerta(self, distancia_cm: float | None) -> None:
        """Conserva una mediana de lecturas ultrasónicas nuevas y estables."""
        secuencia = getattr(self.sensor, "secuencia_medicion", None)
        if secuencia is not None and secuencia == self._ultima_secuencia_distancia_puerta:
            return
        self._ultima_secuencia_distancia_puerta = secuencia
        self._distancia_puerta_estable_cm = (
            self._medidor_distancia_puerta.observar(distancia_cm)
        )

    def _senal_en_rango_de_llegada(
        self, resultado: ResultadoVision, senal: Senal
    ) -> bool:
        """Valida señal centrada y camino frontal libre.

        El ultrasonido es una salvaguarda de proximidad, no un punto de
        llegada preciso: puede medir una pared u obstáculo detrás de la
        señal. La señal interior confirmada por cámara da la referencia;
        solo se bloquea por obstáculo frontal o por distancia insegura.
        """
        distancia = self._distancia_puerta_estable_cm
        if (
            (
                distancia is not None
                and distancia < self.config.distancia_puerta_min_cm
            )
            or time.monotonic() - self._ultima_alerta_obstaculo_en
            < self.config.espera_llegada_tras_obstaculo_s
            or any(
                obstaculo.lado == EN_MEDIO_DEL_CAMINO
                for obstaculo in resultado.obstaculos
            )
        ):
            return False

        deteccion_senal = next(
            (
                deteccion
                for deteccion in resultado.detecciones
                if deteccion.es_senal
                and senal_de_clase(deteccion.nombre) == senal
            ),
            None,
        )
        if deteccion_senal is None:
            return False

        centro_senal = deteccion_senal.centro_x_px / self.config.ancho
        if not (
            self.config.guia_senal_centro_min
            <= centro_senal
            <= self.config.guia_senal_centro_max
        ):
            return False

        if resultado.puertas:
            x1, _, x2, _ = deteccion_senal.caja
            centro_x = (x1 + x2) / 2
            if not any(
                puerta.caja[0] <= centro_x <= puerta.caja[2]
                for puerta in resultado.puertas
            ):
                return False
        return True

    def _deteccion_senal_cierre(
        self, resultado: ResultadoVision
    ) -> Deteccion | None:
        """Devuelve la detección de la señal interior esperada, si aparece."""
        senal = resultado.senal
        if (
            senal is None
            or senal.lado != LADO_INTERIOR
            or senal.zona != self._zona_cierre_tramo
        ):
            return None
        return next(
            (
                deteccion
                for deteccion in resultado.detecciones
                if deteccion.es_senal and senal_de_clase(deteccion.nombre) == senal
            ),
            None,
        )

    def _pedir_acercamiento_a_senal(
        self, resultado: ResultadoVision, senal: Senal
    ) -> None:
        """Explica por qué aún no puede cerrar el tramo por cámara."""
        distancia = self._distancia_puerta_estable_cm
        obstaculo_frontal = any(
            obstaculo.lado == EN_MEDIO_DEL_CAMINO
            for obstaculo in resultado.obstaculos
        )
        if obstaculo_frontal:
            detalle = "Detente; hay un obstáculo delante."
        elif (
            distancia is not None
            and distancia < self.config.distancia_puerta_min_cm
        ):
            detalle = "Detente y aléjate un poco; estás demasiado cerca."
        elif (
            time.monotonic() - self._ultima_alerta_obstaculo_en
            < self.config.espera_llegada_tras_obstaculo_s
        ):
            detalle = (
                "Espera un momento; estoy comprobando que el camino esté libre."
            )
        else:
            deteccion = self._deteccion_senal_cierre(resultado)
            if deteccion is None:
                detalle = "Mantén la señal orientadora a la vista."
            else:
                centro = deteccion.centro_x_px / self.config.ancho
                if centro < self.config.guia_senal_centro_min:
                    detalle = (
                        "La señal está a la izquierda. Gira despacio a la "
                        "izquierda hasta centrarla."
                    )
                elif centro > self.config.guia_senal_centro_max:
                    detalle = (
                        "La señal está a la derecha. Gira despacio a la "
                        "derecha hasta centrarla."
                    )
                else:
                    detalle = (
                        "La señal está enfrente. No hace falta buscar una "
                        "distancia exacta; sigue despacio."
                    )

        self._advertecer(
            f"distancia-senal:{senal.zona}",
            f"Veo la señal de {senal.zona}. {detalle}",
        )
        if any(
            obstaculo.lado == EN_MEDIO_DEL_CAMINO
            for obstaculo in resultado.obstaculos
        ):
            self._registrar_alerta_obstaculo()

    def _deteccion_senal_cierre_centrada(
        self, resultado: ResultadoVision | None
    ) -> bool:
        """Indica si la cámara confirma centrada la señal de cierre del tramo."""
        if (
            resultado is None
            or resultado.senal is None
            or resultado.senal.lado != LADO_INTERIOR
            or resultado.senal.zona != self._zona_cierre_tramo
            or any(
                obstaculo.lado == EN_MEDIO_DEL_CAMINO
                for obstaculo in resultado.obstaculos
            )
        ):
            return False

        deteccion = next(
            (
                deteccion
                for deteccion in resultado.detecciones
                if deteccion.es_senal
                and senal_de_clase(deteccion.nombre) == resultado.senal
            ),
            None,
        )
        if deteccion is None:
            return False

        centro = deteccion.centro_x_px / self.config.ancho
        return (
            self.config.guia_senal_centro_min
            <= centro
            <= self.config.guia_senal_centro_max
        )

    def _creditar_pasos(self, resultado: ResultadoVision | None = None) -> int:
        """Pasos que cuentan hacia el tramo: rectos o guiados por visión.

        La ruta esta medida en linea recta: un paso dado mientras la
        persona esta desviada no la acerca al sitio medido (la curva
        alarga el recorrido), asi que no se acredita. Si la cámara ve
        centrada la señal interior esperada, usa esa referencia visual
        para acreditar el avance aunque el rumbo inercial derive.
        """
        crudos = self.pasos.avanzar()
        nuevos = crudos - self._pasos_previos
        self._pasos_previos = crudos

        if self._deteccion_senal_cierre_centrada(resultado):
            # La cámara confirma que el destino inmediato está delante:
            # en este caso su dirección visual es mejor referencia que
            # una deriva inercial y los pasos sí avanzan hacia la señal.
            self._rumbo_al_caminar = self.rumbo.grados
            self._pasos_acreditados += nuevos
            return self._pasos_acreditados

        if crudos == 0:
            # Aun no ha pisado: mientras gira por inercia la referencia
            # sigue su rumbo.  El primer paso la bloquea.
            self._rumbo_al_caminar = self.rumbo.grados
        else:
            desvio = abs(
                self.rumbo.diferencia(self.rumbo.grados, self._rumbo_al_caminar)
            )
            if desvio < self.config.desvio_maximo_grados:
                self._pasos_acreditados += nuevos
            elif nuevos:
                print(f"PASO SIN CREDITO: desviado {desvio:.0f} grados")
        return self._pasos_acreditados

    def _guiar_con_senal(self, resultado: ResultadoVision) -> None:
        """Guia visual del tramo: caminar hacia la senal si esta enfrente.

        El MPU6050 esta en la cintura y solo da rumbo RELATIVO (ademas
        de derivar): la camara es el unico sensor que no se desvia.
        Cuando YOLO confirma la senal de la SIGUIENTE zona del camino
        casi en el centro del frame, la persona la tiene delante y
        basta con caminar hacia ella; el aviso da una referencia
        absoluta mientras dure el tramo y sirve tambien de apoyo cuando
        el sistema le esta pidiendo que enderece.

        Solo se guia con la senal centrada: una imagen a un lado es
        una puerta que se pasa de largo, no un punto al que caminar
        (ir hacia ella seria acabar contra la pared).  Las de otras
        zonas tampoco sirven (ya se anuncian aparte en _seguir_senal).
        """
        senal = resultado.senal
        if senal is None or not self._camino:
            return

        indice_siguiente = self._indice_zona + 1
        if (
            indice_siguiente >= len(self._camino)
            or self._camino[indice_siguiente] != senal.zona
        ):
            return

        caja = next(
            (
                deteccion.caja
                for deteccion in resultado.detecciones
                if deteccion.es_senal and senal_de_clase(deteccion.nombre) == senal
            ),
            None,
        )
        if caja is None:
            return

        x1, _, x2, _ = caja
        centro = (x1 + x2) / 2 / self.config.ancho
        if not (
            self.config.guia_senal_centro_min
            <= centro
            <= self.config.guia_senal_centro_max
        ):
            return

        if (
            senal.zona == self._zona_cierre_tramo
            and not self._senal_en_rango_de_llegada(resultado, senal)
        ):
            self._pedir_acercamiento_a_senal(resultado, senal)
            return

        self._advertecer(
            f"guia:{senal.zona}",
            f"La señal de {senal.zona} está frente a ti: camina hacia ella.",
        )

    def _vigilar_desvio(self, resultado: ResultadoVision | None = None) -> None:
        """Guia la correccion del rumbo hasta volver a la linea recta.

        El rumbo integra el giroscopio desde la ultima senal anclada:
        si mientras camina acumula mas giro que ``desvio_maximo_grados``
        va curvando, y los pasos no llegarian al sitio medido. La señal
        esperada centrada por la cámara es una referencia visual más
        directa y prevalece sobre la estimación inercial.

        Con el sensor en la cintura hacen falta tres protecciones para
        que el aviso no salga por un balanceo o un retorcimiento del
        torso:

        * el rumbo solo acumula el giro SOSTENIDO (ver MedidorGiro):
          el vaiven de la cadera no entra;
        * el desvio tiene que mantenerse ``confirmacion_desvio_s``
          desde que cruzo el umbral antes de hablar;
        * al liberar se usa ``liberacion_desvio_factor``: sin
          histérisis, en el limite del umbral el sistema parpadearia
          entre "te estas desviando" y "ya enderezaste".

        La correccion se sigue EN VIVO: no se manda un numero fijo de
        grados (que se queda viejo mientras la persona oye la voz y
        sigue caminando), sino que el giro se mide en cada muestra y
        se dice "ya enderezaste" al entrar en la recta.  Asi la
        persona sabe cuando PARAR de girar y no termina caminando en
        la direccion contraria.  Tampoco hay avisos a mitad del giro
        de correccion: la voz tarda mas que la persona en girar y,
        cuando terminaba la frase, el giro quedaba pasado y se
        deshacia.
        """
        if self._deteccion_senal_cierre_centrada(resultado):
            # No pedir una corrección que alejaría a la persona de una
            # señal esperada que la cámara ya ve centrada.
            self._rumbo_al_caminar = self.rumbo.grados
            self._inicio_desvio = None
            self._corrigiendo = False
            self._desvio_inicial = 0.0
            self._ultima_alerta_desvio = None
            return

        desvio = self.rumbo.diferencia(
            self.rumbo.grados, self._rumbo_al_caminar
        )
        umbral = self.config.desvio_maximo_grados
        liberacion = umbral * self.config.liberacion_desvio_factor
        ahora = time.monotonic()

        if abs(desvio) < liberacion:
            # Dentro de la linea recta: si estaba corrigiendo, ya
            # enderezo (aqui se aplica la histérisis de liberacion).
            self._inicio_desvio = None
            if self._corrigiendo:
                self._corrigiendo = False
                self._ultima_alerta_desvio = None
                self._hablar("Ya enderezaste. Los pasos vuelven a contar.")
            return

        if abs(desvio) < umbral:
            # Zona de histérisis: todavia no se libera (falta bajar
            # hasta ``liberacion``).  Tampoco se avisa, y la espera de
            # confirmacion se reinicia: el desvio tiene que sostenerse
            # por encima del umbral para que el aviso salga.
            self._inicio_desvio = None
            return

        # Desvio positivo = giro a la derecha: para volver a la recta
        # hay que girar al lado contrario.
        sentido = "izquierda" if desvio > 0 else "derecha"
        grados = round(abs(desvio))

        if not self._corrigiendo:
            if self._inicio_desvio is None:
                self._inicio_desvio = ahora
                print(f"DESVIO: {desvio:+.1f} grados (confirmando...)")
                return
            if ahora - self._inicio_desvio < self.config.confirmacion_desvio_s:
                # Un giro repentino del torso (o un balanceo fuerte)
                # no es un cambio de ruta: todavia no se avisa.
                return
            if (
                self._ultima_alerta_desvio is not None
                and ahora - self._ultima_alerta_desvio
                < self.config.enfriamiento_desvio_s
            ):
                return
            self._ultima_alerta_desvio = ahora
            self._corrigiendo = True
            self._desvio_inicial = abs(desvio)
            print(f"DESVIO: {desvio:+.1f} grados -> corregir a la {sentido}")
            self._hablar(
                f"Alto un momento. Te estás desviando de la línea "
                f"recta, unos {grados} grados. Gira despacio a la "
                f"{sentido} hasta que te diga que ya enderezaste."
            )
            # El reloj del recordatorio arranca al TERMINAR de oirse el
            # aviso: _hablar bloquea mientras suena el audio, y contar
            # desde antes haria saltar el recordatorio en cuanto
            # terminara de hablar, sin dar tiempo a reaccionar.
            self._ultima_alerta_desvio = time.monotonic()
            return

        # En plena correccion: si el desvio no baja (giró al reves o
        # sigue curvando), se recuerda pronto con el valor actual.
        if abs(desvio) >= self._desvio_inicial:
            self._desvio_inicial = abs(desvio)
            if ahora - self._ultima_alerta_desvio >= RECORDATORIO_CORRECCION_S:
                print(f"DESVIO SIN MEJORAR: {desvio:+.1f} grados")
                self._hablar(
                    f"Sigues fuera de la línea recta: {grados} grados. "
                    f"Gira despacio a la {sentido}."
                )
                self._ultima_alerta_desvio = time.monotonic()
            return

        # El giro de correccion se sigue en vivo hasta aqui: no se
        # anuncia la fraccion recuperada ("Llevas la mitad del giro
        # para enderezar", "Ya casi enderezas").  La voz es lenta y la
        # persona sigue girando mientras suena, de modo que cuando
        # terminaba la frase ya habia girado mas de lo que tocaba y el
        # giro se deshacia.  Solo se recuerda el desvio maximo, para
        # avisar si el desvio no baja.
        self._desvio_inicial = max(self._desvio_inicial, abs(desvio))

    def _anunciar_instruccion(self, instruccion: Instruccion) -> None:
        if instruccion.accion is Accion.GIRAR_DERECHA:
            self._hablar(f"Gira a la derecha {instruccion.cantidad} grados.")
        elif instruccion.accion is Accion.GIRAR_IZQUIERDA:
            self._hablar(f"Gira a la izquierda {instruccion.cantidad} grados.")
        elif instruccion.accion is Accion.CAMINAR:
            self._hablar(f"Camina {instruccion.cantidad} pasos.")
