
import smbus
import time
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from collections import deque

# MPU6050
bus = smbus.SMBus(1)
ADDR = 0x68

# Despertar el sensor
bus.write_byte_data(ADDR, 0x6B, 0)

# Configurar rango del acelerómetro: +/- 2 g
bus.write_byte_data(ADDR, 0x1C, 0x00)

# Configurar rango del giroscopio: +/- 250 grados/s
bus.write_byte_data(ADDR, 0x1B, 0x00)

N = 100
t0 = time.monotonic()
tiempo = deque(maxlen=N)

ax = deque(maxlen=N)
ay = deque(maxlen=N)
az = deque(maxlen=N)

gx = deque(maxlen=N)
gy = deque(maxlen=N)
gz = deque(maxlen=N)


def leer_eje(registro):
    alto = bus.read_byte_data(ADDR, registro)
    bajo = bus.read_byte_data(ADDR, registro + 1)

    valor = (alto << 8) | bajo

    if valor >= 32768:
        valor -= 65536

    return valor


fig, (g1, g2) = plt.subplots(2, 1)

lineas1 = [
    g1.plot([], [], label=e)[0] for e in ["X", "Y", "Z"]
]
lineas2 = [
    g2.plot([], [], label=e)[0] for e in ["X", "Y", "Z"]
]

g1.set_title("Acelerometro")
g1.set_ylabel("Aceleracion (g)")
g1.legend()
g1.grid()

g2.set_title("Giroscopio")
g2.set_ylabel("Velocidad angular (°/s)")
g2.set_xlabel("Tiempo (s)")
g2.legend()
g2.grid()


def actualizar(frame):
    # Acelerometro: registros 0x3B, 0x3D, 0x3F
    ax.append(leer_eje(0x3B) / 16384.0)
    ay.append(leer_eje(0x3D) / 16384.0)
    az.append(leer_eje(0x3F) / 16384.0)

    # Giroscopio: registros 0x43, 0x45, 0x47
    gx.append(leer_eje(0x43) / 131.0)
    gy.append(leer_eje(0x45) / 131.0)
    gz.append(leer_eje(0x47) / 131.0)

    tiempo.append(time.monotonic() - t0)

    for linea, datos in zip(lineas1, [ax, ay, az]):
        linea.set_data(list(tiempo), list(datos))

    for linea, datos in zip(lineas2, [gx, gy, gz]):
        linea.set_data(list(tiempo), list(datos))

    for grafica in (g1, g2):
        grafica.relim()
        grafica.autoscale_view()

    return lineas1 + lineas2


ani = FuncAnimation(
    fig, actualizar, interval=50,
    blit=False, cache_frame_data=False
)

plt.tight_layout()

try:
    plt.show()
finally:
    bus.close()
