import cv2
import numpy as np

# --- CONFIGURACIÓN DE ARCHIVOS ---
# Cambia estos nombres por los archivos que ya tienes en tu carpeta
PROTOTXT = "MobileNetSSD_deploy.prototxt.txt"
MODEL = "MobileNetSSD_deploy.caffemodel"

# Clases de MobileNetSSD
classes = {0:"background", 1:"aeroplane", 2:"bicycle", 3:"bird", 4:"boat",
           5:"bottle", 6:"bus", 7:"car", 8:"cat", 9:"chair", 10:"cow",
           11:"diningtable", 12:"dog", 13:"horse", 14:"motorbike",
           15:"person", 16:"pottedplant", 17:"sheep", 18:"sofa",
           19:"train", 20:"tvmonitor"}

# Cargar el modelo
net = cv2.dnn.readNetFromCaffe(PROTOTXT, MODEL)

def detectar_objetos(img_crop, x_offset, y_offset, original_w, original_h):
    """Realiza la detección en un recorte y mapea las coordenadas a la imagen original"""
    (h, w) = img_crop.shape[:2]
    blob = cv2.dnn.blobFromImage(cv2.resize(img_crop, (300, 300)), 0.007843, (300, 300), 127.5)
    net.setInput(blob)
    detections = net.forward()
   
    results = []
    for i in range(detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        if confidence > 0.5: # Umbral de confianza
            idx = int(detections[0, 0, i, 1])
            # Escalar coordenadas al tamaño del recorte y sumar el offset
            box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
            (startX, startY, endX, endY) = box.astype("int")
           
            # Ajustar a coordenadas de la imagen global
            results.append({
                "box": [startX + x_offset, startY + y_offset, endX + x_offset, endY + y_offset],
                "confidence": float(confidence),
                "label": classes[idx]
            })
    return results

# --- PROCESAMIENTO DE IMAGEN ---
# Carga tu imagen (ejemplo: 'foto.jpg')
image = cv2.imread("silla.jpg")
if image is None:
    print("Error: No se encontró la imagen.")
    exit()

orig_h, orig_w = image.shape[:2]
final_detections = []

# 1. Detección en la imagen completa (objetos grandes)
final_detections.extend(detectar_objetos(image, 0, 0, orig_w, orig_h))

# 2. Búsqueda por secciones (Objetos pequeños/lejanos)
# Dividimos la imagen en 4 cuadrantes con un pequeño solapamiento (intercalado)
step_w, step_h = orig_w // 2, orig_h // 2
overlap = 50 # Píxeles de intercalado

for y in range(0, orig_h - step_h + 1, step_h - overlap):
    for x in range(0, orig_w - step_w + 1, step_w - overlap):
        crop = image[y:y+step_h, x:x+step_w]
        final_detections.extend(detectar_objetos(crop, x, y, orig_w, orig_h))

# --- DIBUJAR RESULTADOS UNIFICADOS ---
for det in final_detections:
    (startX, startY, endX, endY) = det["box"]
    label = f"{det['label']}: {det['confidence']:.2f}"
   
    cv2.rectangle(image, (startX, startY), (endX, endY), (0, 255, 0), 2)
    y_pos = startY - 15 if startY - 15 > 15 else startY + 15
    cv2.putText(image, label, (startX, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

# Mostrar resultado (Estándar de Raspberry/PC)
cv2.imshow("Deteccion por Secciones", image)
print("Presiona cualquier tecla para cerrar...")
cv2.waitKey(0)
cv2.destroyAllWindows()