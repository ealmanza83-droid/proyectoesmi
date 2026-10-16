import cv2
import time
from gpiozero import DigitalInputDevice, DigitalOutputDevice, Device
from gpiozero.pins.lgpio import LGPIOFactory
from ultralytics import YOLO

# --- CONFIGURACIÓN DE SEGURIDAD PARA RPI 4 ---
Device.pin_factory = LGPIOFactory()

# --- CONFIGURACIÓN DE PINES (RPI CV) ---
pin_in_start = DigitalInputDevice(23, pull_up=False)
pin_out_finish = DigitalOutputDevice(24, initial_value=False)

# --- INICIALIZACIÓN DE MODELO Y CÁMARA ---
print("Cargando modelo YOLO...")
model = YOLO("yolo11n_ncnn_model")

print("Inicializando cámara...")
cap = cv2.VideoCapture(0)

def bucle_cv_opcion_b():
    print("\nRPI CV lista. Esperando orden de la RPI Juez...")
    prev_time = time.time()
    
    if not cap.isOpened():
        print("Error: No se pudo acceder a la cámara.")
        return

    while True:
        # 1. Espera el pulso de la Juez sin consumir CPU
        pin_in_start.wait_for_active()
        
        print("\n¡Señal START recibida! Procesando frame inmediatamente...")
        
        # 2. Captura del frame
        ret, frame = cap.read()
        if not ret:
            print("Error al capturar el frame de la cámara.")
            continue

        # 3. Inferencia con YOLO
        results = model(frame, conf=0.65)
        
        # --- EXTRACCIÓN DE LA ETIQUETA REAL ---
        etiqueta_detectada = None
        
        # Si hay cajas de detección en el resultado
        if len(results[0].boxes) > 0:
            # Tomamos la primera detección (puedes cambiar el índice si necesitas otras)
            box = results[0].boxes[0]
            cls_id = int(box.cls[0])           # ID numérico de la clase
            etiqueta_detectada = model.names[cls_id]  # Nombre real (ej: "person", "cat")
            
        # 4. Acción por hardware e impresiones en Shell si hay detección
        if etiqueta_detectada is not None:
            # Enviamos el pulso físico inmediatamente (prioridad alta)
            pin_out_finish.on()
            time.sleep(0.005)  
            pin_out_finish.off()
            
            # Imprime en el Shell el nombre del objeto detectado
            print(f"[{etiqueta_detectada.upper()}] detectado. Señal FINISH enviada a la Juez.")
        else:
            print("No se detectó ningún objeto en este frame.")

        # 5. Dibujar en la imagen de OpenCV
        annotated_frame = results[0].plot()
        
        # Calcular FPS
        current_time = time.time()
        fps = 1 / (current_time - prev_time) if (current_time - prev_time) > 0 else 0
        prev_time = current_time

        # Texto para los FPS
        cv2.putText(
            annotated_frame,
            f"FPS: {int(fps)}",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 0),
            2
        )
        
        # Texto para la Etiqueta (Si existe, la pinta en la esquina superior derecha)
        texto_etiqueta = f"Clase: {etiqueta_detectada}" if etiqueta_detectada else "Clase: Ninguna"
        cv2.putText(
            annotated_frame,
            texto_etiqueta,
            (20, 80), # Un poco más abajo del texto de FPS
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (255, 0, 0) if etiqueta_detectada else (0, 0, 255), # Azul si detecta, Rojo si no
            2
        )

        # Mostrar ventana
        cv2.imshow("YOLO Inference", annotated_frame)

        if cv2.waitKey(1) & 0xFF == 27:
            break
            
        # 6. Evitar procesamiento doble
        time.sleep(1.5) 
        print("Esperando siguiente imagen...")

if __name__ == "__main__":
    try:
        bucle_cv_opcion_b()
    except KeyboardInterrupt:
        print("\nSistema CV detenido por el usuario.")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("Recursos liberados.")