"""Version del codigo en ejecucion (commit de git).

Para no confundirse al probar en la Raspberry: el asistente la anuncia
por voz y por consola al arrancar, de modo que siempre se sabe si el
equipo corre el ultimo ``git pull`` o una copia vieja del codigo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Raiz del repositorio: la carpeta que contiene a este paquete.
_RAIZ = Path(__file__).resolve().parent.parent


def obtener_version() -> str:
    """Devuelve el commit corto actual, o "desconocida" si no hay git."""
    try:
        salida = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=_RAIZ,
        )
        if salida.returncode == 0:
            commit = salida.stdout.strip()
            if commit:
                return commit
    except Exception:
        pass
    return "desconocida"


def version_hablada(version: str) -> str:
    """La version letra por letra, para que Piper la lea sin trabarse."""
    return ", ".join(version)
