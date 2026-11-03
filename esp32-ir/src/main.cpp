// =====================================================================================================
//  WangertTV IR – ESP32-S3-BOX-3 mit Sensor-Dock als Infrarot-Empfänger und -Sender für die TV-Box
//
//  * Hängt per USB-C an der TV-Box und meldet sich als "WangertTV IR" an:
//      - USB-Seriell (CDC): Text-Befehle von/zu WangertTV (wtv-remote)
//      - USB-Tastatur (HID Consumer Control): Ein/Aus-Taste, falls WangertTV nicht mitliest
//  * Empfängt Infrarot (Sensor-Dock, GPIO38): erkennt die eingelernte Ein/Aus-Taste der Fernbedienung
//    (z. B. der waipu.tv-Fernbedienung, die damit eigentlich den Fernseher schaltet).
//  * Sendet Infrarot (Sensor-Dock, GPIO39): eingelernte Befehle für Fernseher und AV-Receiver.
//  * Weckt die TV-Box aus dem Ruhezustand (USB Remote Wakeup), wenn die Ein/Aus-Taste kommt.
//  * Zeigt auf dem Display, was gerade passiert.
//
//  Befehle über USB-Seriell (eine Zeile je Befehl):
//    ping | status | learn <slot> | cancel | send <slot> | clear <slot>
//  Meldungen:  "EV ..." (siehe sendEvent-Aufrufe)
//  Slots: pwr (Ein/Aus-Taste empfangen), tvp (Fernseher Ein/Aus senden), avp (Receiver Ein/Aus),
//         avon (Receiver Ein), avoff (Receiver Aus)
// =====================================================================================================
#include <Arduino.h>
#include <Preferences.h>

#include "USB.h"
#include "USBHIDConsumerControl.h"
#include "esp32-hal-tinyusb.h"

#define RAW_BUFFER_LENGTH 300        // lange Codes (z. B. Klimaanlagen, manche Receiver)
#define MARK_EXCESS_MICROS 20
#include <IRremote.hpp>

#include "lgfx_box3.h"

static const char* FW_VERSION = "1.0.1";

// ------------------------------------------------------------------ Pins (Sensor-Dock)
static constexpr int IR_RX = 38;
static constexpr int IR_TX = 39;
static constexpr int IR_CTRL = 44;   // LOW = Infrarot-Teil im Dock eingeschaltet (laut Espressif-Beispiel)

// ------------------------------------------------------------------ Slots
enum Slot { S_PWR, S_TVP, S_AVP, S_AVON, S_AVOFF, S_COUNT };
static const char* SLOT_KEY[S_COUNT] = {"pwr", "tvp", "avp", "avon", "avoff"};
static const char* SLOT_LABEL[S_COUNT] = {"Ein/Aus-Taste", "Fernseher Ein/Aus", "Receiver Ein/Aus", "Receiver Ein",
                                          "Receiver Aus"};

static constexpr int RAWMAX = 200;
struct IrCode {
  uint8_t used = 0;
  uint8_t proto = 0;
  uint16_t addr = 0, cmd = 0, extra = 0, bits = 0;
  uint32_t raw32 = 0;
  uint16_t rawLen = 0;
  uint16_t raw[RAWMAX];   // Mikrosekunden (nur für unbekannte Protokolle)
};

static IrCode g_slots[S_COUNT];
static Preferences prefs;
USBHIDConsumerControl ConsumerControl;
USBCDC USBSerial;
static LGFX_Box3 lcd;

// ------------------------------------------------------------------ Zustand
static int g_learnSlot = -1;
static uint32_t g_learnUntil = 0;
static uint32_t g_ignoreUntil = 0;      // eigenes Senden nicht wieder empfangen
static uint32_t g_lastPower = 0;
static String g_lastCode = "-";
static String g_message = "Bereit";
static uint32_t g_lastActivity = 0;
static bool g_dirty = true;
static bool g_backlight = true;
static String g_line;

// Aufwecken der TV-Box (USB Remote Wakeup) mit Wiederholungen und Diagnose
static volatile bool g_rwAllowed = false;   // hat der PC beim Einschlafen das Wecken erlaubt?
static volatile uint32_t g_suspendCount = 0;
static bool g_waking = false;
static uint32_t g_wakeStart = 0, g_wakeLast = 0;
static int g_wakeTries = 0, g_wakeOk = 0;
static uint32_t g_waitAck = 0;              // Ein/Aus an WangertTV gemeldet, warte auf Bestätigung
static String g_diag;                       // wird nach dem Aufwachen an WangertTV gemeldet

