# ============================================================
# 1. CONFIGURACIÓN
# ============================================================

import smbus2
import time
import csv
import numpy as np

BUS = 1
DIRECCION = 0x68

# Registros
PWR_MGMT_1 = 0x6B
ACCEL_CONFIG = 0x1C
GYRO_CONFIG = 0x1B
ACCEL_XOUT_H = 0x3B

# Sensibilidades
ACCEL_SENS = 16384.0      # LSB/g para ±2 g
GYRO_SENS = 131.0         # LSB/(°/s) para ±250 °/s

N = 1000

ARCHIVO_CSV = "caracterizacion_MPU6050.csv"


# ============================================================
# 2. INICIALIZACIÓN
# ============================================================

bus = smbus2.SMBus(BUS)

# Despertar MPU6050
bus.write_byte_data(DIRECCION, PWR_MGMT_1, 0x00)

# Acelerómetro ±2 g
bus.write_byte_data(DIRECCION, ACCEL_CONFIG, 0x00)

# Giroscopio ±250 °/s
bus.write_byte_data(DIRECCION, GYRO_CONFIG, 0x00)

time.sleep(1)


# ============================================================
# 3. CONVERSIÓN
# ============================================================

def convertir(high, low):

    valor = (high << 8) | low

    if valor >= 32768:
        valor -= 65536

    return valor


# ============================================================
# 4. LECTURA
# ============================================================

def leer_sensor():

    datos = bus.read_i2c_block_data(
        DIRECCION,
        ACCEL_XOUT_H,
        14
    )

    # Acelerómetro
    ax_raw = convertir(datos[0], datos[1])
    ay_raw = convertir(datos[2], datos[3])
    az_raw = convertir(datos[4], datos[5])

    # Giroscopio
    gx_raw = convertir(datos[8], datos[9])
    gy_raw = convertir(datos[10], datos[11])
    gz_raw = convertir(datos[12], datos[13])

    # Convertir a unidades físicas
    ax = ax_raw / ACCEL_SENS
    ay = ay_raw / ACCEL_SENS
    az = az_raw / ACCEL_SENS

    gx = gx_raw / GYRO_SENS
    gy = gy_raw / GYRO_SENS
    gz = gz_raw / GYRO_SENS

    return ax, ay, az, gx, gy, gz


# ============================================================
# 5. MEDICIÓN
# ============================================================

def medir():

    AX, AY, AZ = [], [], []
    GX, GY, GZ = [], [], []

    for i in range(N):

        ax, ay, az, gx, gy, gz = leer_sensor()

        AX.append(ax)
        AY.append(ay)
        AZ.append(az)

        GX.append(gx)
        GY.append(gy)
        GZ.append(gz)

        time.sleep(0.005)

    return {
        "AX_prom": np.mean(AX),
        "AX_std": np.std(AX, ddof=1),
        "AX_min": np.min(AX),
        "AX_max": np.max(AX),

        "AY_prom": np.mean(AY),
        "AY_std": np.std(AY, ddof=1),
        "AY_min": np.min(AY),
        "AY_max": np.max(AY),

        "AZ_prom": np.mean(AZ),
        "AZ_std": np.std(AZ, ddof=1),
        "AZ_min": np.min(AZ),
        "AZ_max": np.max(AZ),

        "GX_prom": np.mean(GX),
        "GX_std": np.std(GX, ddof=1),
        "GX_min": np.min(GX),
        "GX_max": np.max(GX),

        "GY_prom": np.mean(GY),
        "GY_std": np.std(GY, ddof=1),
        "GY_min": np.min(GY),
        "GY_max": np.max(GY),

        "GZ_prom": np.mean(GZ),
        "GZ_std": np.std(GZ, ddof=1),
        "GZ_min": np.min(GZ),
        "GZ_max": np.max(GZ)
    }


# ============================================================
# 6. ACELERÓMETRO
# ============================================================

