# Wristband firmware

PlatformIO firmware for an ESP32-S3 Super Mini with an MPU6050 IMU. An on-device
Edge Impulse classifier recognizes bottle **pickup** and **put-down** gestures and
sends each event over BLE to the dashboard's live mode (or any BLE central).

The band only reports *that* a pickup or put-down happened. It never knows which
shelf or medication was involved; the dashboard works that out from the camera.

## Hardware

- ESP32-S3 Super Mini (4 MB flash, native USB).
- MPU6050 on **SDA GPIO 13, SCL GPIO 8**, 3.3 V and GND. Both I²C addresses
  (0x68 with AD0 low, 0x69 with AD0 high) are probed.
- Mount the watch in the same orientation used when the training data was collected.

## Build and flash

```sh
cd wristband
pio run                 # build
pio run -t upload       # flash
pio device monitor      # serial log at 115200 baud
```

`platformio.ini` sets `upload_port` and `monitor_port` to `COM6`; change both to
match your machine (for example `/dev/cu.usbmodem*` on macOS), or delete them to
let PlatformIO auto-detect.

To flash more than one watch, set `WATCH_ID` in `include/WatchConfig.h` (`"01"`,
`"02"`, …) before each upload. IDs must be unique and exactly two ASCII characters.
The BLE device name is `Wristband-<ID>`.

## BLE protocol

The watch is a BLE peripheral that advertises again after every disconnect. Clients
connect without pairing and subscribe to notifications.

| Item | Value |
| --- | --- |
| Service UUID | `c47c5b10-9c49-4b73-9af1-3c0bcabdf001` |
| Event characteristic UUID | `c47c5b11-9c49-4b73-9af1-3c0bcabdf001` (read, notify) |
| Payload | UTF-8 JSON, one object per notification |

```json
{"id":"01","e":"P"}
{"id":"02","e":"D"}
```

`P` is a pickup and `D` a put-down. Reading the characteristic returns the last
event (`"e":"?"` before the first one). The 19-byte payload fits the default
20-byte ATT notification, so no MTU negotiation is needed. There is no event queue,
acknowledgement, timestamp or sequence number, and connections are not
authenticated: use the band only where open BLE access is acceptable.

## Model

- Edge Impulse project 1121932, deployment 2, EON-compiled int8 model, stored
  unmodified in `lib/BottleModel` (keep its license notices). `library.json` adds
  the PlatformIO build manifest.
- Labels: `idle`, `pick_up`, `put_down`, `random`.
- Input: six interleaved axes (`accX, accY, accZ` in m/s², `gyrX, gyrY, gyrZ` in °/s),
  300 samples at **50 Hz** (a 6 s window). Inference repeats every 500 ms on the
  latest window. The export's metadata says 100 Hz, but the training data was
  collected at 50 Hz without resampling, so the firmware feeds real 50 Hz samples
  and leaves the exported DSP settings untouched. Retrain with corrected timestamps
  before changing this.
- Sensor ranges are ±2 g and ±250 °/s with the 42/44 Hz digital low-pass filter.
- The MPU FIFO keeps sampling evenly while a separate FreeRTOS task on core 0 runs
  the classifier. If inference is still busy, that inference slot is skipped rather
  than delaying sampling. I²C errors, FIFO overflow or stalls discard the window and
  reinitialize the sensor.

### Startup calibration

Hold the watch still in its training orientation at power-up. The first 150 samples
(3 s) estimate offsets: gyro means are subtracted, and accelerometer means are
subtracted except for 1 g kept on the dominant axis. The first prediction follows
another 6 s of data. Power-cycle to recalibrate.

### Event gate

`include/EventGate.h` turns raw scores into events. An event needs one prediction
scoring at least 0.65 with a lead of at least 0.15 over the runner-up, and a 1.5 s
cooldown since the last event. One continuous episode of the same class emits once;
two confident `idle`/`random` predictions end the episode. An opposite class may
follow directly. These are application defaults, not measured confidence levels.

## Serial output

```text
PRED end_ms=... idle=... pick_up=... put_down=... random=... top=pick_up state=... gate=... dsp_ms=... nn_ms=...
EVENT PICKUP_LIKELY score=... window_end_ms=...
```

`gate` explains filtering (`confirming`, `uncertain`, `same_episode`, `cooldown`,
`background` or `event`). Send `r` to clear the window and estimated state, or `s`
for sensor status, free heap and skipped windows.

## Known limits

- Event time is the end of a 6 s window, not the moment of contact, and the
  dashboard observes notifications roughly 4–5 s after the physical action. The
  dashboard compensates by saving the 9 s before each notification.
- The detector can produce false or missed events. It has not been validated on
  non-bottle gestures, walking, or other wearers.
- Some training clips included holding/resting poses. A future model should label
  only the reach-and-lift as `pick_up` and the lower-and-place as `put_down`, treat
  stillness (with or without a bottle) as `idle`, and use a shorter 1–2 s window.