// ------------------------------------------------------------------ Hilfen
static String ascii(const String& s) {
  // Das Display-Font hat nur ASCII -> Umlaute umschreiben
  String o;
  for (size_t i = 0; i < s.length(); ++i) {
    uint8_t c = s[i];
    if (c == 0xC3 && i + 1 < s.length()) {
      uint8_t d = s[++i];
      switch (d) {
        case 0xA4: o += "ae"; break;
        case 0xB6: o += "oe"; break;
        case 0xBC: o += "ue"; break;
        case 0x84: o += "Ae"; break;
        case 0x96: o += "Oe"; break;
        case 0x9C: o += "Ue"; break;
        case 0x9F: o += "ss"; break;
        default: o += '?';
      }
    } else if (c < 0x80) {
      o += (char)c;
    }
  }
  return o;
}

static void activity(const String& msg = "") {
  if (msg.length()) g_message = msg;
  g_lastActivity = millis();
  g_dirty = true;
}

static String describe(const IrCode& c) {
  if (!c.used) return "-";
  char buf[64];
  if (c.proto == UNKNOWN) {
    snprintf(buf, sizeof(buf), "RAW:%u", c.rawLen);
  } else {
    snprintf(buf, sizeof(buf), "%s:%X:%X:%u", getProtocolString((decode_type_t)c.proto), c.addr, c.cmd, c.bits);
  }
  return String(buf);
}

static void sendEvent(const String& line) {
  if (USBSerial) {
    USBSerial.print("EV ");
    USBSerial.println(line);
  }
}

static int slotByName(const String& name) {
  for (int i = 0; i < S_COUNT; ++i)
    if (name == SLOT_KEY[i]) return i;
  return -1;
}

// ------------------------------------------------------------------ Speicher
static void loadSlots() {
  prefs.begin("wtvir", true);
  for (int i = 0; i < S_COUNT; ++i) {
    size_t n = prefs.getBytesLength(SLOT_KEY[i]);
    if (n == sizeof(IrCode)) prefs.getBytes(SLOT_KEY[i], &g_slots[i], sizeof(IrCode));
    else g_slots[i].used = 0;
  }
  prefs.end();
}

static void saveSlot(int i) {
  prefs.begin("wtvir", false);
  if (g_slots[i].used) prefs.putBytes(SLOT_KEY[i], &g_slots[i], sizeof(IrCode));
  else prefs.remove(SLOT_KEY[i]);
  prefs.end();
}

// ------------------------------------------------------------------ Infrarot
static void captureCode(IrCode& c) {
  const IRData& d = IrReceiver.decodedIRData;
  c = IrCode();
  c.used = 1;
  c.proto = (uint8_t)d.protocol;
  c.addr = d.address;
  c.cmd = d.command;
  c.extra = d.extra;
  c.bits = d.numberOfBits;
  c.raw32 = (uint32_t)d.decodedRawData;
  // Rohdaten immer mitnehmen: für unbekannte Protokolle zum Senden, sonst zum Vergleichen
  int n = (int)IrReceiver.irparams.rawlen - 1;
  if (n > RAWMAX) n = RAWMAX;
  if (n < 0) n = 0;
  for (int i = 0; i < n; ++i) {
    uint32_t us = (uint32_t)IrReceiver.irparams.rawbuf[i + 1] * MICROS_PER_TICK;
    us = (i % 2 == 0) ? (us > MARK_EXCESS_MICROS ? us - MARK_EXCESS_MICROS : us) : us + MARK_EXCESS_MICROS;
    c.raw[i] = us > 65000 ? 65000 : (uint16_t)us;
  }
  c.rawLen = (uint16_t)n;
}

static bool sameRaw(const IrCode& a, const IrCode& b) {
  if (a.rawLen < 12 || b.rawLen < 12) return false;
  int diff = (int)a.rawLen - (int)b.rawLen;
  if (diff < -2 || diff > 2) return false;
  int n = min(a.rawLen, b.rawLen);
  int bad = 0;
  for (int i = 0; i < n; ++i) {
    int x = a.raw[i], y = b.raw[i];
    int tol = max(220, max(x, y) / 4);
    if (abs(x - y) > tol) ++bad;
  }
  return bad <= n / 12;
}

