import cv2

cap = cv2.VideoCapture(0)

while True:
    ret, frame = cap.read()
    print(ret)
    
    if not ret:
        print("No se pudo leer la cámara")
        break

    cv2.imshow("Camara",frame)
    if cv2.waitKey(1) == ord('q'):
        break
    
cap.release()
cv2.destroyAllWIndows()