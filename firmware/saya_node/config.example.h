// Copy nothing secret into the repo. Fill these in locally.
#pragma once

#define WIFI_SSID        "your-2.4GHz-network"
#define WIFI_PASSWORD    "your-password"

// Where the ingest service is reachable. Use the LAN IP of the machine running
// server/ingest.py, not localhost: the device is not on that machine.
#define INGEST_HOST      "192.168.1.100"   // your Mac's LAN IP: ipconfig getifaddr en0
#define INGEST_PORT      8080

#define DEVICE_NAME      "roof-01"

// Seconds between readings. The roof run does not need to be fast, and a longer
// interval means fewer frames to review and a smaller archive.
#define REPORT_INTERVAL_S  300

// DHT22 data line. GPIO 13 is chosen because the camera takes most pins, GPIO 12 is a
// boot strapping pin that a sensor pull-up can stop the board booting on, GPIO 16 is
// tied to PSRAM, and GPIO 1 and 3 are the serial console. See 05-HARDWARE.md.
#define DHT_PIN          13
