"""Asistente de orientacion para personas invidentes.

El paquete ``guia`` organiza el sistema en capas con responsabilidades
claras:

* ``configuracion`` -> parametros ajustables (rutas, umbrales, pines).
* ``dominio``       -> estados, senales, comandos de voz y rutas.
* ``hardware``      -> camara, MPU6050 y sensor ultrasonico.
* ``audio``         -> voz (Piper), escucha (Vosk) y pitidos.
* ``vision``        -> deteccion YOLO y ventana de diagnostico.
* ``navegacion``    -> medicion de giros y conteo de pasos.
* ``aplicacion``    -> orquestador con la maquina de estados.
"""

__version__ = "2.0.0"
