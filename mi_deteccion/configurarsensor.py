import smbus2
import time
import numpy as np

# ============================================================
# 1. CONFIGURACIÓN
# ============================================================

BUS = 1
DIRECCION = 0x68

bus = smbus2.SMBus(BUS)

# Encender MPU6050
bus.write_byte_data(DIRECCION, 0x6B, 0x00)

# Giroscopio ±250 °/s
bus.write_byte_data(DIRECCION, 0x1B, 0x00)


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

def leer_giroscopio():

    datos = bus.read_i2c_block_data(DIRECCION, 0x43, 6)

    gx = convertir(datos[0], datos[1]) / 131.0
    gy = convertir(datos[2], datos[3]) / 131.0
    gz = convertir(datos[4], datos[5]) / 131.0

    return gx, gy, gz


# ============================================================
# 4. CALIBRACIÓN
# ============================================================

print("CALIBRACIÓN DEL GIROSCOPIO")
print()
print("Coloca el MPU6050 sobre una superficie estable.")
print("NO lo muevas durante la calibración.")
input("Presiona ENTER para comenzar...")

muestras = []

for i in range(1000):

    gx, gy, gz = leer_giroscopio()

    muestras.append([gx, gy, gz])

    time.sleep(0.005)

datos = np.array(muestras)

offset_x = np.mean(datos[:, 0])
offset_y = np.mean(datos[:, 1])
offset_z = np.mean(datos[:, 2])


# ============================================================
# 5. RESULTADOS
# ============================================================

print("\n" + "=" * 50)
print("RESULTADO DE LA CALIBRACIÓN")
print("=" * 50)

print(f"Offset GX = {offset_x:.6f} °/s")
print(f"Offset GY = {offset_y:.6f} °/s")
print(f"Offset GZ = {offset_z:.6f} °/s")

print("\nEstos valores representan el error del giroscopio")
print("cuando el sensor está completamente quieto.")

bus.close()