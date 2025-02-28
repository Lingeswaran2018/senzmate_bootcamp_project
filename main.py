import cv2
import time
import threading
from ultralytics import YOLO
from utills.deep_sort import DeepSort
import torch
import os
import yaml
from firebase_admin import firestore

with open(f'configs/config.yaml', 'r') as file:
    config = yaml.safe_load(file)

# Main configurations
input_video_file_name = config.get("input_video_file_name", "configs/pedestrian_human.mp4")
firebase_generated_json_file_name = config.get("firebase_generated_json_file_name", "project_key.json")
count_send_interval_in_seconds = config.get("count_send_interval_in_seconds", 60)
draw = config.get("draw", False)
show_output = config.get("show_output", False)

# YOLO model configurations
yolo_confidence_score = config.get("yolo_confidence_score", 0.3)
yolo_required_class_ids = config.get("yolo_required_class_ids", [0])  # 0 corresponds to 'person'
yolo_input_img_size = config.get("yolo_input_img_size", 640)

# DeepSORT configurations
max_dist = config.get("max_cosine_dist", 0.2)
nms_max_overlap = config.get("nms_max_overlap", 1.0)
max_iou_distance = config.get("max_iou_distance", 0.7)
max_age = config.get("max_age", 70)
n_init = config.get("n_init", 3)
nn_budget = config.get("nn_budget", 100)
use_cuda_for_deepsort = config.get("use_cuda_for_deepsort", False)

# Firebase setup
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f'configs/{firebase_generated_json_file_name}'
db = firestore.Client()
people_count_collection = db.collection("people_count")

# YOLO and DeepSORT initialization
model = YOLO('models/yolov8n.pt')
deepsort = DeepSort(
    model_path="utills/deep_sort/deep/checkpoint/ckpt.t7",
    max_dist=max_dist,
    min_confidence=yolo_confidence_score,
    nms_max_overlap=nms_max_overlap,
    max_iou_distance=max_iou_distance,
    max_age=max_age,
    n_init=n_init,
    nn_budget=nn_budget,
    use_cuda=use_cuda_for_deepsort)

cap = cv2.VideoCapture(f'configs/{input_video_file_name}')

people_counter = set()
lock = threading.Lock()

def send_people_count_to_firebase():
    global people_counter
    while True:
        try:
            time.sleep(count_send_interval_in_seconds)
            with lock:
                data = {"time_stamp": time.strftime("%Y-%m-%d_%H:%M:%S"), "people_count": len(people_counter), "tracking_ids": list(people_counter)}
                people_count_collection.add(data)
                print(f"People count sent: {len(people_counter)}")
                people_counter.clear()
        except Exception as e:
            print(f"Error sending data to Firebase: {e}")

# Start Firebase thread
threading.Thread(target=send_people_count_to_firebase, daemon=True).start()

try:
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        
        # YOLOv8 Detection
        results = model.predict(source=frame, conf=yolo_confidence_score, classes=yolo_required_class_ids, save=False, verbose=False, imgsz=yolo_input_img_size)
        if results[0].boxes is None:
            continue
        
        xywhs = results[0].boxes.xywh.cpu().numpy()
        scores = results[0].boxes.conf.cpu().numpy()
        class_ids = results[0].boxes.cls.cpu().numpy()
        
        if len(xywhs) == 0:
            continue
        
        # DeepSORT tracking
        tracks = deepsort.update(torch.Tensor(xywhs), scores, class_ids, frame)
        if tracks is None or len(tracks) == 0:
            continue
        
        identities = tracks[:, -2]
        
        with lock:
            for track_id in identities:
                people_counter.add(int(track_id))
        
        if show_output:
            cv2.imshow('People Counting', frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break

except KeyboardInterrupt:
    print("\nKeyboard interrupt detected! Stopping gracefully...")

finally:
    cap.release()
    if show_output:
        cv2.destroyAllWindows()
    print("Resources released successfully.")
