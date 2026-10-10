// Saya rooftop node: ESP32-CAM plus DHT22.
//
// Reads air temperature and humidity where people actually stand, photographs the
// scene, and posts both to the ingest service. Nothing is decided here. The board's
// only job is to report honestly and to keep reporting.
//
// Libraries needed in the Arduino IDE:
//   Adafruit DHT sensor library, and Adafruit Unified Sensor which it depends on.
// Board: "AI Thinker ESP32-CAM". Partition scheme: "Huge APP".
//
// Three behaviours exist because of how this will actually be deployed on a roof in
// Islamabad, and each is explained where it happens:
//   1. The camera sensor is detected, not assumed. The ordered board's listing said
//      OV3660 in the title and OV2640 in the description.
//   2. Load-shedding will cut power. Recovery is automatic and needs nobody on the roof.
//   3. A failed reading is reported as missing, never as a plausible number.

#include "config.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>
#include "esp_camera.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// AI-Thinker ESP32-CAM pin map.
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM   0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM     5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22

DHT dht(DHT_PIN, DHT22);

static bool cameraReady = false;
static uint32_t reportCount = 0;

// ---------------------------------------------------------------- camera

static bool startCamera() {
  camera_config_t c;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer   = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;   c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM;   c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;   c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;   c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;   c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;   c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    c.frame_size = FRAMESIZE_SVGA;   // 800x600 is plenty for judging shade and surface
    c.jpeg_quality = 12;
    c.fb_count = 2;
  } else {
    // Without PSRAM the framebuffer must be small or allocation fails outright.
    c.frame_size = FRAMESIZE_VGA;
    c.jpeg_quality = 15;
    c.fb_count = 1;
    Serial.println("WARNING: no PSRAM found. Falling back to VGA.");
  }

  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("camera init failed: 0x%x\n", err);
    return false;
  }

  // Detect rather than assume. The ordered board's listing named two different
  // sensors, and the tuning below differs between them.
  sensor_t *s = esp_camera_sensor_get();
  if (s == NULL) { Serial.println("camera sensor handle is null"); return false; }
  Serial.printf("camera sensor PID: 0x%04x", s->id.PID);
  if (s->id.PID == OV2640_PID)      Serial.println("  (OV2640)");
  else if (s->id.PID == OV3660_PID) {
    Serial.println("  (OV3660)");
    // The OV3660 ships flipped and washed out on these boards.
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, -2);
  } else {
    Serial.println("  (unrecognised, using defaults)");
  }
  return true;
}

// ---------------------------------------------------------------- wifi

static void connectWiFi(uint32_t budgetMs) {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("wifi: connecting to %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);          // sleep makes the first POST after idle flaky
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < budgetMs) {
    delay(400);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("wifi: %s  rssi %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    Serial.println("wifi: no connection this round, will retry next cycle");
  }
}

// ---------------------------------------------------------------- report

static void report() {
  // Read the sensor first. If the camera is broken the reading still matters, and a
  // reading without a frame is explicitly accepted by the ingest service.
  float t = dht.readTemperature();
  float h = dht.readHumidity();

  String query = String("/api/reading?device=") + DEVICE_NAME;
  // NaN means the sensor did not answer. Send nothing for that field rather than a
  // substitute value. A number invented here would end up in the dataset and in the
  // headline comparison, and nobody downstream could tell it was not measured.
  if (!isnan(t)) query += "&t=" + String(t, 1);
  if (!isnan(h)) query += "&h=" + String(h, 1);
  if (isnan(t) && isnan(h)) {
    Serial.println("dht: no reading, skipping this cycle");
    return;
  }
  Serial.printf("dht: %.1f C  %.1f %%\n", t, h);

  camera_fb_t *fb = NULL;
  if (cameraReady) {
    fb = esp_camera_fb_get();
    if (!fb) Serial.println("camera: capture failed, sending reading only");
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("wifi: down, reading lost this cycle");
    if (fb) esp_camera_fb_return(fb);
    return;
  }

  HTTPClient http;
  String url = String("http://") + INGEST_HOST + ":" + INGEST_PORT + query;
  http.begin(url);
  http.addHeader("Content-Type", "image/jpeg");
  http.setTimeout(20000);

  int code;
  if (fb) {
    // Post the framebuffer straight from memory. Base64 would cost a third more RAM
    // on a board that does not have it spare, which is why the wire format puts the
    // readings in the query string and the frame in the raw body.
    code = http.POST(fb->buf, fb->len);
    Serial.printf("post: %d bytes of JPEG -> HTTP %d\n", fb->len, code);
    esp_camera_fb_return(fb);
  } else {
    code = http.POST((uint8_t *)NULL, 0);
    Serial.printf("post: reading only -> HTTP %d\n", code);
  }
  http.end();
  reportCount++;
}

// ---------------------------------------------------------------- lifecycle

void setup() {
  // The onboard brownout detector trips on the current spike when the camera and the
  // WiFi radio start together, even on an adequate supply, and the board resets in a
  // loop that reads exactly like a software fault. Disabling it is standard practice
  // on ESP32-CAM. It does NOT excuse a weak supply: use a real 5V 2A source.
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);

  Serial.begin(115200);
  delay(300);
  Serial.println("\n\nSaya node starting");
  Serial.printf("reset reason: %d\n", (int)esp_reset_reason());

  dht.begin();
  cameraReady = startCamera();
  if (!cameraReady) Serial.println("continuing without a camera, readings only");
  connectWiFi(20000);
  Serial.println("ready\n");
}

void loop() {
  // Load-shedding will cut power to this board, and the WiFi router with it. Every
  // cycle re-checks the link and reconnects, so recovery needs nobody on the roof.
  connectWiFi(15000);
  report();
  Serial.printf("reports sent: %lu  uptime: %lu s\n\n",
                (unsigned long)reportCount, (unsigned long)(millis() / 1000));
  delay((uint32_t)REPORT_INTERVAL_S * 1000UL);
}
