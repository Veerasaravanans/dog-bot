/*
 * DogBot Recon System - ESP32-CAM Firmware
 * MJPEG streaming + L293D motor control
 * Board: AI-Thinker ESP32-CAM
 */

#include "esp_camera.h"
#include "esp_system.h"
#include <WiFi.h>
#include <WebServer.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>

// ==================== WiFi Config ====================
const char* ssid = "mini_bot";
const char* password = "bot12345";

// ==================== MQTT Config ====================
const char* mqtt_server   = "218a9f2b6e644efcba373298c08a6588.s1.eu.hivemq.cloud";   // e.g. broker.hivemq.com
const int   mqtt_port     = 8883;
const char* mqtt_user     = "VJ-DOG-BOT";
const char* mqtt_pass     = "Dog-Bot123";

// MQTT Topics
const char* TOPIC_CMD_MOTOR  = "dogbot/cmd/motor";
const char* TOPIC_STATUS     = "dogbot/status";
const char* TOPIC_HEARTBEAT  = "dogbot/heartbeat";

// ==================== AI-Thinker ESP32-CAM Pins ====================
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ==================== L298N Motor Driver Pins ====================
// L298N module with PWM speed control via ENABLE pins
// IMPORTANT: Remove both ENABLE jumpers on L298N module to enable PWM control
// ESP32-CAM left header (top→bottom): 5V, GND, IO12, IO13, IO15, IO14, IO2, IO4
// Motor A = Left motors (IN1, IN2, ENABLE A)
// Motor B = Right motors (IN3, IN4, ENABLE B)

// Direction Control Pins
#define IN1_PIN    12  // Direction - Left motors (Motor A Input 1)
#define IN2_PIN    13  // Direction - Left motors (Motor A Input 2)
#define IN3_PIN    14  // Direction - Right motors (Motor B Input 3)
#define IN4_PIN    15  // Direction - Right motors (Motor B Input 4)

// PWM Speed Control Pins (ENABLE pins)
#define ENA_PIN     2  // PWM Speed - Left motors (Motor A Enable)
#define ENB_PIN     4  // PWM Speed - Right motors (Motor B Enable)

// PWM Configuration
#define PWM_FREQ      1000   // 1 kHz PWM frequency (suitable for DC motors)
#define PWM_RESOLUTION  8    // 8-bit resolution (0-255)
#define PWM_CHANNEL_A   0    // LEDC channel for Motor A
#define PWM_CHANNEL_B   1    // LEDC channel for Motor B

// ==================== Servers ====================
WebServer controlServer(80);
WiFiServer streamServer(81);

// ==================== MQTT ====================
WiFiClientSecure mqttWifiClient;
PubSubClient mqttClient(mqttWifiClient);
unsigned long mqttLastReconnectAttempt = 0;
unsigned long mqttLastStatusPublish = 0;
unsigned long mqttLastHeartbeat = 0;
bool mqttEnabled = false;

// Motor balance trim — compensates hardware drift when going straight.
// If bot drifts RIGHT: right motor is overpowered → reduce it (positive MOTOR_TRIM).
// If bot drifts LEFT:  left motor is overpowered → use negative MOTOR_TRIM.
// Tune: start at 15, drive 2m straight, adjust ±5 per ~5cm of drift.
const int MOTOR_TRIM = 15;

// Motor state
String currentMotorState = "stop";
unsigned long motorLastCmd = 0;
int currentSpeed = 100;  // Current motor speed (0-255)
bool motorPWMInitialized = false;  // Track if PWM has been set up

// ==================== Motor Functions ====================
// L298N module with PWM speed control via ENABLE pins
// IMPORTANT: Remove ENABLE jumpers on L298N module before use

void motorSetup() {
  Serial.println("Starting motor setup...");

  // Configure direction control pins
  pinMode(IN1_PIN, OUTPUT);
  pinMode(IN2_PIN, OUTPUT);
  pinMode(IN3_PIN, OUTPUT);
  pinMode(IN4_PIN, OUTPUT);
  Serial.println("Direction pins configured");

  // Initialize with motors stopped (no PWM setup yet to avoid conflicts)
  digitalWrite(IN1_PIN, LOW);
  digitalWrite(IN2_PIN, LOW);
  digitalWrite(IN3_PIN, LOW);
  digitalWrite(IN4_PIN, LOW);

  // Configure PWM AFTER WiFi is started to avoid initialization conflicts
  // Will be done in loop() on first motor command
  Serial.println("Motor pins initialized (PWM will be configured on first use)");
}