posiciones = [
    ("+X", "AX", 1),
    ("-X", "AX", -1),
    ("+Y", "AY", 1),
    ("-Y", "AY", -1),
    ("+Z", "AZ", 1),
    ("-Z", "AZ", -1)
]

resultados_accel = []

for posicion, eje, ideal in posiciones:

    input(
        f"\nColoca el MPU6050 en {posicion} "
        "y presiona ENTER..."
    )

    resultado = medir()

    promedio = resultado[eje + "_prom"]
    desviacion = resultado[eje + "_std"]
    minimo = resultado[eje + "_min"]
    maximo = resultado[eje + "_max"]

    # Error respecto a ±1 g
    error = abs(promedio - ideal) * 100

    resultados_accel.append({
        "Posicion": posicion,
        "Eje": eje,
        "Promedio": promedio,
        "Desviacion": desviacion,
        "Minimo": minimo,
        "Maximo": maximo,
        "Ideal": ideal,
        "Error": error
    })

    print("\nResultado")
    print(f"Eje: {eje}")
    print(f"Promedio = {promedio:.5f} g")
    print(f"Desviación estándar = {desviacion:.5f} g")
    print(f"Mínimo = {minimo:.5f} g")
    print(f"Máximo = {maximo:.5f} g")
    print(f"Error = {error:.3f} %")


# Bias y factor de escala

AX_pos = resultados_accel[0]["Promedio"]
AX_neg = resultados_accel[1]["Promedio"]

AY_pos = resultados_accel[2]["Promedio"]
AY_neg = resultados_accel[3]["Promedio"]

AZ_pos = resultados_accel[4]["Promedio"]
AZ_neg = resultados_accel[5]["Promedio"]

bias_AX = (AX_pos + AX_neg) / 2
bias_AY = (AY_pos + AY_neg) / 2
bias_AZ = (AZ_pos + AZ_neg) / 2

escala_AX = (AX_pos - AX_neg) / 2
escala_AY = (AY_pos - AY_neg) / 2
escala_AZ = (AZ_pos - AZ_neg) / 2

print("\n")
print("=" * 60)
print("CARACTERIZACIÓN DEL ACELERÓMETRO")
print("=" * 60)

print(f"\nAX: Bias = {bias_AX:.6f} g")
print(f"AX: Factor de escala = {escala_AX:.6f}")

print(f"\nAY: Bias = {bias_AY:.6f} g")
print(f"AY: Factor de escala = {escala_AY:.6f}")

print(f"\nAZ: Bias = {bias_AZ:.6f} g")
print(f"AZ: Factor de escala = {escala_AZ:.6f}")


# ============================================================
# 7. GIROSCOPIO
# ============================================================

print("\n")
print("=" * 60)
print("CARACTERIZACIÓN DEL GIROSCOPIO")
print("=" * 60)

print("\nDeja el MPU6050 completamente quieto.")

input(
    "Presiona ENTER para comenzar las 1000 muestras..."
)

resultado_gyro = medir()

print("\nGX")
print(f"Promedio = {resultado_gyro['GX_prom']:.5f} °/s")
print(f"Desviación estándar = {resultado_gyro['GX_std']:.5f} °/s")
print(f"Mínimo = {resultado_gyro['GX_min']:.5f} °/s")
print(f"Máximo = {resultado_gyro['GX_max']:.5f} °/s")

print("\nGY")
print(f"Promedio = {resultado_gyro['GY_prom']:.5f} °/s")
print(f"Desviación estándar = {resultado_gyro['GY_std']:.5f} °/s")
print(f"Mínimo = {resultado_gyro['GY_min']:.5f} °/s")
print(f"Máximo = {resultado_gyro['GY_max']:.5f} °/s")

print("\nGZ")
print(f"Promedio = {resultado_gyro['GZ_prom']:.5f} °/s")
print(f"Desviación estándar = {resultado_gyro['GZ_std']:.5f} °/s")
print(f"Mínimo = {resultado_gyro['GZ_min']:.5f} °/s")
print(f"Máximo = {resultado_gyro['GZ_max']:.5f} °/s")


