"""Orquestador del asistente: maquina de estados, voz y navegacion.

Esta es la unica pieza que coordina todo.  Cada fase del recorrido es
un estado (:class:`Estado`) con su propio manejador, de modo que el
bucle principal siempre hace lo mismo: mirar, escuchar, actuar y
mostrar.
"""

from __future__ import annotations

import queue
import time
from typing import Callable

from .audio.alarma import ReproductorBeep
from .audio.locutor import Locutor
from .audio.microfono import Microfono, ModoMicrofono
from .configuracion import Configuracion
from .dominio.catalogo import LADO_INTERIOR, LADO_PASILLO
from .dominio.comandos import (
    TipoComando,
    contiene_despertador,
    extraer_destino,
    quitar_despertador,
    reconocer,
)
from .dominio.estados import Estado
from .dominio.rutas import Accion, Instruccion, PlanificadorRutas
from .hardware.mpu6050 import MPU6050
from .hardware.ultrasonido import AlertaProximidad, SensorUltrasonico
from .navegacion.caida import DetectorCaida
from .navegacion.movimiento import ContadorPasos, MedidorGiro, MuestreadorMPU
from .navegacion.rumbo import Rumbo
from .vision.detector import DetectorVisual, ResultadoVision, dibujar_detecciones
from .vision.interfaz import TECLA_SALIR, VentanaDiagnostico

# Enfoque de busqueda: girar, mirar, media vuelta, mirar, 5 pasos.
MAX_INTENTOS_DESDE_GIRO = 2

