import time
from gpiozero import DigitalInputDevice, DigitalOutputDevice

# --- CONFIGURACIÓN DE PINES (RPI CV) ---
pin_in_start = DigitalInputDevice(23, pull_up=False)
pin_out_finish = DigitalOutputDevice(24, initial_value=False)

# RECUERDA: Conectar obligatoriamente un pin GND de esta Pi a un pin GND de la Pi Juez.

def bucle_cv_opcion_b():
    print("RPI CV lista. Esperando orden de la RPI Juez...")
    
    while True:
        # 1. Bloqueo eficiente en Linux: no consume CPU mientras espera el pulso
        pin_in_start.wait_for_active()
        
        print("\n¡Señal START recibida! Procesando frame inmediatamente...")
        
        # 2. Tu código de Visión Computacional actúa aquí de inmediato:
        # frame = cap.read()
        
        # --- SIMULACIÓN DE TIEMPO DE INFERENCIA ---
        time.sleep(0.035) # Simula que el modelo (YOLO, etc.) tardó 35ms
        etiqueta = "Objeto_Detectado"
        # ------------------------------------------
        
        # 3. En cuanto la lógica del software genera la etiqueta, disparamos FINISH
        if etiqueta is not None:
            pin_out_finish.on()
            time.sleep(0.005) # Sostener el pulso 5ms para asegurar detección en la otra Pi
            pin_out_finish.off()
            print(f"Etiqueta generada. Señal FINISH enviada a la Juez.")
            
        # Evitar procesamiento doble del mismo pulso
        # Esperamos a que la Juez baje la señal y limpie la pantalla antes de volver a escuchar
        time.sleep(1.5) 
        print("Esperando siguiente imagen...")

if __name__ == "__main__":
    try:
        bucle_cv_opcion_b()
    except KeyboardInterrupt:
        print("\nSistema CV detenido.")
 