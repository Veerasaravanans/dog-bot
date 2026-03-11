/*
 * ESP32-CAM Test - Camera + WiFi ONLY
 * No MQTT, No WebServer, No motor libraries
 */

#include "esp_camera.h"
#include "esp_system.h"
#include <WiFi.h>

// WiFi Config - UPDATE THESE
const char* ssid = "Airtel_thir_0818";
const char* password = "Air@51882";

// AI-Thinker ESP32-CAM Pins
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

  if (psramFound()) {
    Serial.println("PSRAM: Found ✅");
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 12;
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    Serial.println("PSRAM: Not found ⚠️");
    config.frame_size = FRAMESIZE_SVGA;
    config.jpeg_quality = 16;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  config.grab_mode = CAMERA_GRAB_LATEST;

  Serial.println("Initializing camera...");
  esp_err_t err = esp_camera_init(&config);

  if (err != ESP_OK) {
    Serial.printf("Camera init FAILED: 0x%x\n", err);
    return false;
  }

  Serial.println("Camera: OK ✅");

  // Adjust sensor settings
  sensor_t* s = esp_camera_sensor_get();
  s->set_brightness(s, 1);
  s->set_saturation(s, -1);

  return true;
}

void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.println("\n\n=== ESP32-CAM Test: Camera + WiFi ===");

  // Check reset reason
  esp_reset_reason_t reason = esp_reset_reason();
  Serial.print("Reset reason: ");
  Serial.println(reason);

  if (reason == ESP_RST_BROWNOUT) {
    Serial.println("⚠️ BROWNOUT! Power supply too weak!");
    Serial.println("Use 5V 1A+ USB charger, not computer USB");
  }

  // Init camera
  if (!cameraInit()) {
    Serial.println("FATAL: Camera failed!");
    while (true) delay(1000);
  }

  // Test capture
  Serial.println("\nTesting frame capture...");
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Frame capture FAILED ❌");
  } else {
    Serial.printf("Frame captured: %u bytes ✅\n", fb->len);
    esp_camera_fb_return(fb);
  }

  // Connect WiFi
  Serial.println("\nStarting WiFi...");
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  int attempts = 0;
  Serial.print("Connecting");
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi: OK ✅");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nWiFi: FAILED (check credentials) ❌");
  }

  Serial.println("\n=== TEST COMPLETE ===");
  Serial.println("Camera + WiFi working!");
  Serial.println("\nNext step: Add motor control and MQTT");
}

void loop() {
  // Capture a frame every 5 seconds to test stability
  static unsigned long lastCapture = 0;

  if (millis() - lastCapture > 5000) {
    lastCapture = millis();

    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) {
      Serial.printf("Frame: %u bytes (RSSI: %d dBm)\n", fb->len, WiFi.RSSI());
      esp_camera_fb_return(fb);
    } else {
      Serial.println("Frame capture failed!");
    }
  }

  delay(100);
}
