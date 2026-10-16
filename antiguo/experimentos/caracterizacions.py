import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# =====================================================
# 1. CARGAR LOS DATOS
# =====================================================

ARCHIVO = "datos_reposo.csv"

df = pd.read_csv(ARCHIVO)

print("=" * 50)
print("ANÁLISIS DE DATOS EN REPOSO")
print("=" * 50)

print("\nNúmero de muestras:", len(df))

print("\nColumnas:")
print(df.columns.tolist())

print("\nPrimeras 5 muestras:")
print(df.head())


# =====================================================
# 2. SEÑALES QUE VAMOS A ANALIZAR
# =====================================================

senales = [
    "ax_g",
    "ay_g",
    "az_g",
    "gx_dps",
    "gy_dps",
    "gz_dps"
]


# =====================================================
# 3. ESTADÍSTICAS
# =====================================================

print("\n")
print("=" * 70)
print("ESTADÍSTICAS DE LAS 6 SEÑALES")
print("=" * 70)

for señal in senales:

    if señal in df.columns:

        promedio = df[señal].mean()
        desviacion = df[señal].std()
        minimo = df[señal].min()
        maximo = df[señal].max()

        print("\nSeñal:", señal)
        print("Promedio:", round(promedio, 6))
        print("Desviación estándar:", round(desviacion, 6))
        print("Mínimo:", round(minimo, 6))
        print("Máximo:", round(maximo, 6))


# =====================================================
# 4. OFFSET
# =====================================================

print("\n")
print("=" * 70)
print("OFFSET")
print("=" * 70)

# En reposo:
# Ax y Ay deberían estar cerca de 0 g
# Az debería estar cerca de 1 g
# Giroscopio debería estar cerca de 0 °/s

offset_ax = df["ax_g"].mean() - 0
offset_ay = df["ay_g"].mean() - 0
offset_az = df["az_g"].mean() - 1

offset_gx = df["gx_dps"].mean() - 0
offset_gy = df["gy_dps"].mean() - 0
offset_gz = df["gz_dps"].mean() - 0

print("Offset Ax:", round(offset_ax, 6), "g")
print("Offset Ay:", round(offset_ay, 6), "g")
print("Offset Az:", round(offset_az, 6), "g")

print("Offset Gx:", round(offset_gx, 6), "°/s")
print("Offset Gy:", round(offset_gy, 6), "°/s")
print("Offset Gz:", round(offset_gz, 6), "°/s")


# =====================================================
# 5. RUIDO
# =====================================================

print("\n")
print("=" * 70)
print("RUIDO")
print("=" * 70)

print("Ruido Ax:", round(df["ax_g"].std(), 6), "g")
print("Ruido Ay:", round(df["ay_g"].std(), 6), "g")
print("Ruido Az:", round(df["az_g"].std(), 6), "g")

print("Ruido Gx:", round(df["gx_dps"].std(), 6), "°/s")
print("Ruido Gy:", round(df["gy_dps"].std(), 6), "°/s")
print("Ruido Gz:", round(df["gz_dps"].std(), 6), "°/s")


# =====================================================
# 6. MAGNITUD TOTAL DEL ACELERÓMETRO
# =====================================================

df["a_total_g"] = np.sqrt(
    df["ax_g"]**2 +
    df["ay_g"]**2 +
    df["az_g"]**2
)

print("\n")
print("=" * 70)
print("MAGNITUD TOTAL DEL ACELERÓMETRO")
print("=" * 70)

print("Promedio:",
      round(df["a_total_g"].mean(), 6), "g")

print("Desviación estándar:",
      round(df["a_total_g"].std(), 6), "g")

print("Mínimo:",
      round(df["a_total_g"].min(), 6), "g")

print("Máximo:",
      round(df["a_total_g"].max(), 6), "g")


# =====================================================
# 7. GRÁFICAS
# =====================================================

if "tiempo_s" in df.columns:
    eje_x = df["tiempo_s"]
    etiqueta_x = "Tiempo [s]"
else:
    eje_x = df["muestra"]
    etiqueta_x = "Muestra"


# ---------- ACELERÓMETRO ----------

for señal in ["ax_g", "ay_g", "az_g"]:

    plt.figure(figsize=(10, 4))

    plt.plot(eje_x, df[señal])

    plt.title("Señal " + señal)
    plt.xlabel(etiqueta_x)
    plt.ylabel("Aceleración [g]")

    plt.grid(True)
    plt.tight_layout()

    plt.show()


# ---------- GIROSCOPIO ----------

for señal in ["gx_dps", "gy_dps", "gz_dps"]:

    plt.figure(figsize=(10, 4))

    plt.plot(eje_x, df[señal])

    plt.title("Señal " + señal)
    plt.xlabel(etiqueta_x)
    plt.ylabel("Velocidad angular [°/s]")

    plt.grid(True)
    plt.tight_layout()

    plt.show()


# =====================================================
# 8. FINAL
# =====================================================

print("\n")
print("=" * 50)
print("ANÁLISIS TERMINADO")
print("=" * 50)