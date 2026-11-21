/*
 * esp32_alert.ino - Board cảnh báo nguy hiểm
 *
 * Board ESP32 mở một HTTP server nhỏ trong mạng WiFi nội bộ. Chương trình Python
 * trên máy tính sẽ gọi:
 *   GET /status                        -> trạng thái board (JSON)
 *   GET /alert?state=on&duration=60    -> bật còi + đèn nháy trong 60 giây
 *   GET /alert?state=off               -> tắt cảnh báo
 *
 * Phần cứng:
 *   - Còi buzzer (hoặc module còi) -> GPIO 25
 *   - LED                          -> GPIO 2 (LED có sẵn trên board)
 *   - Nút nhấn (dùng nút BOOT)     -> GPIO 0, nhấn để tắt còi tại chỗ
 *
 * Bo mạch: ESP32 DevKit. Thư viện: ESP32 core >= 3.0 (Boards Manager).
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <esp_arduino_version.h>

// ===================== CẤU HÌNH =====================
static const char *WIFI_SSID = "TEN_WIFI_CUA_BAN";
static const char *WIFI_PASS = "MAT_KHAU_WIFI";

// Có thể dùng tên này thay cho IP trong config.json: "alert-esp32.local"
static const char *MDNS_HOST = "alert-esp32";

static const int PIN_BUZZER = 25;
static const int PIN_LED = 2;
static const int PIN_BUTTON = 0;  // nút BOOT, nhấn = tắt còi

static const uint16_t TONE_HZ = 2200;         // tần số còi
static const uint32_t DEFAULT_ALARM_MS = 60000;  // thời lượng cảnh báo mặc định
static const uint32_t DEBOUNCE_MS = 200;

// ===================== BIẾN =====================
WebServer server(80);

bool alarmActive = false;
uint32_t alarmEndMs = 0;
uint32_t lastLedToggleMs = 0;
bool ledState = false;
uint32_t lastWifiCheckMs = 0;
uint32_t lastButtonMs = 0;
bool lastButtonReading = HIGH;

#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
#define BUZZER_SETUP() ledcAttach(PIN_BUZZER, TONE_HZ, 10)
#define BUZZER_TONE(freq) ledcWriteTone(PIN_BUZZER, (freq))
#else
#define BUZZER_CHANNEL 0
#define BUZZER_SETUP()                       \
  do {                                       \
    ledcSetup(BUZZER_CHANNEL, TONE_HZ, 10);  \
    ledcAttachPin(PIN_BUZZER, BUZZER_CHANNEL); \
  } while (0)
#define BUZZER_TONE(freq) ledcWriteTone(BUZZER_CHANNEL, (freq))
#endif

// ===================== CÒI / ĐÈN =====================
void setAlarm(bool on, uint32_t durationMs) {
  alarmActive = on;
  alarmEndMs = on ? millis() + durationMs : 0;

  if (on) {
    BUZZER_TONE(TONE_HZ);
    digitalWrite(PIN_LED, HIGH);
    ledState = true;
  } else {
    BUZZER_TONE(0);
    digitalWrite(PIN_LED, LOW);
    ledState = false;
  }
  lastLedToggleMs = millis();

  Serial.printf("[ALARM] %s (thoi luong %lu ms)\n", on ? "BAT" : "TAT",
                (unsigned long)durationMs);
}

void updateLed() {
  uint32_t now = millis();
  uint32_t interval = alarmActive ? 120 : 2000;  // cảnh báo: nháy nhanh
  if (now - lastLedToggleMs >= interval) {
    lastLedToggleMs = now;
    ledState = !ledState;
    digitalWrite(PIN_LED, ledState ? HIGH : LOW);
  }
}

void updateButton() {
  bool reading = digitalRead(PIN_BUTTON);
  if (reading != lastButtonReading && (millis() - lastButtonMs) > DEBOUNCE_MS) {
    lastButtonMs = millis();
    lastButtonReading = reading;
    if (reading == LOW) {  // vừa nhấn
      if (alarmActive) {
        Serial.println("[BUTTON] Nhan nut -> tat canh bao");
        setAlarm(false, 0);
      } else {
        Serial.println("[BUTTON] Nhan nut -> bat canh bao");
        setAlarm(true, DEFAULT_ALARM_MS);
      }
    }
  }
}

// ===================== HTTP =====================
void sendStatus() {
  String json = "{\"alarm\":";
  json += alarmActive ? "true" : "false";
  json += ",\"remaining_ms\":";
  if (alarmActive) {
    json += String((long)(alarmEndMs - millis()));
  } else {
    json += "0";
  }
  json += ",\"rssi\":" + String(WiFi.RSSI());
  json += ",\"uptime_s\":" + String(millis() / 1000);
  json += ",\"ip\":\"" + WiFi.localIP().toString() + "\"}";
  server.send(200, "application/json", json);
}

void handleStatus() {
  sendStatus();
}

void handleAlert() {
  String state = server.arg("state");
  if (state == "on") {
    uint32_t durationMs = DEFAULT_ALARM_MS;
    if (server.hasArg("duration")) {
      long seconds = server.arg("duration").toInt();
      if (seconds > 0) durationMs = (uint32_t)seconds * 1000UL;
    }
    setAlarm(true, durationMs);
  } else if (state == "off") {
    setAlarm(false, 0);
  } else {
    server.send(400, "application/json", "{\"error\":\"state phai la on hoac off\"}");
    return;
  }
  sendStatus();
}

void handleNotFound() {
  server.send(404, "application/json", "{\"error\":\"not found\"}");
}

// ===================== WIFI =====================
void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.printf("\nDang ket noi WiFi \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\nDa ket noi. IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\nKet noi WiFi that bai, se thu lai...");
  }
}

// ===================== SETUP / LOOP =====================
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n=== ESP32 CANH BAO NGUY HIEM ===");

  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  digitalWrite(PIN_LED, LOW);
  BUZZER_SETUP();
  BUZZER_TONE(0);

  connectWifi();

  if (MDNS.begin(MDNS_HOST)) {
    Serial.printf("mDNS: http://%s.local\n", MDNS_HOST);
  }

  server.on("/status", HTTP_GET, handleStatus);
  server.on("/alert", HTTP_GET, handleAlert);
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.println("HTTP server dang chay o cong 80");
}

void loop() {
  server.handleClient();

  // Tự tắt còi khi hết thời lượng
  if (alarmActive && (int32_t)(millis() - alarmEndMs) >= 0) {
    Serial.println("[ALARM] Het thoi luong -> tat");
    setAlarm(false, 0);
  }

  updateLed();
  updateButton();

  // Kiểm tra và kết nối lại WiFi mỗi 10 giây
  if (millis() - lastWifiCheckMs >= 10000) {
    lastWifiCheckMs = millis();
    connectWifi();
  }
}
