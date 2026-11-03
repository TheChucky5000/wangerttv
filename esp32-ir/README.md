# WangertTV IR – ESP32-S3-BOX-3 als Infrarot-Empfänger und -Sender

Die ESP32-S3-BOX-3 **mit Sensor-Dock** hängt per USB-C an der TV-Box und übernimmt das, was dem Geekom fehlt:

- **Ein/Aus-Taste der Fernbedienung empfangen:** Die waipu.tv-Fernbedienung schaltet den Fernseher per Infrarot.
  Die BOX-3 sieht dasselbe Signal und meldet es an WangertTV. Fernseher und Box gehen gemeinsam an und aus.
- **Fernseher und AV-Receiver per Infrarot schalten:** WangertTV sendet beim Standby „Receiver aus“ und beim
  Aufwachen „Receiver ein“. Das ersetzt den fehlenden HDMI-CEC-Adapter beim Ein- und Ausschalten.
- **Aus dem Ruhezustand wecken:** Die BOX-3 ist für den PC eine USB-Tastatur und darf ihn aufwecken.
- **Display:** zeigt, welche Tasten eingelernt sind und was zuletzt empfangen wurde.

## 1. Firmware aufspielen (einmalig, am Windows-PC)

> **Reihenfolge:** Erst WangertTV ab Version 1.0.14 auf der Box installieren, dann die Firmware 1.0.1 flashen.
> (Die Firmware erwartet eine Bestätigung von WangertTV – ältere Versionen senden sie noch nicht.)

1. Die BOX-3 per USB-C an den PC stecken (Sensor-Dock bleibt dran).
2. In **Chrome oder Edge** öffnen: <https://espressif.github.io/esptool-js/>
3. **Baudrate** 921600 → **Connect** → den Anschluss der BOX-3 wählen.
   - Erscheint kein Anschluss oder schlägt die Verbindung fehl: an der BOX-3 die **Boot-Taste** gedrückt halten,
     kurz **Reset** drücken, Boot loslassen – dann erneut „Connect“.
4. Bei **Flash Address** `0x0` eintragen und die Datei `wangerttv-ir-1.0.1-factory.bin` wählen → **Program**.
5. Danach **Reset** drücken. Auf dem Display erscheint „WangertTV IR“.

> Die bisherige Firmware (RoboEyes/PC-Monitor) wird dabei überschrieben. Zurück geht jederzeit genauso mit deren Datei.
> Spätere Updates dieser Firmware: immer mit Boot + Reset in den Download-Modus wechseln.

Alternativ mit PlatformIO: `pio run -t upload` in diesem Ordner.

## 2. An die TV-Box anschließen

- BOX-3 per USB-C an einen USB-Anschluss des Geekom.
- So aufstellen, dass sie die Fernbedienung „sieht“ (Infrarot-Fenster vorne am Sensor-Dock), z. B. neben den Fernseher.
- Für das Senden an Fernseher und Receiver muss die Vorderseite grob in deren Richtung zeigen
  (Infrarot wird auch über Wände reflektiert, meistens klappt es auch seitlich).

## 3. In WangertTV einlernen

Einstellungen → Fernbedienung → **Infrarot (ESP32-S3-BOX-3)**:

| Eintrag | Mit welcher Fernbedienung | Wozu |
|---|---|---|
| Ein/Aus-Taste der Fernbedienung | waipu.tv-Fernbedienung, Ein/Aus | Box geht mit dem Fernseher an und aus |
| Receiver Ein | Receiver-Fernbedienung, „On“/„Power On“ | wird beim Aufwachen gesendet |
| Receiver Aus | Receiver-Fernbedienung, „Standby“/„Power Off“ | wird beim Standby gesendet |
| Receiver Ein/Aus (eine Taste) | nur wenn es keine getrennten Tasten gibt | Umschalten |
| Fernseher Ein/Aus senden | TV-Fernbedienung, Ein/Aus | wenn die Box per Handy, Web oder Sleep-Timer an/aus geht |

Getrennte Ein- und Aus-Tasten sind am zuverlässigsten: Damit kann der Receiver nie „aus dem Takt“ kommen.
Bei einer gemeinsamen Taste sendet WangertTV sie nur, wenn sicher ist, dass der Receiver gerade anders steht
(nicht z. B. nach einem Neustart der Box).

## Technik

- Pins im Sensor-Dock: Empfänger GPIO38, Sender GPIO39, Infrarot-Teil einschalten GPIO44 = LOW
- Display wie im PC-Monitor: ILI9342C an SPI2, Reset GPIO48 HIGH-aktiv, Rotation 2
- USB: TinyUSB, VID 0x303A / PID 0x8166, Produktname „WangertTV IR“ – CDC (Befehle) + HID Consumer Control
- Befehle (Text, eine Zeile): `ping`, `status`, `learn <slot>`, `cancel`, `send <slot>`, `clear <slot>`, `ack`, `diag`
- Aufwecken: USB Remote Wakeup (bis 6 s lang wiederholt); kommt auf „key power“ keine Bestätigung (`ack`), schläft
  der PC vermutlich ohne den USB-Bus schlafen zu legen – dann zusätzlich Ein/Aus als USB-Tastatur.
  Das Ergebnis steht nach dem Aufwachen im Protokoll: `journalctl -u wtv-remote | grep Diagnose`
- Slots: `pwr`, `tvp`, `avp`, `avon`, `avoff` – gespeichert im NVS, überstehen Stromausfall
- Erkennt NEC, Samsung, Sony, RC5, RC6 (Wechsel-Bit wird ignoriert), Panasonic/Kaseikyo, Denon, JVC, LG u. v. m.;
  unbekannte Fernbedienungen werden als Rohsignal gespeichert und genauso wieder gesendet.
