import socket 

client = socket.socket()
client.connect(('172.20.10.5',9998))
while True: 
	mensaje = input("Escribe mensaje:")
	client.send(mensaje.encode())