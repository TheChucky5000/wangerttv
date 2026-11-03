#pragma once
// LovyanGFX-Konfiguration für die ESP32-S3-BOX-3 – übernommen aus Mikes funktionierendem PC-Monitor (pcbox3):
//   LCD ILI9342C an SPI2: SCLK=7 MOSI=6 MISO=0 DC=4 CS=5, Hintergrundlicht GPIO47 (PWM)
//   RST GPIO48 ist HIGH-aktiv (Ruhepegel LOW) – den Reset machen wir selbst
//   Das Panel ist um 180° verbaut -> Rotation 2 = richtig herum
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

static constexpr int BOX3_LCD_RST = 48;
static constexpr int BOX3_LCD_BL = 47;

class LGFX_Box3 : public lgfx::LGFX_Device {
  lgfx::Panel_ILI9342 _panel;
  lgfx::Bus_SPI _bus;
  lgfx::Light_PWM _light;

 public:
  LGFX_Box3() {
    {
      auto cfg = _bus.config();
      cfg.spi_host = SPI2_HOST;
      cfg.spi_mode = 0;
      cfg.freq_write = 40000000;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = false;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = 7;
      cfg.pin_mosi = 6;
      cfg.pin_miso = 0;
      cfg.pin_dc = 4;
      _bus.config(cfg);
      _panel.setBus(&_bus);
    }
    {
      auto cfg = _panel.config();
      cfg.pin_cs = 5;
      cfg.pin_rst = -1;  // HIGH-aktiv -> siehe hardwareReset()
      cfg.pin_busy = -1;
      cfg.panel_width = 320;
      cfg.panel_height = 240;
      cfg.memory_width = 320;
      cfg.memory_height = 240;
      cfg.offset_x = 0;
      cfg.offset_y = 0;
      cfg.offset_rotation = 0;
      cfg.dummy_read_pixel = 8;
      cfg.dummy_read_bits = 1;
      cfg.readable = false;
      cfg.invert = false;
      cfg.rgb_order = false;
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;
      _panel.config(cfg);
    }
    {
      auto cfg = _light.config();
      cfg.pin_bl = BOX3_LCD_BL;
      cfg.invert = false;
      cfg.freq = 12000;
      cfg.pwm_channel = 7;
      _light.config(cfg);
      _panel.setLight(&_light);
    }
    setPanel(&_panel);
  }

  // Reset mit der richtigen Polarität (HIGH-aktiv), wie im PC-Monitor. MUSS vor init() laufen.
  static void hardwareReset() {
    pinMode(BOX3_LCD_RST, OUTPUT);
    digitalWrite(BOX3_LCD_RST, LOW);
    delay(10);
    digitalWrite(BOX3_LCD_RST, HIGH);
    delay(20);
    digitalWrite(BOX3_LCD_RST, LOW);
    delay(150);
  }
};