// Initialize PWM on first motor use (lazy initialization)
void ensureMotorPWMInitialized() {
  if (!motorPWMInitialized) {
    Serial.println("First motor use - initializing PWM...");
    ledcAttach(ENA_PIN, PWM_FREQ, PWM_RESOLUTION);  // Motor A (Left)
    ledcAttach(ENB_PIN, PWM_FREQ, PWM_RESOLUTION);  // Motor B (Right)
    motorPWMInitialized = true;
    Serial.println("Motor PWM initialized");
  }
}

// Set Motor A speed (0-255)
void motorASpeed(int speed) {
  ensureMotorPWMInitialized();
  speed = constrain(speed, 0, 255);
  ledcWrite(ENA_PIN, speed);
}

// Set Motor B speed (0-255)
void motorBSpeed(int speed) {
  ensureMotorPWMInitialized();
  speed = constrain(speed, 0, 255);
  ledcWrite(ENB_PIN, speed);
}

// Motor A = Left motors (IN1, IN2, ENA)
void motorA(bool forward, int speed) {
  digitalWrite(IN1_PIN, forward ? HIGH : LOW);
  digitalWrite(IN2_PIN, forward ? LOW : HIGH);
  motorASpeed(speed);
}

// Motor B = Right motors (IN3, IN4, ENB)
void motorB(bool forward, int speed) {
  digitalWrite(IN3_PIN, forward ? HIGH : LOW);
  digitalWrite(IN4_PIN, forward ? LOW : HIGH);
  motorBSpeed(speed);
}

void motorStop() {
  // Stop by setting speed to 0 (keeps direction pins as-is)
  motorASpeed(0);
  motorBSpeed(0);
  // Also set direction pins LOW for safety
  digitalWrite(IN1_PIN, LOW);
  digitalWrite(IN2_PIN, LOW);
  digitalWrite(IN3_PIN, LOW);
  digitalWrite(IN4_PIN, LOW);
  currentMotorState = "stop";
}

void motorForward(int speed) {
  // Apply trim: reduce right motor (B) to correct rightward drift
  motorA(true, speed);
  motorB(true, constrain(speed - MOTOR_TRIM, 0, 255));
  currentMotorState = "forward";
  currentSpeed = speed;
}

void motorBack(int speed) {
  // Apply trim: reduce right motor (B) to correct rightward drift
  motorA(false, speed);
  motorB(false, constrain(speed - MOTOR_TRIM, 0, 255));
  currentMotorState = "back";
  currentSpeed = speed;
}

void motorLeft(int speed) {
  // Tank turn: A backward, B forward
  motorA(false, speed);
  motorB(true, speed);
  currentMotorState = "left";
  currentSpeed = speed;
}

void motorRight(int speed) {
  // Tank turn: A forward, B backward
  motorA(true, speed);
  motorB(false, speed);
  currentMotorState = "right";
  currentSpeed = speed;
}

// ==================== MQTT Functions ====================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) {
    msg += (char)payload[i];
  }

  if (String(topic) == TOPIC_CMD_MOTOR) {
    // Parse JSON: {"dir":"forward", "speed":200}
    int dirStart = msg.indexOf("\"dir\":\"");
    if (dirStart == -1) return;
    dirStart += 7;
    int dirEnd = msg.indexOf("\"", dirStart);
    if (dirEnd == -1) return;
    String dir = msg.substring(dirStart, dirEnd);
    dir.toLowerCase();

    // Parse speed parameter (default 200 if not specified)
    int speed = 200;
    int speedStart = msg.indexOf("\"speed\":");
    if (speedStart != -1) {
      speedStart += 8;
      int speedEnd = msg.indexOf(",", speedStart);
      if (speedEnd == -1) speedEnd = msg.indexOf("}", speedStart);
      if (speedEnd != -1) {
        String speedStr = msg.substring(speedStart, speedEnd);
        speedStr.trim();
        speed = speedStr.toInt();
        speed = constrain(speed, 0, 255);
      }
    }

    if (dir == "forward")     motorForward(speed);
    else if (dir == "back")   motorBack(speed);
    else if (dir == "left")   motorLeft(speed);
    else if (dir == "right")  motorRight(speed);
    else if (dir == "stop")   motorStop();
    else return;

    motorLastCmd = millis();
    Serial.printf("MQTT motor: %s (speed: %d)\n", dir.c_str(), speed);
  }
}

bool mqttReconnect() {
  String clientId = "dogbot-esp32-" + String(random(0xffff), HEX);
  if (mqttClient.connect(clientId.c_str(), mqtt_user, mqtt_pass)) {
    Serial.println("MQTT connected");
    mqttClient.subscribe(TOPIC_CMD_MOTOR, 1);
    return true;
  }
  Serial.printf("MQTT connect failed, rc=%d\n", mqttClient.state());
  return false;
}

