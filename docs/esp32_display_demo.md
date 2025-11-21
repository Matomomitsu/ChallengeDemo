# ESP32 Display Demo for GoodWe Inverter Parameters

This guide walks you through wiring an ESP32 to an I2C LCD (or OLED), fetching your inverter snapshot from the FastAPI endpoint, and rendering it for your demo. It also covers exposing your endpoint with ngrok if you need to consume it over the internet.

## What You’ll Build
- Backend (FastAPI) serves a lightweight snapshot at `GET /api/inverter`.
- The GoodWe→Tuya bridge writes the snapshot locally every polling cycle.
- An ESP32 connects to Wi‑Fi, polls the endpoint every few seconds, and displays:
  - PV power (W/kW)
  - Load (W/kW)
  - Battery SOC (%)
  - Status (carregando / descarregando / etc.)

## Prerequisites
- FastAPI running: `python main.py` (default port 8001)
- Tuya bridge running: `python -m integrations.tuya.bridge_soc`
  - Confirms snapshot at `data/last_inverter_telemetry.json`
- Arduino IDE or PlatformIO with ESP32 support
- Display module:
  - Option A (most common): 16x2 or 20x4 I2C LCD with PCF8574 backpack (address usually `0x27` or `0x3F`)
  - Option B: SSD1306 128x64 OLED (I2C)

## Endpoint Shape (what the ESP32 reads)
Example from `http://<host>:8001/api/inverter`:

```json
{
  "powerstation_id": "7f9af1fc-3a9a-4779-a4c0-ca6ec87bd93a",
  "timestamp": 1762281495,
  "battery_soc": 96,
  "status": "descarregando",
  "load_w": 153,
  "pv_power_w": 2317,
  "eday_kwh": 18,
  "emonth_kwh": 54,
  "day_income": 18
}
```

## Hardware Wiring (I2C)
- ESP32 `3V3` → Display `VCC`
- ESP32 `GND` → Display `GND`
- ESP32 `GPIO21` → Display `SDA`
- ESP32 `GPIO22` → Display `SCL`

Notes:
- ESP32 I/O is 3.3V. Most I2C LCD backpacks accept 3.3V logic; if yours requires 5V logic, use a level shifter.
- If unsure of your I2C address, run the scanner below.

### I2C Address Scanner (optional)
Upload this once to find your device address (e.g., `0x27`):

```cpp
#include <Wire.h>
void setup(){
  Serial.begin(115200);
  Wire.begin(21,22);
  Serial.println("Scanning...");
  for (byte addr=1; addr<127; addr++){
    Wire.beginTransmission(addr);
    if (Wire.endTransmission()==0){
      Serial.print("Found 0x"); Serial.println(addr, HEX);
      delay(10);
    }
  }
}
void loop(){}
```

## Arduino IDE Setup
1. Install ESP32 board support (Boards Manager → “esp32 by Espressif Systems”).
2. Libraries (Library Manager):
   - LiquidCrystal_I2C (for LCD) or Adafruit SSD1306 + Adafruit GFX (for OLED)
   - ArduinoJson

## Option A: 16x2 I2C LCD (Full Sketch)
Paste into a new sketch, set Wi‑Fi and API URL.

```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <LiquidCrystal_I2C.h>

// ---------- CONFIG ----------
const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_PASS";
// Prefer local LAN HTTP for simplicity
const char* API_URL  = "http://<host>:8001/api/inverter"; // e.g., http://192.168.1.50:8001/api/inverter

// I2C LCD at 0x27 (change to 0x3F if needed); 16x2 display
LiquidCrystal_I2C lcd(0x27, 16, 2);

unsigned long lastFetch = 0;
const unsigned long intervalMs = 7000; // poll every 7s

int pv = -1, loadv = -1, soc = -1;
String statusTxt = "?";
unsigned long lastOk = 0;
unsigned long lastRender = 0;

// Optional: after 20s without data, show a demo loop
bool demoMode = false;
int demoStep = 0;

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(200);
  }
}

void fetchSnapshot() {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.begin(API_URL);
  int code = http.GET();
  if (code == 200) {
    StaticJsonDocument<1024> doc;
    DeserializationError err = deserializeJson(doc, http.getStream());
    if (!err) {
      pv = doc["pv_power_w"] | -1;
      loadv = doc["load_w"] | -1;
      soc = doc["battery_soc"] | -1;
      statusTxt = String((const char*)doc["status"] | "?");
      lastOk = millis();
      demoMode = false;
    }
  }
  http.end();
}

void render() {
  // Avoid excessive I2C updates
  if (millis() - lastRender < 250) return;
  lastRender = millis();

  lcd.clear();
  // Line 1: PV and SOC
  lcd.setCursor(0, 0);
  lcd.print("PV ");
  if (pv >= 1000) { lcd.print(pv/1000.0, 1); lcd.print("kW"); }
  else if (pv >= 0) { lcd.print(pv); lcd.print("W"); }
  else lcd.print("--");
  lcd.print(" S");
  if (soc >= 0) { lcd.print(soc); lcd.print("%"); } else lcd.print("--");

  // Line 2: Load and Status abbrev
  lcd.setCursor(0, 1);
  lcd.print("L ");
  if (loadv >= 1000) { lcd.print(loadv/1000.0, 1); lcd.print("kW"); }
  else if (loadv >= 0) { lcd.print(loadv); lcd.print("W"); }
  else lcd.print("--");
  lcd.print(" ");
  String s = statusTxt;
  if (s == "carregando") s = "chg";
  else if (s == "descarregando") s = "dchg";
  lcd.print(s);

  int age = (millis() - lastOk) / 1000;
  if (age < 100) { lcd.setCursor(13,1); lcd.print(age); lcd.print("s"); }
}

void setup() {
  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.print("Connecting...");
  connectWiFi();
  lcd.clear();
  lcd.print(WiFi.status() == WL_CONNECTED ? "WiFi OK" : "WiFi FAIL");
  delay(800);
}

void loop() {
  unsigned long now = millis();

  // Fetch
  if (now - lastFetch > intervalMs) {
    lastFetch = now;
    fetchSnapshot();
  }

  // Enter demo mode if no data > 20s
  if (!demoMode && (now - lastOk > 20000)) {
    demoMode = true; demoStep = 0;
  }
  if (demoMode) {
    // Simple animated demo data
    pv = 1200 + (demoStep % 5) * 300;
    loadv = 200 + (demoStep % 3) * 100;
    soc = 50 + (demoStep % 6) * 5;
    statusTxt = (demoStep % 2 == 0) ? "carregando" : "descarregando";
    demoStep++;
  }

  render();
  delay(50);
}
```