# Cada cuantos segundos se avisa por consola el avance del giro.
# Sirve para diagnosticar en el Raspberry (consola) si la velocidad
# de giro tiene el signo correcto o si el giro esta atascado en cero.
AVISO_GIRO_S = 1.0


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
        self.caida = DetectorCaida(config)
        self.muestreador = MuestreadorMPU(
            mpu,
            config,
            (self.giro.en_muestra, self.pasos.en_muestra, self.caida.en_muestra),
        )

        # Navegacion
        self._planificador = planificador or PlanificadorRutas()
        self._plan: list[Instruccion] = []
        self._indice_plan = 0
        self._instruccion_actual: Instruccion | None = None
        self._ruta_pendiente: list[Instruccion] | None = None
        self._instruccion_encarar_dada = False
        self._rumbo_al_caminar = 0.0
        self._ultima_alerta_desvio: float | None = None
        self._pasos_acreditados = 0
        self._pasos_previos = 0
        # Zonas del plan en curso: sirven para seguir el avance y para
        # detectar si la persona se devolvio (y replanificar a tiempo).
        self._camino: list[str] = []
        self._indice_zona = 0
        self._inicio_llegada = 0.0

        # Busqueda de senal
        self._intento = 0
        self._inicio_busqueda = 0.0

        # Voz
        self._fin_ventana_comando = 0.0
        self._en_pausa = False

        self._etiqueta = ""
        self._ultimo_aviso_giro = 0.0
        # Enfriamiento de los avisos hablados que dependen de lo que
        # se ve con la camara (puerta del lado del pasillo, senal de
        # otra zona): una senal confirmada se repite en varios frames.
        self._ultima_advertencia: dict[str, float] = {}
        # Avisos de avance del giro en curso (0.5 y 0.75 del objetivo)
        self._avances_anunciados: set[float] = set()
        self._ultimo_angulo_giro = 0.0
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
        print()
        print("======================================")
        print("INICIANDO SISTEMA")
        print("======================================")

        self.muestreador.start()
        self.alerta.start()
        self.microfono.iniciar()
        time.sleep(2)

        try:
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
        self.muestreador.detener()
        self.alerta.detener()
        self.microfono.detener()
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

            self._procesar_voz()
            self._vigilar_caida()
            self._anunciar_obstaculos(resultado)

            # Un solo punto de decision: el manejador del estado actual.
            self._manejadores[self.estado](self, resultado)

            tecla = 0
            if self._ventana.activa:
                dibujar_detecciones(frame, resultado)
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
        """
        modo_anterior = self.microfono.modo
        self.microfono.cambiar_modo(ModoMicrofono.APAGADO)
        self.locutor.hablar(texto)
        self.microfono.cambiar_modo(cambiar_a if cambiar_a is not None else modo_anterior)

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
            plan = self._planificador.planificar(self.ubicacion, destino)
            if plan is None:
                self._volver_a_preguntar(
                    f"La ruta hacia {destino} todavía no está configurada."
                )
            else:
                self._hablar(f"Entendido. Vamos a {destino}.")
                self.microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)
                self._iniciar_encarada(plan)

    def _iniciar_encarada(self, plan: list[Instruccion]) -> None:
        """Prepara la ruta: antes hay que encarar la senal de la zona.

        Las instrucciones del mapa se dan "de frente a la senal
        interior de la puerta" (asi se midieron los pasos).  Si la
        camara no la ve ahora, se pide girar despacio hasta
        encontrarla antes de arrancar.
        """
        self._ruta_pendiente = plan
        self._instruccion_encarar_dada = False
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
                    f"{self.ubicacion}. Gira despacio hasta encontrar "
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
                print("RUMBO ANCLADO: señal de", senal.zona, "enfrente")
                plan = self._ruta_pendiente or []
                self._ruta_pendiente = None
                if self._instruccion_encarar_dada:
                    self._hablar("La señal está frente a ti.")
                self._iniciar_navegacion(plan)
                return

        self._etiqueta = "Encarando la senal orientadora"
        if not self._instruccion_encarar_dada:
            self._instruccion_encarar_dada = True
            self._hablar(
                "Para saber hacia dónde miras, primero encárate con la "
                "señal de esta zona. Gira despacio a la izquierda hasta "
                "que te diga que la tienes frente a ti."
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
        # Zonas del recorrido: la primera es donde se parte, y cada
        # senal interior confirmada va avanzando por la lista.
        self._camino = self._planificador.camino(self.ubicacion, self.destino) or []
        self._indice_zona = 0
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
        self._indice_zona = max(indice, 0)
        self._replanificar_desde()
        return True

    def _replanificar_desde(self) -> None:
        """Rehace la ruta desde la ubicacion real (la persona se desvio)."""
        destino = self.destino
        plan = self._planificador.planificar(self.ubicacion, destino)
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
        self._hablar(
            f"Para confirmar la llegada, gira despacio hasta encontrar "
            f"la señal de {self.destino}."
        )

    def _en_confirmando_llegada(self, resultado: ResultadoVision) -> None:
        """Solo anuncia la llegada si ve la senal interior del destino."""
        self._etiqueta = f"Confirmando llegada a {self.destino}"
        senal = resultado.senal

        if senal is not None and senal.zona == self.destino:
            if senal.lado == LADO_INTERIOR:
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
            self._hablar(
                f"Todavía no veo la señal de {self.destino}. Gira "
                "despacio hasta encontrarla."
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
    def _anunciar_obstaculos(self, resultado: ResultadoVision) -> None:
        for nombre in resultado.obstaculos:
            self._hablar(f"Alto, hay un obstáculo: {nombre}.")

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

    def _mostrar_giro(self, sentido: str, angulo: float, objetivo: float) -> bool:
        """Etiqueta de diagnostico, aviso de consola y voz del giro.

        La consola muestra los grados acumulados y la velocidad de giro
        actual (positiva = derecha).  Si al girar a la derecha la
        velocidad sale negativa, el ``signo_derecha`` esta invertido.

        Se avisa por voz al 50% y al 75% del objetivo con frases
        *cortas* (sin numeros): la voz tarda su tiempo y mientras se
        habla la persona sigue girando, por eso el manejador vuelve a
        medir tras cualquier aviso.  Devuelve ``True`` si acaba de
        hablar.
        """
        self._etiqueta = f"Giro {sentido}: {angulo:.1f}/{objetivo} grados"
        ahora = time.monotonic()
        if ahora - self._ultimo_aviso_giro >= AVISO_GIRO_S:
            self._ultimo_aviso_giro = ahora
            velocidad = self.giro.velocidad_derecha
            print(
                f"GIRO {sentido}: {angulo:.1f}/{objetivo} grados"
                f" | vel {velocidad:+.1f} dps"
            )

        # El acumulado solo baja cuando se reinicia el giro: ese es el
        # momento de olvidar los avisos del giro anterior.
        if angulo < self._ultimo_angulo_giro:
            self._avances_anunciados.clear()
        self._ultimo_angulo_giro = angulo

        for parte, texto in (
            (0.75, "Ya casi listo."),
            (0.5, "Vas por la mitad."),
        ):
            if parte not in self._avances_anunciados and angulo >= objetivo * parte:
                self._avances_anunciados.add(parte)
                self._hablar(texto)
                return True
        return False

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
        if self._mostrar_giro("derecha", angulo, objetivo):
            # Mientras la voz aviso el avance, la persona sigue
            # girando: se vuelve a medir para no dejar pasar el
            # objetivo (antes se giraba de mas esperando el aviso).
            angulo = self.giro.avanzar(derecha=True)

        if angulo >= objetivo:
            self._avances_anunciados.clear()
            self._hablar(f"Ya giraste {objetivo:.0f} grados. Puedes parar.")
            self._iniciar_busqueda(intento=1)

    def _en_buscar_senal(self, resultado: ResultadoVision) -> None:
        self._etiqueta = f"Buscando senal - intento {self._intento}"

        senal = resultado.senal
        if senal is not None:
            self.rumbo.anclar()  # se mira la senal: rumbo conocido
            if senal.lado == LADO_INTERIOR:
                # La cara interior confirma que la persona esta en la
                # zona (los tramos de ruta se midieron desde ahi).
                self.ubicacion = senal.zona
                self.lado_puerta = senal.lado
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
        if self._mostrar_giro("izquierda", angulo, objetivo):
            angulo = self.giro.avanzar(derecha=False)

        if angulo >= objetivo:
            self._avances_anunciados.clear()
            self._hablar(f"Ya giraste {objetivo:.0f} grados. Puedes parar.")
            self._iniciar_busqueda(intento=2)

    def _en_cinco_pasos(self, resultado: ResultadoVision) -> None:
        objetivo = self.config.pasos_objetivo
        dados = self._creditar_pasos()
        self._etiqueta = f"Caminando - {dados}/{objetivo} pasos"
        self._vigilar_desvio()

        if dados >= objetivo:
            self._hablar(
                f"{objetivo} pasos completados. Puedes parar. Gira "
                f"{self.config.grados_giro_inicial} grados a la derecha "
                "suavemente para buscar la señal."
            )
            self.giro.reiniciar()
            self.estado = Estado.GIRO_INICIAL

    def _en_esperando_destino(self, resultado: ResultadoVision) -> None:
        self._etiqueta = "Esperando destino"

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

            self._instruccion_actual = self._plan[self._indice_plan]
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
            if self._mostrar_giro(sentido, angulo, float(instruccion.cantidad)):
                angulo = self.giro.avanzar(derecha=derecha)
            if angulo >= instruccion.cantidad:
                self._avances_anunciados.clear()
                self._hablar("Giro completo. Puedes parar.")
                self._instruccion_actual = None

        elif instruccion.es_pasos:
            dados = self._creditar_pasos()
            self._etiqueta = f"Caminando: {dados}/{instruccion.cantidad} pasos"
            self._vigilar_desvio()
            if dados >= instruccion.cantidad:
                self._hablar("Pasos completados. Puedes parar.")
                self._instruccion_actual = None

    def _empezar_a_caminar(self) -> None:
        """Reinicia el tramo a pie: pasos, rumbo de partida y avisos."""
        self.pasos.reiniciar()
        # La referencia de la linea recta se fija con el primer paso
        # (ver _creditar_pasos): el residuo del giro anterior, la
        # reaccion al "ya giraste, puedes parar", no debe contaminarla.
        self._rumbo_al_caminar = self.rumbo.grados
        self._ultima_alerta_desvio = None
        self._pasos_previos = 0
        self._pasos_acreditados = 0

    def _creditar_pasos(self) -> int:
        """Pasos que cuentan hacia el tramo: solo los dados rectos.

        La ruta esta medida en linea recta: un paso dado mientras la
        persona esta desviada no la acerca al sitio medido (la curva
        alarga el recorrido), asi que no se acredita.  Cuando
        enderece, los pasos vuelven a contar.
        """
        crudos = self.pasos.avanzar()
        nuevos = crudos - self._pasos_previos
        self._pasos_previos = crudos

        if crudos == 0:
            # Aun no ha pisado: mientras gira por inercia la referencia
            # sigue su rumbo.  El primer paso la bloquea.
            self._rumbo_al_caminar = self.rumbo.grados
        else:
            desvio = abs(self.rumbo.grados - self._rumbo_al_caminar)
            if desvio < self.config.desvio_maximo_grados:
                self._pasos_acreditados += nuevos
            elif nuevos:
                print(f"PASO SIN CREDITO: desviado {desvio:.0f} grados")
        return self._pasos_acreditados

    def _vigilar_desvio(self) -> None:
        """Avisa si al caminar se aparta de la linea recta.

        El rumbo integra el giroscopio desde la ultima senal anclada:
        si mientras camina acumula mas giro que ``desvio_maximo_grados``
        va curvando, y los pasos no llegarian al sitio medido.
        """
        desvio = self.rumbo.grados - self._rumbo_al_caminar
        if abs(desvio) < self.config.desvio_maximo_grados:
            return

        ahora = time.monotonic()
        if (
            self._ultima_alerta_desvio is not None
            and ahora - self._ultima_alerta_desvio
            < self.config.enfriamiento_desvio_s
        ):
            return
        self._ultima_alerta_desvio = ahora

        # Desvio positivo = giro a la derecha: para enderezar hay que
        # corregir al lado contrario.
        sentido = "izquierda" if desvio > 0 else "derecha"
        grados = round(abs(desvio))
        print(f"DESVIO: {desvio:+.1f} grados respecto de la recta")
        self._hablar(
            f"Te estás desviando de la línea recta. Gira {grados} "
            f"grados a la {sentido} para seguir derecho. Mientras no "
            "endereces, los pasos no cuentan."
        )

    def _anunciar_instruccion(self, instruccion: Instruccion) -> None:
        if instruccion.accion is Accion.GIRAR_DERECHA:
            self._hablar(f"Gira a la derecha {instruccion.cantidad} grados.")
        elif instruccion.accion is Accion.GIRAR_IZQUIERDA:
            self._hablar(f"Gira a la izquierda {instruccion.cantidad} grados.")
        elif instruccion.accion is Accion.CAMINAR:
            self._hablar(f"Camina {instruccion.cantidad} pasos.")
