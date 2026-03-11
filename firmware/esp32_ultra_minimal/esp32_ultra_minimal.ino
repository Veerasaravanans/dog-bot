/*
 * Ultra minimal ESP32 test - NO LIBRARIES
 * Tests if board settings are correct
 */

#define LED_PIN 33  // Built-in LED on AI-Thinker ESP32-CAM

void setup() {
  Serial.begin(115200);
  delay(2000);  // Wait for serial to stabilize

  Serial.println("\n\n=== ULTRA MINIMAL TEST ===");
  Serial.println("If you see this, ESP32 is working!");
  Serial.println("Board settings are correct.");

  pinMode(LED_PIN, OUTPUT);
  Serial.println("LED pin configured.");
}

void loop() {
  Serial.println("Loop running... (LED blinking)");
  digitalWrite(LED_PIN, HIGH);
  delay(1000);
  digitalWrite(LED_PIN, LOW);
  delay(1000);
}
