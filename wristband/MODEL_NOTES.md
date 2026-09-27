# MPU6050 + your Edge Impulse model

Open this directory in PlatformIO. The exported model has been moved into `lib/BottleModel`. Its generated SDK/model files are preserved unchanged; `library.json` supplies the PlatformIO source selection.

## Upload and classify

```sh
pio run
pio run -t upload
pio device monitor
```

The existing COM6 port, native USB, 4 MB flash and board configuration are retained. Change `upload_port` and `monitor_port` if Windows assigns a different port. Close the Edge Impulse data forwarder before uploading/monitoring. Reset the board after opening the monitor if necessary.

Wiring remains **SDA GPIO 13, SCL GPIO 8, 3.3 V, GND**. AD0 low uses 0x68; AD0 high uses 0x69. Both addresses are probed. The code accepts MPU6050 identity 0x68; other sensor models are not assumed to be compatible.

After an initial six seconds of valid samples, serial output shows all four scores every approximately 500 ms:

```text
PRED end_ms=... idle=... pick_up=... put_down=... random=... top=pick_up state=... dsp_ms=... nn_ms=...
EVENT PICKUP_LIKELY score=... window_end_ms=...
```

Numbers above are placeholders. `gate` explains filtering: `confirming`, `uncertain`, `same_episode`, `cooldown`, `background`, or `event`. `first_evidence_ms` is the end of the first qualifying prediction window, not physical action onset; `emitted_ms` is the firmware clock when the event is printed. `PRED` shows the actual model result even when the event filter rejects it. `EVENT` requires one prediction with score >=0.65, a >=0.15 lead over the next label, and a 1.5-second cooldown. An opposite class can transition directly without idle/random in between. The same continuous class episode emits once; two confident idle/random predictions allow a new episode of that same class. Estimated held/down state is descriptive and never blocks a new episode. These are application defaults, not measured confidence guarantees. Adjust `include/EventGate.h` if needed. The estimated state may be wrong after a missed/false event.

Send `r` to clear the window and estimated state; send `s` for sensor status, free heap and skipped inference windows. No gesture calibration or training happens on the device. No Wi-Fi/cloud connection is used.

## Exact model contract

- Project 1121932, deployment 2: `poke535-project-1`.
- EON compiled int8 model, four labels: `idle`, `pick_up`, `put_down`, `random`.
- Actual acquisition: **50 Hz / 20 ms**, matching the user-confirmed recordings. Each rolling window has 300 frames = six seconds. **Inference repeats every 500 ms**, using the latest window; it does not wait another six seconds. Exported nominal metadata is 100 Hz and remains unchanged.
- Six interleaved axes: `accX, accY, accZ, gyrX, gyrY, gyrZ` (1,800 floats).
- Acceleration m/s², gyro degrees/s, inferred from your original collection firmware. The export specifies names/order/rate but does not itself establish the physical units used in the dataset.
- MPU6050 ranges stay at ±2 g and ±250 degrees/s, with the original 42/44 Hz digital filter. Startup bias correction matches the supplied collection sketch: 150 frames (three seconds) are averaged, gyro means are subtracted, and accelerometer means are subtracted except for signed 1 g retained on the strongest axis (ties prefer Z, then Y).
- The SDK performs its exported spectral analysis and standard-scaler normalization. Feed calibrated sensor values in the collector units, not hand-computed FFT features.

**Sampling choice for this export:** you confirmed recordings were taken at 50 Hz and chose to keep 50 Hz live acquisition. The export declares 100 Hz. Firmware passes 300 real 50 Hz samples per window, assuming those recorded samples were used directly without resampling before training. The exported DSP metadata stays untouched: changing its nominal frequency alone could change spectral features relative to training. For future retraining, correct the dataset timestamps/sample rate to 50 Hz and re-export. The six-second window limits event timing precision, but results update every half-second after warmup.

## Implementation

The MPU's hardware FIFO supplies evenly spaced 50 Hz records. Acquisition continues on the Arduino task while a separate core-0 FreeRTOS task runs classification. A stable snapshot is taken every 25 new samples; if inference is still busy, that inference opportunity is skipped instead of delaying sampling. I2C errors, FIFO overflow or a sample stall invalidate the entire window and any pending result; the sensor is reinitialized and six new seconds are collected.

Full `run_classifier()` is used on overlapping windows. `run_classifier_continuous()` is not assumed to support this spectral sensor-fusion impulse. Output time is the end of a six-second sample window, not exact finger contact time. A window spans indices 0..299, or 5.98 s between first and last sample timestamps; it represents the export's nominal three-second input sampled over six real seconds.

The portable EON kernels are used, with ESP-NN/ESP-DSP and ARM CMSIS acceleration disabled for this generic Arduino build. The model still uses EON; disabling those optional accelerators does not switch the model to a different trained network. PSRAM is not required.

Your previous `src/main.cpp` and `platformio.ini` are saved in `backup/data-forwarder/`. The original model file hashes are in `backup/model-original-hashes.json`. Keep the generated license notices with the model.

This build can classify whatever the model learned; it does not establish real-world bottle specificity. Confirm performance on live stationary/walking use and non-bottle gestures. Sensor orientation and mounting should match training.

Host regression test:

```sh
g++ -std=c++11 -Wall -Wextra -Werror -I include test/test_pipeline.cpp -o test_pipeline
./test_pipeline
```




## Improving event timing after the first live test

The original filter incorrectly required background between opposite classes. That lockout is fixed. The pasted log contained strong putdown predictions that were blocked by that rule. Serial output now uses complete mutex-protected lines with a larger USB transmit buffer.

You confirmed that target training clips included holding/resting. Such labels may teach poses rather than motion transitions. For the next model, label only reaching/lifting as pick_up and lowering/placing as put_down. Label stillness both with and without the bottle as idle, and unrelated movement as random. A proposed starting window is 1–2 seconds at the correct 50 Hz, with short overlapping updates. Regenerate features and retrain; do not shorten this exported model's fixed 300-frame input manually. Include negative examples and separate-session testing. Exact grip/release timing is not directly observable from a wrist IMU.


## Startup sensor calibration

Hold the watch still in the same orientation used at collection startup. After sensor initialization, the first 150 FIFO frames (three seconds at 50 Hz) estimate offsets using the supplied collector algorithm. Calibration frames never enter the model window. `CAL start` and `CAL done` mark progress and report offsets. First inference follows another six seconds of data (roughly nine seconds after sensor startup, plus boot/USB delay). Subsequent predictions remain every 500 ms.

This dominant-axis method does not preserve arbitrary tilted gravity vectors; startup pose must match collection. It does not detect movement during calibration. Restart the board if you moved. No full multi-position accelerometer calibration or gesture training is performed. Completed offsets survive automatic sensor reconnections; an interrupted calibration restarts after reconnection. The `r` command resets prediction history only. Power cycling/restarting recalibrates.

## V2 update

Installed and SHA256-verified the v2 export. Previous model: `backup/BottleModel-v1-complete`. Original v2 hashes: `backup/model-v2-original-hashes.json`. User confirmed v2 recordings used the supplied 50 Hz collector. Startup calibration remains three seconds, then a six-second rolling window with predictions every 500 ms. Exported 100 Hz DSP metadata remains unchanged under the existing no-resampling assumption.
