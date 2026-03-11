from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # ESP32-CAM
    esp32_stream_url: str = "http://192.168.1.100:81/stream"
    esp32_control_url: str = "http://192.168.1.100"
    esp32_timeout: float = 5.0

    # VIO Cloud LLM
    vio_base_url: str = "https://vio.automotive-wan.com:446"
    vio_username: str = "uih62283"
    vio_api_token: str = "VPImz2XnDYkC_aeEfQTK1iAwKOEDNoTrPulgF6XcH5E"
    vio_token_expiry: str = "2026-10-10"
    vio_primary_model: str = "claude-4-5-sonnet-v1:0"
    vio_ssl_verify: bool = True

    # MQTT Remote Connectivity
    mqtt_broker_host: str = "218a9f2b6e644efcba373298c08a6588.s1.eu.hivemq.cloud"
    mqtt_broker_port: int = 8883
    mqtt_username: str = "VJ-DOG-BOT"
    mqtt_password: str = "Dog-Bot123"
    mqtt_use_tls: bool = True

    # CV Pipeline
    cv_contour_min_area: int = 1500
    cv_canny_low: int = 80
    cv_canny_high: int = 200
    cv_blur_kernel: int = 5

    # CV Edge Detection (thin obstacles: desk legs, poles, wires)
    cv_edge_gradient_threshold: int = 80
    cv_edge_confidence: float = 0.3

    # Floor Plane Detection (adaptive floor color model)
    cv_floor_confidence: float = 0.25
    cv_floor_deviation_k: float = 2.5
    cv_floor_ema_alpha: float = 0.05

    # ML Detection
    ml_model_path: str = "models/footprint_yolov8n.pt"
    ml_confidence_threshold: float = 0.35
    ml_detect_every_n_frames: int = 1
    roboflow_api_key: str = "rf_TjjUjgJA6wUS6lzWRxTaf60WWxI3"
    roboflow_model: str = "foot-print-detection/2"

    # AI Decision
    ai_decision_interval: float = 0.066  # ~15 FPS
    ai_manual_pause_seconds: float = 5.0
    ai_command_reissue_interval: float = 0.5   # re-issue same direction at most every 500ms
    ai_analyze_duration: float = 3.0     # seconds to observe scene before acting
    ai_act_duration: float = 0.5         # seconds to execute motor command

    # Path Planner
    planner_grid_width_m: float = 3.0
    planner_grid_depth_m: float = 4.0
    planner_cell_size_m: float = 0.05
    planner_emergency_stop_dist_m: float = 0.25
    planner_estop_lateral_m: float = 0.18      # emergency stop corridor half-width (metres)
    planner_estop_distance_m: float = 0.28     # distance at which all paths become infeasible
    planner_blocked_threshold: int = 5         # frames before recovery turn engages
    planner_recovery_speed: float = 0.5        # motor power during recovery turn
    planner_recovery_steering: float = 0.8     # steering magnitude during recovery

    # Motor Drift Compensation
    motor_forward_steering_bias: float = 0.05  # +ve biases left (corrects rightward drift)

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
