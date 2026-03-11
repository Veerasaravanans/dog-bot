/*
 * ESP32-CAM Low Power Mode
 * Reduced resolution and quality for weak power supplies
 */

#include "esp_camera.h"
#include "esp_system.h"
#include <WiFi.h>

const char* ssid = "Airtel_thir_0818";
const char* password = "Air@51882";

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

  // REDUCED settings for low power
  config.xclk_freq_hz = 10000000;  // 10MHz instead of 20MHz
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_QVGA;  // 320x240 (very small)
  config.jpeg_quality = 20;  // Lower quality
  config.fb_count = 1;  // Single buffer only
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_LATEST;

  Serial.println("Using LOW POWER camera settings");
  Serial.println("Resolution: 320x240 (QVGA)");

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }

  // Reduce camera current consumption
  sensor_t* s = esp_camera_sensor_get();
  s->set_brightness(s, 0);  // Lower brightness
  s->set_contrast(s, 0);
  s->set_saturation(s, -2);  // Lower saturation
  s->set_ae_level(s, -2);  // Reduce auto-exposure
  s->set_aec_value(s, 300);  // Manual exposure
  s->set_agc_gain(s, 0);  // Reduce gain

  return true;
}

void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.println("\n=== LOW POWER MODE ===");

  esp_reset_reason_t reason = esp_reset_reason();
  if (reason == ESP_RST_BROWNOUT) {
    Serial.println("⚠️ Previous brownout detected");
  }

  if (!cameraInit()) {
    Serial.println("Camera failed!");
    while (true) delay(1000);
  }
  Serial.println("Camera: OK");

  // Small delay before WiFi
  delay(500);

  // Start WiFi with reduced power
  Serial.println("Starting WiFi (low power mode)...");
  WiFi.mode(WIFI_STA);

  // Reduce WiFi TX power
  esp_wifi_set_max_tx_power(44);  // Reduce from 78 to 44 (11dBm)

  WiFi.begin(ssid, password);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi: OK");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());
    Serial.println("\n✅ SUCCESS with low power mode!");
  } else {
    Serial.println("\nWiFi failed");
  }
}

void loop() {
  delay(5000);
  camera_fb_t* fb = esp_camera_fb_get();
  if (fb) {
    Serial.printf("Frame: %u bytes\n", fb->len);
    esp_camera_fb_return(fb);
  }
}
