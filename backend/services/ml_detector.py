import asyncio
import logging
import time
import cv2
import numpy as np
import httpx

from backend.config import settings
from backend.models.schemas import Detection, BBox

logger = logging.getLogger("dogbot.ml")


class MLDetector:
    def __init__(self):
        self._model = None
        self._use_local = False
        self._frame_count = 0
        self._latest_detections: list[Detection] = []
        self._detect_interval = settings.ml_detect_every_n_frames
        self._tracker_type = "bytetrack.yaml"  # Built-in Ultralytics tracker

    async def start(self):
        """Load YOLOv8 segmentation model (auto-downloads if missing)."""
        try:
            from ultralytics import YOLO
            # Use Nano Segmentation model for real-time performance on CPU
            self._model = YOLO("yolov8n-seg.pt") 
            self._use_local = True
            logger.info("YOLOv8n-seg (Segmentation) model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load YOLOv8 model: {e}")
            self._use_local = False

    @property
    def latest_detections(self) -> list[Detection]:
        return self._latest_detections

    def _classify_zone(self, cy: int, height: int) -> str:
        ratio = cy / height
        if ratio > 0.75:
            return "DANGER"
        elif ratio > 0.50:
            return "CAUTION"
        return "SAFE"

    async def detect(self, frame: np.ndarray) -> list[Detection]:
        """Run YOLOv8 segmentation and tracking."""
        self._frame_count += 1
        if not self._use_local:
            return []
            
        # Skip frames for performance (if needed)
        if self._frame_count % self._detect_interval != 0:
            return self._latest_detections

        detections = await self._detect_local(frame)
        self._latest_detections = detections
        return detections

    async def _detect_local(self, frame: np.ndarray) -> list[Detection]:
        """Run YOLOv8 segmentation inference locally."""
        try:
            loop = asyncio.get_event_loop()
            
            # Run inference in executor to avoid blocking asyncio loop
            # mode='track' enables the built-in tracker (BoT-SORT or ByteTrack)
            results = await loop.run_in_executor(
                None,
                lambda: self._model.track(
                    frame,
                    persist=True,
                    conf=settings.ml_confidence_threshold,
                    verbose=False,
                    tracker=self._tracker_type,
                    retina_masks=False  # Faster masks
                )
            )

            h, w = frame.shape[:2]
            detections = []
            
            # Process results (usually just one frame)
            for r in results:
                boxes = r.boxes
                masks = r.masks
                
                if boxes is None:
                    continue
                
                # Iterate through detected objects
                for i, box in enumerate(boxes):
                    # Bounding Box
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                    conf = float(box.conf[0])
                    cls_id = int(box.cls[0])
                    cls_name = r.names.get(cls_id, f"class_{cls_id}")
                    
                    # Tracking ID (if available)
                    track_id = int(box.id[0]) if box.id is not None else 0
                    
                    # Zone classification based on bottom-center
                    cy = y2
                    zone = self._classify_zone(cy, h)
                    
                    # Create detection object
                    # We store mask polygon if available, otherwise just box
                    # Prepare optional fields
                    mask_points = None
                    tid = None
                    
                    if masks is not None:
                        # Convert numpy mask to list of points for Pydantic
                        mask_points = masks.xy[i].astype(int).tolist()
                        tid = track_id

                    detection = Detection(
                        class_name=cls_name,
                        confidence=round(conf, 3),
                        bbox=BBox(x=x1, y=y1, w=x2-x1, h=y2-y1),
                        zone=zone,
                        mask=mask_points,
                        track_id=tid
                    )
                    
                    detections.append(detection)

            return detections

        except Exception as e:
            logger.error(f"YOLO segmentation error: {e}")
            return []

    def draw_detections(self, frame: np.ndarray, detections: list[Detection]) -> np.ndarray:
        """Draw segmentation masks and boxes."""
        overlay = frame.copy()
        alpha = 0.4  # Transparency factor
        
        for det in detections:
            # Draw Segmentation Mask if available
            if det.mask is not None:
                # Color based on zone
                color = (0, 255, 0) # Green default
                if det.zone == "DANGER":
                    color = (0, 0, 255) # Red
                elif det.zone == "CAUTION":
                    color = (0, 255, 255) # Yellow
                
                # Convert list points back to numpy for OpenCV
                mask_cnt = np.array(det.mask, dtype=np.int32)
                
                # Fill polygon
                cv2.fillPoly(overlay, [mask_cnt], color)
                
                # Draw border
                cv2.polylines(overlay, [mask_cnt], True, color, 2)
                
                # Label with ID
                tid = det.track_id if det.track_id is not None else "?"
                centroid = np.mean(mask_cnt, axis=0).astype(int)
                cv2.putText(frame, f"ID:{tid} {det.class_name}", 
                           (centroid[0], centroid[1]), cv2.FONT_HERSHEY_SIMPLEX, 
                           0.5, (255, 255, 255), 2)
            else:
                # Fallback to standard box drawing
                b = det.bbox
                cv2.rectangle(frame, (b.x, b.y), (b.x+b.w, b.y+b.h), (0, 255, 0), 2)

        # Apply transparency
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
        return frame