# ============================================================
# 8. COMPARACIÓN
# ============================================================

print("\n")
print("=" * 60)
print("COMPARACIÓN")
print("=" * 60)

print("\nACELERÓMETRO")

print(
    f"AX -> +X: {AX_pos:.5f} g | "
    f"-X: {AX_neg:.5f} g | "
    f"Bias: {bias_AX:.5f} g"
)

print(
    f"AY -> +Y: {AY_pos:.5f} g | "
    f"-Y: {AY_neg:.5f} g | "
    f"Bias: {bias_AY:.5f} g"
)

print(
    f"AZ -> +Z: {AZ_pos:.5f} g | "
    f"-Z: {AZ_neg:.5f} g | "
    f"Bias: {bias_AZ:.5f} g"
)

print("\nGIROSCOPIO EN REPOSO")

print(
    f"GX -> Promedio: "
    f"{resultado_gyro['GX_prom']:.5f} °/s | "
    f"Desv. estándar: "
    f"{resultado_gyro['GX_std']:.5f} °/s"
)

print(
    f"GY -> Promedio: "
    f"{resultado_gyro['GY_prom']:.5f} °/s | "
    f"Desv. estándar: "
    f"{resultado_gyro['GY_std']:.5f} °/s"
)

print(
    f"GZ -> Promedio: "
    f"{resultado_gyro['GZ_prom']:.5f} °/s | "
    f"Desv. estándar: "
    f"{resultado_gyro['GZ_std']:.5f} °/s"
)


# ============================================================
# 9. CSV
# ============================================================

with open(
    ARCHIVO_CSV,
    "w",
    newline=""
) as archivo:

    escritor = csv.writer(archivo)

    escritor.writerow([
        "Tipo",
        "Posicion",
        "Eje",
        "Promedio",
        "Desviacion_estandar",
        "Minimo",
        "Maximo",
        "Bias_Offset",
        "Factor_escala",
        "Error"
    ])

    # Resultados del acelerómetro
    for r in resultados_accel:

        if r["Eje"] == "AX":
            bias = bias_AX
            escala = escala_AX

        elif r["Eje"] == "AY":
            bias = bias_AY
            escala = escala_AY

        else:
            bias = bias_AZ
            escala = escala_AZ

        escritor.writerow([
            "Acelerometro",
            r["Posicion"],
            r["Eje"],
            r["Promedio"],
            r["Desviacion"],
            r["Minimo"],
            r["Maximo"],
            bias,
            escala,
            r["Error"]
        ])

    # Resultados del giroscopio
    escritor.writerow([
        "Giroscopio",
        "QUIETO",
        "GX",
        resultado_gyro["GX_prom"],
        resultado_gyro["GX_std"],
        resultado_gyro["GX_min"],
        resultado_gyro["GX_max"],
        "",
        "",
        ""
    ])

    escritor.writerow([
        "Giroscopio",
        "QUIETO",
        "GY",
        resultado_gyro["GY_prom"],
        resultado_gyro["GY_std"],
        resultado_gyro["GY_min"],
        resultado_gyro["GY_max"],
        "",
        "",
        ""
    ])

    escritor.writerow([
        "Giroscopio",
        "QUIETO",
        "GZ",
        resultado_gyro["GZ_prom"],
        resultado_gyro["GZ_std"],
        resultado_gyro["GZ_min"],
        resultado_gyro["GZ_max"],
        "",
        "",
        ""
    ])

print(f"\nCSV guardado como: {ARCHIVO_CSV}")


# ============================================================
# 10. PROGRAMA PRINCIPAL
# ============================================================

print("\n")
print("=" * 60)
print("CARACTERIZACIÓN DEL MPU6050 TERMINADA")
print("=" * 60)

bus.close()

print("\nComunicación I2C cerrada correctamente.")