static bool matches(const IrCode& learned, const IrCode& got) {
  if (!learned.used || !got.used) return false;
  if (learned.proto != got.proto) return false;
  if (learned.proto == UNKNOWN) return sameRaw(learned, got);
  if (learned.proto == PULSE_DISTANCE || learned.proto == PULSE_WIDTH)
    return learned.raw32 == got.raw32 && learned.bits == got.bits;
  // RC5/RC6: Wechsel-Bit steckt nicht in Adresse/Befehl -> wird automatisch ignoriert
  return learned.addr == got.addr && learned.cmd == got.cmd;
}

static bool sendSlot(int i) {
  const IrCode& c = g_slots[i];
  if (!c.used) return false;
  IrReceiver.stop();
  if (c.proto == UNKNOWN || c.proto == PULSE_DISTANCE || c.proto == PULSE_WIDTH) {
    IrSender.sendRaw(c.raw, c.rawLen, 38);
    delay(40);
    IrSender.sendRaw(c.raw, c.rawLen, 38);
  } else {
    IRData d;
    memset(&d, 0, sizeof(d));
    d.protocol = (decode_type_t)c.proto;
    d.address = c.addr;
    d.command = c.cmd;
    d.extra = c.extra;
    d.numberOfBits = c.bits;
    d.decodedRawData = c.raw32;
    // RC5/RC6: einmal senden (Wechsel-Bit setzt die Bibliothek selbst), sonst mit einer Wiederholung
    bool toggleProto = c.proto == RC5 || c.proto == RC6;
    IrSender.write(&d, toggleProto ? 0 : 1);
  }
  IrReceiver.start();
  g_ignoreUntil = millis() + 500;
  return true;
}

static void hidPower() {
  // als normale Ein/Aus-Taste (Linux: KEY_POWER) – auch ein Weckweg, falls der USB-Bus nicht schläft
  ConsumerControl.press(CONSUMER_CONTROL_POWER);
  delay(40);
  ConsumerControl.release();
}

static void startWake(const char* why) {
  if (g_waking) return;
  g_waking = true;
  g_wakeStart = millis();
  g_wakeLast = 0;
  g_wakeTries = 0;
  g_wakeOk = 0;
  g_diag = String("wake why=") + why + " allowed=" + (g_rwAllowed ? 1 : 0) + " suspends=" + g_suspendCount;
  activity("TV-Box wird geweckt");
}

static void wakeLoop() {
  if (!g_waking) return;
  uint32_t now = millis();
  if (!tud_suspended()) {
    g_waking = false;
    g_diag += String(" result=awake ms=") + (now - g_wakeStart) + " tries=" + g_wakeTries + " rw_ok=" + g_wakeOk;
    activity("TV-Box ist wach");
    return;
  }
  if (now - g_wakeStart > 6000) {
    g_waking = false;
    g_diag += String(" result=failed tries=") + g_wakeTries + " rw_ok=" + g_wakeOk;
    activity(g_wakeOk ? "PC reagiert nicht aufs Wecken" : "PC hat Wecken nicht erlaubt");
    return;
  }
  if (now - g_wakeLast > 400) {
    g_wakeLast = now;
    ++g_wakeTries;
    if (tud_remote_wakeup()) ++g_wakeOk;
  }
}

static void powerKey() {
  uint32_t now = millis();
  if (now - g_lastPower < 1200) return;   // eine Taste kommt oft mehrfach
  g_lastPower = now;
  if (tud_suspended()) {
    // TV-Box schläft -> per USB aufwecken (Remote Wakeup, mehrere Versuche)
    startWake("suspended");
    return;
  }
  if (USBSerial) {
    sendEvent("key power");
    g_waitAck = now;          // WangertTV bestätigt mit "ack" – kommt nichts, schläft der PC vermutlich
    activity("Ein/Aus an WangertTV gemeldet");
  } else {
    hidPower();
    activity("Ein/Aus-Taste gesendet");
  }
}

static void handleIr() {
  if (!IrReceiver.decode()) return;
  const IRData& d = IrReceiver.decodedIRData;
  bool repeat = d.flags & (IRDATA_FLAGS_IS_REPEAT | IRDATA_FLAGS_IS_AUTO_REPEAT);
  bool overflow = d.flags & IRDATA_FLAGS_WAS_OVERFLOW;
  IrCode got;
  captureCode(got);
  IrReceiver.resume();
  if (millis() < g_ignoreUntil || overflow) return;
  if (got.proto == UNKNOWN && got.rawLen < 12) return;   // Störsignal
  if (got.proto == NEC && repeat && got.addr == 0 && got.cmd == 0) return;

  g_lastCode = describe(got);
  sendEvent(String("ir ") + g_lastCode + (repeat ? " repeat" : ""));

  if (g_learnSlot >= 0) {
    if (repeat) return;
    g_slots[g_learnSlot] = got;
    saveSlot(g_learnSlot);
    sendEvent(String("learned ") + SLOT_KEY[g_learnSlot] + " " + g_lastCode);
    activity(String("Gespeichert: ") + SLOT_LABEL[g_learnSlot]);
    g_learnSlot = -1;
    g_lastPower = millis();      // die gerade eingelernte Taste nicht gleich als Ein/Aus werten
    return;
  }
  if (!repeat && matches(g_slots[S_PWR], got)) {
    powerKey();
    return;
  }
  activity("");
}

