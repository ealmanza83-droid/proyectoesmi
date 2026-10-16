import smbus2
import time

# ============================================================
# 1. CONFIGURACIÓN
# ============================================================

BUS = 1
DIRECCION = 0x68

bus = smbus2.SMBus(BUS)

# Encender MPU6050
bus.write_byte_data(DIRECCION, 0x6B, 0x00)


# ============================================================
# 2. CONVERSIÓN
# ============================================================

def convertir(high, low):

    valor = (high << 8) | low

    if valor >= 32768:
        valor -= 65536

    return valor


# ============================================================
# 3. LECTURA
# ============================================================

def leer_acelerometro():

    datos = bus.read_i2c_block_data(DIRECCION, 0x3B, 6)

    ax = convertir(datos[0], datos[1]) / 16384.0
    ay = convertir(datos[2], datos[3]) / 16384.0
    az = convertir(datos[4], datos[5]) / 16384.0

    return ax, ay, az


# ============================================================
# 4. PROGRAMA PRINCIPAL
# ============================================================

print("ORIENTACIÓN DEL MPU6050")
print("Inclina y gira el sensor para encontrar cada eje.")
print("Presiona Ctrl+C para terminar.\n")

try:

    while True:

        ax, ay, az = leer_acelerometro()

        print(
            f"AX = {ax:+.2f} g   "
            f"AY = {ay:+.2f} g   "
            f"AZ = {az:+.2f} g"
        )

        time.sleep(0.5)

except KeyboardInterrupt:

    print("\nPrograma terminado.")

bus.close()