## Option B: SSD1306 128x64 OLED (Short Variant)
Use Adafruit SSD1306 + GFX libraries. Update I2C pins if needed.

```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_PASS";
const char* API_URL  = "http://<host>:8001/api/inverter";

unsigned long lastFetch = 0, lastOk = 0; const unsigned long intervalMs = 7000;
int pv=-1, loadv=-1, soc=-1; String statusTxt="?";

void fetchSnapshot(){
  if (WiFi.status()!=WL_CONNECTED) return; HTTPClient http; http.begin(API_URL);
  if (http.GET()==200){ StaticJsonDocument<1024> d; if (!deserializeJson(d, http.getStream())){
      pv=d["pv_power_w"]| -1; loadv=d["load_w"]| -1; soc=d["battery_soc"]| -1; statusTxt=String((const char*)d["status"]|"?"); lastOk=millis(); }}
  http.end();
}

void draw(){
  display.clearDisplay(); display.setTextSize(1); display.setTextColor(SSD1306_WHITE);
  display.setCursor(0,0); display.print("PV "); if (pv>=1000){ display.print(pv/1000.0,1); display.print("kW"); } else if (pv>=0){ display.print(pv); display.print("W"); } else display.print("--");
  display.setCursor(0,16); display.print("Load "); if (loadv>=1000){ display.print(loadv/1000.0,1); display.print("kW"); } else if (loadv>=0){ display.print(loadv); display.print("W"); } else display.print("--");
  display.setCursor(0,32); display.print("SOC "); if (soc>=0){ display.print(soc); display.print("%"); } else display.print("--");
  String s=statusTxt; if (s=="carregando") s="chg"; else if (s=="descarregando") s="dchg";
  display.setCursor(0,48); display.print("Status "); display.print(s);
  int age=(millis()-lastOk)/1000; display.setCursor(100,48); display.print(age); display.print("s");
  display.display();
}

void setup(){ WiFi.begin(WIFI_SSID,WIFI_PASS); unsigned long t=millis(); while(WiFi.status()!=WL_CONNECTED && millis()-t<15000) delay(200);
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C); display.clearDisplay(); display.display(); }
void loop(){ if (millis()-lastFetch>intervalMs){ lastFetch=millis(); fetchSnapshot(); } draw(); delay(200); }
```

## Exposing the Endpoint with ngrok (Optional)
Use this only if the ESP32 cannot be on the same LAN as your backend.

1. Start your API locally: `python main.py` (port 8001)
2. Run ngrok: `ngrok http 8001`
3. You’ll get a public URL like `https://abc123.ngrok.io`. Your ESP32 endpoint becomes:
   - `https://abc123.ngrok.io/api/inverter`

### Using HTTPS from ESP32
ESP32 can speak HTTPS via `WiFiClientSecure`. Easiest for demos is to disable certificate validation (not for production):

```cpp
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

WiFiClientSecure client; // global

void fetchSnapshotHttps(const char* url){
  if (WiFi.status()!=WL_CONNECTED) return;
  client.setInsecure(); // WARNING: no cert validation
  HTTPClient http;
  if (!http.begin(client, url)) return;
  int code=http.GET();
  // ... parse JSON like before
  http.end();
}
```

If you prefer validation, extract the ngrok root CA and use `client.setCACert(...)` with the PEM string.

## Troubleshooting
- `503` from `/api/inverter`: the bridge hasn’t written the first snapshot yet. Let it run one cycle.
- LCD shows gibberish or stays blank: confirm I2C address (`0x27` vs `0x3F`) and wiring SDA=21/SCL=22.
- No Wi‑Fi: double‑check SSID/password; try 2.4 GHz; some venues block client isolation.
- Very slow updates: adjust `TUYA_SOC_POLL_INTERVAL` on the bridge (min 10s) and the ESP32 `intervalMs`.
- HTTPS issues: prefer LAN HTTP. For ngrok, use `WiFiClientSecure` with `setInsecure()` for quick demos.

## Notes and Safety
- The ESP32 display is read‑only; it does not control devices.
- Prefer keeping the endpoint on your LAN. If exposing via ngrok, rotate URLs/tokens after the demo.
- You can override the snapshot location with `TELEMETRY_SNAPSHOT_PATH` (defaults to `data/last_inverter_telemetry.json`).

---

That’s it. With the API and bridge running, your ESP32 should refresh every few seconds and mirror the same values your Tuya/Alexa logic uses.

