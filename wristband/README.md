# Wristband firmware

ESP32-S3 Super Mini + MPU6050 firmware for the Edge Impulse v2 pickup/putdown model. This is a standalone PlatformIO project. See [MODEL_NOTES.md](MODEL_NOTES.md) for sensor, model, and event-gate details.

## Two watches

1. Open this folder in PlatformIO. Set `WATCH_ID` in `include/WatchConfig.h` to `"01"` for the first watch.
2. Build and upload with `pio run -t upload`. Disconnect the first board.
3. Change `WATCH_ID` to `"02"`, rebuild, and upload to the second board. Each board retains the firmware flashed to it.
4. If the serial port is not COM6, change `upload_port` and `monitor_port` in `platformio.ini`. The MPU6050 uses SDA GPIO 13, SCL GPIO 8, 3.3 V, and GND.

The ID must be exactly two ASCII characters. Make IDs unique. The BLE device name is `Wristband-01` or `Wristband-02`.

## BLE data

Each watch advertises a BLE GATT service. Any BLE central application can scan for the `Wristband-XX` name, connect without pairing, and subscribe to notifications on the event characteristic. No approval button or application allowlist is used. The device advertises again after a disconnect. The watch is a BLE peripheral, so a central application must initiate the connection. This is BLE, not Bluetooth Classic serial.

- Service UUID: `c47c5b10-9c49-4b73-9af1-3c0bcabdf001`
- Event characteristic UUID: `c47c5b11-9c49-4b73-9af1-3c0bcabdf001`
- Characteristic properties: read and notify; subscribe through its Client Characteristic Configuration Descriptor (CCCD).
- Notification encoding: UTF-8 JSON, exactly one object per notification, 19 bytes with a two-character ID.

Examples:

```json
{"id":"01","e":"P"}
{"id":"02","e":"D"}
```

`P` means a detected pickup; `D` means a detected putdown. Reading the characteristic gives the last event; its initial value has `e` set to `?` to mean no event yet. Notifications carry new events only. A client that connects after an event can read the last value, but there is no durable event queue or acknowledgement. The serial monitor still reports scores and timing. The detector may make false or missed predictions; validate on the actual mounted watches.

The event packet deliberately fits the default BLE ATT notification payload of 20 bytes, so clients do not need to negotiate a larger MTU. The firmware does not authenticate connections; use it only where open BLE access is acceptable.

## Build

```powershell
cd C:\Users\yuvan\Documents\Cursor\MPU6050-wristband
C:\Users\yuvan\.platformio\penv\Scripts\pio.exe run
```

See `MODEL_NOTES.md` for the model's 50 Hz acquisition versus 100 Hz exported metadata assumption and calibration behavior. No physical upload or live BLE test was performed as part of this code change.
