"""Ventana de diagnostico: estado del sistema para quien pruebe el equipo."""

from __future__ import annotations

import cv2

from ..dominio.estados import Estado

TECLA_SALIR = 27  # Escape


class VentanaDiagnostico:
    """Muestra distancia, estado, ubicacion y destino sobre el video."""

    def __init__(self, titulo: str = "Sistema de Orientacion", activa: bool = True):
        self._titulo = titulo
        self._activa = activa

    def mostrar(
        self,
        frame,
        *,
        distancia_cm: float,
        estado: Estado,
        detalle: str = "",
        ubicacion: str | None = None,
        destino: str | None = None,
    ) -> int:
        """Pinta la informacion y devuelve la tecla pulsada (0 si nada)."""
        if not self._activa:
            return 0

        lineas = [
            (f"Distancia: {distancia_cm:.1f} cm", 30, (0, 255, 255)),
            (estado.etiqueta, 60, (255, 255, 255)),
        ]
        if detalle:
            lineas.append((detalle, 90, (255, 255, 255)))
        if ubicacion:
            lineas.append((f"Ubicacion: {ubicacion}", 120, (255, 255, 255)))
        if destino:
            lineas.append((f"Destino: {destino}", 150, (255, 255, 255)))

        for texto, y, color in lineas:
            cv2.putText(
                frame,
                texto,
                (10, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.60,
                color,
                2,
            )

        cv2.imshow(self._titulo, frame)
        return cv2.waitKey(1) & 0xFF

    def cerrar(self) -> None:
        """Cierra todas las ventanas de OpenCV."""
        if self._activa:
            cv2.destroyAllWindows()
