from tkinter import Label, Tk
from gpiozero import DistanceSensor, PWMOutputDevice
from gpiozero.pins.lgpio import LGPIOFactory

# IMPORTANTE: Cambia el chip al que le funcione a tu Raspberry Pi 5 (ej. 4 o 1)
factory = LGPIOFactory(chip=15) # Sin poner un número dentro

# Sensor HC-SR04
sensor = DistanceSensor(
    echo=23, trigger=18, pin_factory=factory, max_distance=4
)

# Buzzer conectado al GPIO 24
buzzer = PWMOutputDevice(24, pin_factory=factory, frequency=2000)

# Ventana
ventana = Tk()
ventana.title("Sensor de Distancia - Raspberry Pi 5")
ventana.geometry("500x300")
ventana.configure(bg="black")

label = Label(
    ventana, text="Calculando...", font=("Helvetica", 32), fg="white", bg="black"
)

label.pack(expand=True)

# Estado del buzzer
buzzer_encendido = False


def controlar_buzzer():
  global buzzer_encendido

  distancia_cm = sensor.distance * 100

  # Más de 100 cm: apagado
  if distancia_cm > 100:
    buzzer.off()
    buzzer_encendido = False
    tiempo = 500

  # Entre 60 y 100 cm: pitido lento
  elif distancia_cm > 60:
    tiempo = 1000

  # Entre 30 y 60 cm: pitido medio
  elif distancia_cm > 30:
    tiempo = 500

  # Entre 15 y 30 cm: pitido rápido
  elif distancia_cm > 15:
    tiempo = 250

  # Menos de 15 cm: sonido continuo
  else:
    buzzer.on()
    buzzer_encendido = True
    tiempo = 100

  # Para los casos que son intermitentes
  if 15 < distancia_cm <= 100:
    if buzzer_encendido:
      buzzer.off()
      buzzer_encendido = False
    else:
      buzzer.on()
      buzzer_encendido = True

  ventana.after(tiempo, controlar_buzzer)


def update_reading():
  try:
    distancia_cm = round(sensor.distance * 100, 2)
    label.config(text=f"Distancia:\n{distancia_cm} cm")
  except Exception as e:
    print("Error:", e)
    buzzer.off()

  ventana.after(100, update_reading)


def cerrar():
  sensor.close()
  buzzer.close()
  factory.close()
  ventana.destroy()


ventana.protocol("WM_DELETE_WINDOW", cerrar)

# Iniciar funciones
update_reading()
controlar_buzzer()

# Mantener ventana abierta
ventana.mainloop()