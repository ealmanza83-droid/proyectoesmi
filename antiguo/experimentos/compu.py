import cv2
import socket
import struct

client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

client_socket.connect(('172.20.10.5', 9998))

cap = cv2.VideoCapture(0)

cap.set(3, 320)
cap.set(4,240)

while True:
    ret, frame = cap.read()
    print("RET",ret)
    
    if not ret:
        print("no se pudo leer el mensaje")
        break
    
    result, buffer = cv2.imencode('.jpg', frame)
    print("ENCODE",result)
    
    data = buffer.tobytes()
    size = len(data)
    print("TAMAÑO", size)
    
    try:
        client_socket.sendall(struct.pack("Q",size) + data)
    except Exception as e:
            print("ERROR:", e)
cap.release()
client_socket.close()