// ------------------------------------------------------------------ Befehle von WangertTV
static void statusLine() {
  String s = String("status fw=") + FW_VERSION + " slots=";
  for (int i = 0; i < S_COUNT; ++i) {
    if (i) s += ",";
    s += SLOT_KEY[i];
    s += "=";
    s += describe(g_slots[i]);
  }
  sendEvent(s);
}

static void handleCommand(String line) {
  line.trim();
  if (!line.length()) return;
  int sp = line.indexOf(' ');
  String cmd = sp < 0 ? line : line.substring(0, sp);
  String arg = sp < 0 ? "" : line.substring(sp + 1);
  arg.trim();
  if (cmd == "ack") {
    g_waitAck = 0;
  } else if (cmd == "diag") {
    sendEvent(String("diag ") + (g_diag.length() ? g_diag : "none") + " allowed_now=" + (g_rwAllowed ? 1 : 0));
  } else if (cmd == "ping") {
    sendEvent("pong");
  } else if (cmd == "status") {
    statusLine();
  } else if (cmd == "learn") {
    int i = slotByName(arg);
    if (i < 0) { sendEvent("error unknown_slot"); return; }
    g_learnSlot = i;
    g_learnUntil = millis() + 25000;
    sendEvent(String("learning ") + SLOT_KEY[i]);
    activity(String("Taste druecken: ") + SLOT_LABEL[i]);
  } else if (cmd == "cancel") {
    g_learnSlot = -1;
    sendEvent("cancelled");
    activity("Bereit");
  } else if (cmd == "send") {
    int i = slotByName(arg);
    if (i < 0) { sendEvent("error unknown_slot"); return; }
    bool ok = sendSlot(i);
    sendEvent(String("sent ") + SLOT_KEY[i] + (ok ? " ok" : " empty"));
    activity(ok ? String("Gesendet: ") + SLOT_LABEL[i] : String("Nicht eingelernt: ") + SLOT_LABEL[i]);
  } else if (cmd == "clear") {
    int i = slotByName(arg);
    if (i < 0) { sendEvent("error unknown_slot"); return; }
    g_slots[i].used = 0;
    saveSlot(i);
    sendEvent(String("cleared ") + SLOT_KEY[i]);
    activity(String("Geloescht: ") + SLOT_LABEL[i]);
  } else {
    sendEvent("error unknown_command");
  }
}

static void readSerial() {
  while (USBSerial.available()) {
    char ch = (char)USBSerial.read();
    if (ch == '\n' || ch == '\r') {
      if (g_line.length()) handleCommand(g_line);
      g_line = "";
    } else if (g_line.length() < 120) {
      g_line += ch;
    }
  }
}

// ------------------------------------------------------------------ Anzeige
static void drawScreen() {
  g_dirty = false;
  lcd.startWrite();
  lcd.fillScreen(TFT_BLACK);
  lcd.setTextDatum(textdatum_t::top_left);
  lcd.setFont(&fonts::DejaVu24);
  lcd.setTextColor(0x3D7F, TFT_BLACK);   // Cyan-Blau
  lcd.drawString("WangertTV IR", 12, 8);

  lcd.setFont(&fonts::DejaVu12);
  bool usbOk = (bool)USBSerial;
  bool asleep = tud_suspended();
  bool mounted = tud_mounted();
  const char* state;
  uint16_t col;
  if (asleep) {
    state = g_rwAllowed ? "TV-Box schlaeft - Wecken erlaubt" : "TV-Box schlaeft - Wecken NICHT erlaubt";
    col = g_rwAllowed ? TFT_ORANGE : TFT_RED;
  } else if (!mounted) {
    state = "USB getrennt (PC hat Anschluss abgeschaltet)";   // kein Bus -> Wecken per USB unmoeglich
    col = TFT_RED;
  } else if (usbOk) {
    state = "Mit WangertTV verbunden";
    col = TFT_GREEN;
  } else {
    state = "USB da, WangertTV liest nicht mit";
    col = TFT_LIGHTGREY;
  }
  lcd.setTextColor(col, TFT_BLACK);
  lcd.drawString(state, 12, 40);

  lcd.setFont(&fonts::DejaVu18);
  int y = 64;
  for (int i = 0; i < S_COUNT; ++i) {
    bool on = g_slots[i].used;
    lcd.setTextColor(i == g_learnSlot ? TFT_YELLOW : (on ? TFT_WHITE : 0x7BEF), TFT_BLACK);
    lcd.drawString(String(on ? "+ " : "- ") + SLOT_LABEL[i], 12, y);
    y += 24;
  }
  lcd.setFont(&fonts::DejaVu12);
  lcd.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  lcd.drawString(String("Zuletzt: ") + g_lastCode, 12, 192);
  lcd.setFont(&fonts::DejaVu18);
  lcd.setTextColor(g_learnSlot >= 0 ? TFT_YELLOW : 0x3D7F, TFT_BLACK);
  lcd.drawString(ascii(g_message), 12, 212);
  lcd.endWrite();
}