void mqttPublishStatus() {
  if (!mqttClient.connected()) return;

  long rssi = WiFi.RSSI();
  unsigned long uptime = millis() / 1000;

  String json = "{";
  json += "\"rssi\":" + String(rssi) + ",";
  json += "\"uptime\":" + String(uptime) + ",";
  json += "\"motor\":\"" + currentMotorState + "\",";
  json += "\"speed\":" + String(currentSpeed) + ",";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"mqtt\":true,";
  json += "\"driver\":\"L298N\"";
  json += "}";

  mqttClient.publish(TOPIC_STATUS, json.c_str());
}

void mqttPublishHeartbeat() {
  if (!mqttClient.connected()) return;
  mqttClient.publish(TOPIC_HEARTBEAT, "{\"alive\":true}");
}

// ==================== HTTP Handlers ====================
void handleMotor() {
  controlServer.sendHeader("Access-Control-Allow-Origin", "*");

  if (!controlServer.hasArg("dir")) {
    controlServer.send(400, "application/json", "{\"error\":\"missing dir param\"}");
    return;
  }

  String dir = controlServer.arg("dir");
  dir.toLowerCase();

  // Parse speed parameter (default 200 if not specified)
  int speed = 200;
  if (controlServer.hasArg("speed")) {
    speed = controlServer.arg("speed").toInt();
    speed = constrain(speed, 0, 255);
  }

  if (dir == "forward")     motorForward(speed);
  else if (dir == "back")   motorBack(speed);
  else if (dir == "left")   motorLeft(speed);
  else if (dir == "right")  motorRight(speed);
  else if (dir == "stop")   motorStop();
  else {
    controlServer.send(400, "application/json", "{\"error\":\"invalid direction\"}");
    return;
  }

  motorLastCmd = millis();
  controlServer.send(200, "application/json",
    "{\"status\":\"ok\",\"direction\":\"" + dir + "\",\"speed\":" + String(speed) + "}");
}

void handleStatus() {
  controlServer.sendHeader("Access-Control-Allow-Origin", "*");

  long rssi = WiFi.RSSI();
  unsigned long uptime = millis() / 1000;

  String json = "{";
  json += "\"rssi\":" + String(rssi) + ",";
  json += "\"uptime\":" + String(uptime) + ",";
  json += "\"motor\":\"" + currentMotorState + "\",";
  json += "\"speed\":" + String(currentSpeed) + ",";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"mqtt_connected\":" + String(mqttClient.connected() ? "true" : "false") + ",";
  json += "\"driver\":\"L298N\"";
  json += "}";

  controlServer.send(200, "application/json", json);
}

void handleRoot() {
  controlServer.sendHeader("Access-Control-Allow-Origin", "*");
  controlServer.send(200, "text/html",
    "<h1>DogBot ESP32-CAM</h1>"
    "<p><strong>L298N Motor Driver with PWM Speed Control</strong></p>"
    "<p>Stream: <a href='http://" + WiFi.localIP().toString() + ":81/stream'>MJPEG</a></p>"
    "<p>Motor: /motor?dir=forward|back|left|right|stop&speed=0-255</p>"
    "<p>Status: /status</p>"
    "<p>MQTT: " + String(mqttClient.connected() ? "Connected" : "Disconnected") + "</p>"
    "<p style='color:#888; font-size:12px;'>Example: /motor?dir=forward&speed=150</p>");
}

// ==================== MJPEG Stream ====================
void streamTask(void* pvParameters) {
  WiFiClient client;

  while (true) {
    client = streamServer.available();
    if (client) {
      String request = client.readStringUntil('\r');
      client.flush();

      client.println("HTTP/1.1 200 OK");
      client.println("Content-Type: multipart/x-mixed-replace; boundary=frame");
      client.println("Access-Control-Allow-Origin: *");
      client.println("Cache-Control: no-cache");
      client.println("Connection: keep-alive");
      client.println();

      while (client.connected()) {
        camera_fb_t* fb = esp_camera_fb_get();
        if (!fb) {
          Serial.println("Camera capture failed");
          delay(100);
          continue;
        }

        client.printf("--frame\r\n");
        client.printf("Content-Type: image/jpeg\r\n");
        client.printf("Content-Length: %u\r\n\r\n", fb->len);
        client.write(fb->buf, fb->len);
        client.printf("\r\n");

        esp_camera_fb_return(fb);

        if (!client.connected()) break;
        delay(33); // ~30 FPS target
      }

      client.stop();
    }
    delay(10);
  }
}

