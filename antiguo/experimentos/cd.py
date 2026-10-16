import csv
import statistics
import math

archivo = "caracterizacion_mpu6050.csv"

ax = []
ay = []
az = []

gx = []
gy = []
gz = []

with open(archivo, "r") as f:
    lector = csv.DictReader(f)

    for fila in lector:
        ax.append(int(fila["ax_raw"]))
        ay.append(int(fila["ay_raw"]))
        az.append(int(fila["az_raw"]))

        gx.append(int(fila["gx_raw"]))
        gy.append(int(fila["gy_raw"]))
        gz.append(int(fila["gz_raw"]))


def analizar(nombre, datos):
    promedio = statistics.mean(datos)
    desviacion = statistics.stdev(datos)
    minimo = min(datos)
    maximo = max(datos)

    print(nombre)
    print("  Promedio:", round(promedio, 2))
    print("  Desviacion estandar:", round(desviacion, 2))
    print("  Minimo:", minimo)
    print("  Maximo:", maximo)
    print()


print("==============================")
print("CARACTERIZACION MPU6050")
print("==============================")
print("Numero de muestras:", len(ax))
print()

print("ACELEROMETRO")
print("------------------------------")

analizar("AX", ax)
analizar("AY", ay)
analizar("AZ", az)

print("GIROSCOPIO")
print("------------------------------")

analizar("GX", gx)
analizar("GY", gy)
analizar("GZ", gz)


# Magnitud promedio de la aceleracion
magnitudes = []

for i in range(len(ax)):
    magnitud = math.sqrt(
        ax[i]**2 +
        ay[i]**2 +
        az[i]**2
    )

    magnitudes.append(magnitud)

print("ACELERACION RESULTANTE")
print("------------------------------")
print(
    "Promedio RAW:",
    round(statistics.mean(magnitudes), 2)
)

print("==============================")
print("ANALISIS TERMINADO")
print("==============================")