static void usbEvent(void* arg, esp_event_base_t base, int32_t id, void* data) {
  if (id == ARDUINO_USB_SUSPEND_EVENT) {
    arduino_usb_event_data_t* d = (arduino_usb_event_data_t*)data;
    g_rwAllowed = d ? d->suspend.remote_wakeup_en : false;
    ++g_suspendCount;
  }
  g_dirty = true;
}

static void updateBacklight() {
  bool want = millis() - g_lastActivity < 60000 || g_learnSlot >= 0;
  if (want != g_backlight) {
    g_backlight = want;
    lcd.setBrightness(want ? 90 : 0);
  }
}

// ------------------------------------------------------------------ Start
void setup() {
  pinMode(IR_CTRL, OUTPUT);
  digitalWrite(IR_CTRL, LOW);          // Infrarot-Teil im Sensor-Dock einschalten

  USB.VID(0x303A);
  USB.PID(0x8166);
  USB.productName("WangertTV IR");
  USB.manufacturerName("WangertTV");
  USB.serialNumber("WTVIR1");
  USB.usbAttributes(TUSB_DESC_CONFIG_ATT_SELF_POWERED | TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP);
  USB.onEvent(usbEvent);
  ConsumerControl.begin();
  USBSerial.setTxTimeoutMs(0);         // nie blockieren, wenn niemand mitliest
  USBSerial.begin();
  USB.begin();

  LGFX_Box3::hardwareReset();
  lcd.init();
  lcd.setRotation(2);
  lcd.setBrightness(90);

  loadSlots();
  IrReceiver.begin(IR_RX, DISABLE_LED_FEEDBACK);
  IrSender.begin(IR_TX);
  activity(g_slots[S_PWR].used ? "Bereit" : "In WangertTV Tasten einlernen");
}

void loop() {
  readSerial();
  handleIr();
  wakeLoop();
  if (g_waitAck && millis() - g_waitAck > 800) {
    // keine Bestätigung von WangertTV: PC schläft, ohne den USB-Bus schlafen zu legen -> andere Weckwege
    g_waitAck = 0;
    g_diag = String("noack suspended=") + (tud_suspended() ? 1 : 0) + " allowed=" + (g_rwAllowed ? 1 : 0);
    hidPower();
    if (tud_suspended()) startWake("noack");
    activity("Keine Antwort - wecke per Tastatur");
  }
  if (g_learnSlot >= 0 && (int32_t)(millis() - g_learnUntil) > 0) {
    sendEvent(String("learn_timeout ") + SLOT_KEY[g_learnSlot]);
    g_learnSlot = -1;
    activity("Keine Taste empfangen");
  }
  static bool lastUsb = false, lastSleep = false, lastMounted = false;
  bool usb = (bool)USBSerial, sl = tud_suspended(), mt = tud_mounted();
  if (mt != lastMounted) {
    lastMounted = mt;
    activity("");
  }
  if (usb != lastUsb || sl != lastSleep) {
    lastUsb = usb;
    lastSleep = sl;
    if (usb && !lastSleep) {
      statusLine();
      if (g_diag.length()) sendEvent(String("diag ") + g_diag);
    }
    activity("");
  }
  static uint32_t lastDraw = 0;
  if (g_dirty && millis() - lastDraw > 120) {
    lastDraw = millis();
    drawScreen();
  }
  updateBacklight();
  delay(2);
}
