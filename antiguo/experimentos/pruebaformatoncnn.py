import cv2
from ultralytics import YOLO

model = YOLO("yolo11n_ncnn_model", task="detect")

cap = cv2.VideoCapture(0)

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    results = model(frame, conf=0.75)
    annotated_frame = results[0].plot()

    cv2.imshow("YOLO Inference", annotated_frame)

    if cv2.waitKey(1) & 0xFF == 27:
        break

cap.release()
cv2.destroyAllWindows()