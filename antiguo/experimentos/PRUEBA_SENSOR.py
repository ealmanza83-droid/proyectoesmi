from gpiozero import DistanceSensor
from gpiozero.pins.lgpio import LGPIOFactory
from tkinter import Tk, Label

# Usar el gpiochip que funciona en esta Raspberry Pi 5
factory = LGPIOFactory(chip=15)

# Sensor HC-SR04
sensor = DistanceSensor(
    echo=23,
    trigger=18,
    pin_factory=factory,
    max_distance=4
)

# Ventana
ventana = Tk()
ventana.title("Sensor de Distancia - Raspberry Pi 5")
ventana.geometry("500x300")
ventana.configure(bg="black")

label = Label(
    ventana,
    text="Calculando...",
    font=("Helvetica", 32),
    fg="white",
    bg="black"
)
label.pack(expand=True)


def update_reading():
    try:
        distancia_cm = round(sensor.distance * 100, 2)
        label.config(text=f"Distancia:\n{distancia_cm} cm")
    except Exception as e:
        label.config(text=f"Error:\n{e}")

    ventana.after(500, update_reading)


def cerrar():
    sensor.close()
    factory.close()
    ventana.destroy()


ventana.protocol("WM_DELETE_WINDOW", cerrar)

update_reading()
ventana.mainloop()
