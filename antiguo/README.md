# Código antiguo (archivado)

Estos scripts eran la primera versión del proyecto. Ya **no se usan**:
su lógica fue reorganizada y optimizada con POO en el paquete [`../guia/`](../guia).

| Archivo | Qué era | Dónde vive ahora |
|----------|---------|------------------|
| `MODELO DE ORIENTACION.py` | Script principal monolítico (~2800 líneas, con errores de nombres de estado) | `guia/aplicacion.py` + capas de hardware/audio/vision/navegacion |
| `LIBRERIA CAMARA.py` | Intento de agrupar todo en un solo archivo | sustituido por el paquete `guia/` |
| `MODULOS/MODULO DE CONFIGURACION.py` | Constantes sueltas | `guia/configuracion.py` (dataclass) |
| `MODULOS/MODULO AUDIO.py` | (vacío) | `guia/audio/` |
| `experimentos/` | Pruebas sueltas de sensores, calibración, caracterización del MPU6050, conversiones de modelo, etc. | quedan como referencia histórica |

Se conservan por si quieres consultar cómo se calibró el giroscopio
(`experimentos/calibracion_giroscopio.txt`) o las caracterizaciones del
MPU6050 (`experimentos/*.csv`).

Cuando el proyecto funcione en la Raspberry Pi con `guia/`, puedes borrar
esta carpeta sin problema.
