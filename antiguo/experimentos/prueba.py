import time
from gpiozero import DigitalOutputDevice

# --- CONFIGURACIÓN DE PINES (RPI CV) ---
# PIN 24 (Salida): Conectar al PIN 24 (Entrada FINISH) de la RPI JUEZ
pin_out_finish = DigitalOutputDevice(24, initial_value=False)

# RECUERDA: Conectar GND de esta Pi con el GND de la Pi Juez de forma obligatoria.

def bucle_procesamiento_cv():
    print("RPI CV: Esperando frames de la cámara...")
    
    # Ejemplo de estructura de tu bucle real (ej. while cap.isOpened():)
    while True:
        # 1. Tu cámara captura el frame actual
        # ret, frame = cap.read()
        
        # 2. Tu modelo de CV hace la inferencia
        # resultados = modelo.predict(frame)
        
        # --- SIMULACIÓN DE DETECCIÓN ---
        # Supongamos que tu código detectó el objeto y generó la etiqueta:
        objeto_detectado = True 
        etiqueta = "Objeto_Prueba"
        # -------------------------------
        
        # 3. Si se generó la etiqueta con éxito:
        if objeto_detectado and etiqueta is not None:
            # Enviamos el pulso inmediatamente a la RPI Juez para detener su cronómetro
            pin_out_finish.on()
            time.sleep(0.002) # Mantener el pulso 2ms para asegurar lectura
            pin_out_finish.off()
            
            print(f"Etiqueta '{etiqueta}' generada. Pulso de FINISH enviado.")
            
            # Nota para tu implementación: Tras enviar el FINISH, haz que tu código de CV
            # ignore los siguientes frames correspondientes a la pantalla negra inter-imagen
            # para evitar falsos positivos antes de la siguiente prueba.
            time.sleep(2.0) 

if __name__ == "__main__":
    try:
        bucle_procesamiento_cv()
    except KeyboardInterrupt:
        print("Sistema CV detenido.")