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
        self.destino: str | None = None

        # Movimiento: el muestreador lee el MPU6050 a 100 Hz y reparte
        # cada lectura entre el medidor de giro, el contador de pasos
        # y el detector de caidas.  Integrar en un hilo propio evita
        # que la carga de la camara haga perder grados de giro.
        self.giro = MedidorGiro(config)
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

        # Busqueda de senal
        self._intento = 0
        self._inicio_busqueda = 0.0

        # Voz
        self._fin_ventana_comando = 0.0
        self._en_pausa = False

        self._etiqueta = ""
        self._ultimo_aviso_giro = 0.0
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
            Estado.NAVEGANDO: Aplicacion._en_navegando,
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

        else:
            self._hablar("No entendí la orden.")

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
        else:
            plan = self._planificador.planificar(self.ubicacion, destino)
            if plan is None:
                self._volver_a_preguntar(
                    f"La ruta hacia {destino} todavía no está configurada."
                )
            else:
                self._hablar(f"Entendido. Vamos a {destino}.")
                self.microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)
                self._iniciar_navegacion(plan)

    def _volver_a_preguntar(self, motivo: str) -> None:
        """Explica el problema y vuelve a pedir el destino."""
        self.destino = None
        self._hablar(motivo)
        self._hablar("¿A dónde quieres ir?", cambiar_a=ModoMicrofono.DESTINO)

    def _iniciar_navegacion(self, plan: list[Instruccion]) -> None:
        self._plan = plan
        self._indice_plan = 0
        self._instruccion_actual = None
        self._en_pausa = False
        self.estado = Estado.NAVEGANDO

    def _finalizar_navegacion(self) -> None:
        destino = self.destino
        self.ubicacion = destino
        self.destino = None
        self._plan = []
        self._indice_plan = 0
        self._instruccion_actual = None
        self.estado = Estado.ESPERANDO_DESTINO

        self._hablar(f"Has llegado a {destino}.")
        self._hablar("¿A dónde quieres ir?", cambiar_a=ModoMicrofono.DESTINO)

    # ------------------------------------------------------------------
    # Obstaculos y seguridad
    # ------------------------------------------------------------------
    def _anunciar_obstaculos(self, resultado: ResultadoVision) -> None:
        for nombre in resultado.obstaculos:
            self._hablar(f"Alto, hay un {nombre}.")

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

        if resultado.senal is not None:
            self.ubicacion = resultado.senal
            print("SEÑAL ENCONTRADA:", self.ubicacion)
            self._hablar(f"Estás en {self.ubicacion}.")
            self.estado = Estado.ESPERANDO_DESTINO
            self._hablar("¿A dónde quieres ir?", cambiar_a=ModoMicrofono.DESTINO)
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
            self.pasos.reiniciar()
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
        dados = self.pasos.avanzar()
        self._etiqueta = f"Caminando - {dados}/{objetivo} pasos"

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

        if self._instruccion_actual is None:
            if self._indice_plan >= len(self._plan):
                self._finalizar_navegacion()
                return

            self._instruccion_actual = self._plan[self._indice_plan]
            self._indice_plan += 1

            if self._instruccion_actual.accion is Accion.LLEGADA:
                self._finalizar_navegacion()
                return

            self._anunciar_instruccion(self._instruccion_actual)
            if self._instruccion_actual.es_giro:
                self.giro.reiniciar()
            else:
                self.pasos.reiniciar()

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
            dados = self.pasos.avanzar()
            self._etiqueta = f"Caminando: {dados}/{instruccion.cantidad} pasos"
            if dados >= instruccion.cantidad:
                self._hablar("Pasos completados. Puedes parar.")
                self._instruccion_actual = None

    def _anunciar_instruccion(self, instruccion: Instruccion) -> None:
        if instruccion.accion is Accion.GIRAR_DERECHA:
            self._hablar(f"Gira a la derecha {instruccion.cantidad} grados.")
        elif instruccion.accion is Accion.GIRAR_IZQUIERDA:
            self._hablar(f"Gira a la izquierda {instruccion.cantidad} grados.")
        elif instruccion.accion is Accion.CAMINAR:
            self._hablar(f"Camina {instruccion.cantidad} pasos.")