// ==================== Camera Init ====================
bool cameraInit() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_2;
  config.ledc_timer = LEDC_TIMER_1;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // Check if PSRAM is available
  if (psramFound()) {
    Serial.println("PSRAM found - using high quality settings");
    config.frame_size = FRAMESIZE_VGA;   // 640x480
    config.jpeg_quality = 12;
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    Serial.println("WARNING: PSRAM not found - using low quality settings");
    config.frame_size = FRAMESIZE_SVGA;  // 800x600 (lower than VGA for memory)
    config.jpeg_quality = 16;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  config.grab_mode = CAMERA_GRAB_LATEST;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }

  // Adjust sensor settings
  sensor_t* s = esp_camera_sensor_get();
  s->set_brightness(s, 1);
  s->set_saturation(s, -1);
  s->set_whitebal(s, 1);
  s->set_awb_gain(s, 1);

  return true;
}

// ==================== Setup ====================
void setup() {
  Serial.begin(115200);
  delay(1000);  // Give serial time to stabilize
  Serial.println("\n=== DogBot Recon System ===");

  // Check reset reason
  esp_reset_reason_t reason = esp_reset_reason();
  Serial.print("Reset reason: ");
  Serial.println(reason);
  if (reason == ESP_RST_BROWNOUT) {
    Serial.println("⚠️ WARNING: Brownout detected! Power supply insufficient!");
  }

  // Init camera
  if (!cameraInit()) {
    Serial.println("FATAL: Camera init failed!");
    while (true) delay(1000);
  }
  Serial.println("Camera: OK");

  // Init motors
  Serial.println("\n--- Starting Motor Initialization ---");
  motorSetup();
  Serial.println("Motors: OK");
  Serial.flush();  // Force output before continuing

  // Small delay to stabilize power after motor init
  Serial.println("Waiting for power stabilization...");
  Serial.flush();
  delay(500);

  Serial.println("Starting WiFi setup...");
  Serial.flush();

  // Connect WiFi
  Serial.println("Calling WiFi.mode(WIFI_STA)...");
  Serial.flush();
  WiFi.mode(WIFI_STA);

  Serial.println("WiFi mode set to STA");
  Serial.flush();
  delay(100);

  Serial.println("Calling WiFi.begin()...");
  Serial.flush();
  WiFi.begin(ssid, password);

  Serial.print("WiFi connecting");
  Serial.flush();
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\nFATAL: WiFi connection failed!");
    while (true) delay(1000);
  }

  Serial.println("\nWiFi: OK");
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());

  // Setup HTTP routes
  controlServer.on("/", handleRoot);
  controlServer.on("/motor", handleMotor);
  controlServer.on("/status", handleStatus);
  controlServer.begin();
  Serial.println("Control server: port 80");

  // Start MJPEG stream server
  streamServer.begin();
  Serial.println("Stream server: port 81");

  // Run stream on core 0
  xTaskCreatePinnedToCore(streamTask, "stream", 8192, NULL, 1, NULL, 0);

  // Setup MQTT (only if configured)
  if (String(mqtt_server) != "YOUR_MQTT_BROKER" && strlen(mqtt_server) > 0) {
    mqttEnabled = true;
    mqttWifiClient.setInsecure();  // Skip cert validation (use setCACert for production)
    mqttClient.setServer(mqtt_server, mqtt_port);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(512);
    Serial.println("MQTT: configured");
  } else {
    Serial.println("MQTT: not configured, skipping");
  }

  Serial.println("=== DogBot Ready ===");
  Serial.println("Motor Driver: L298N with PWM speed control");
  Serial.printf("Stream: http://%s:81/stream\n", WiFi.localIP().toString().c_str());
  Serial.printf("Control: http://%s/motor?dir=forward&speed=200\n", WiFi.localIP().toString().c_str());
}

// ==================== Loop ====================
void loop() {
  controlServer.handleClient();

  // MQTT handling
  if (mqttEnabled) {
    if (!mqttClient.connected()) {
      unsigned long now = millis();
      if (now - mqttLastReconnectAttempt > 5000) {
        mqttLastReconnectAttempt = now;
        mqttReconnect();
      }
    } else {
      mqttClient.loop();

      // Publish status every 5 seconds
      unsigned long now = millis();
      if (now - mqttLastStatusPublish > 5000) {
        mqttLastStatusPublish = now;
        mqttPublishStatus();
      }

      // Publish heartbeat every 3 seconds
      if (now - mqttLastHeartbeat > 3000) {
        mqttLastHeartbeat = now;
        mqttPublishHeartbeat();
      }
    }
  }

  // Auto-stop safety: stop motors if no command for 800ms
  // Reduced from 2000ms to work with backend command deduplication
  if (currentMotorState != "stop" && (millis() - motorLastCmd > 800)) {
    motorStop();
    Serial.println("Auto-stop: no command timeout");
  }

  delay(1);
}
