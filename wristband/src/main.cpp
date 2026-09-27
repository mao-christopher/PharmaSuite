// Edge Impulse project 1121932, deployment 2; ESP32-S3 + MPU6050.
// SDA=13, SCL=8; m/s^2 and deg/s, exactly as in the original collector.
#include <Arduino.h>
#include <Wire.h>
#include <cstdarg>
#include <cmath>
#include <cstring>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include "edge-impulse-sdk/classifier/ei_run_classifier.h"
#include "MotionWindow.h"
#include "EventGate.h"
#include "WatchConfig.h"

constexpr char BLE_SERVICE_UUID[]="c47c5b10-9c49-4b73-9af1-3c0bcabdf001";
constexpr char BLE_EVENT_UUID[]="c47c5b11-9c49-4b73-9af1-3c0bcabdf001";
BLECharacteristic* eventCharacteristic=nullptr;
static_assert(sizeof(WATCH_ID)==3,"WATCH_ID must be exactly two ASCII characters");
volatile bool bleConnected=false;

class WatchServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer*) override {
    bleConnected=true;
    Serial.println("BLE connected");
  }
  void onDisconnect(BLEServer*) override {
    bleConnected=false;
    BLEDevice::startAdvertising();
    Serial.println("BLE disconnected; advertising resumed");
  }
};

void startBluetooth() {
  String name=String("Wristband-")+WATCH_ID;
  BLEDevice::init(name.c_str());
  BLEServer* server=BLEDevice::createServer();
  server->setCallbacks(new WatchServerCallbacks());
  BLEService* service=server->createService(BLE_SERVICE_UUID);
  eventCharacteristic=service->createCharacteristic(
    BLE_EVENT_UUID,BLECharacteristic::PROPERTY_READ|BLECharacteristic::PROPERTY_NOTIFY);
  eventCharacteristic->addDescriptor(new BLE2902());
  eventCharacteristic->setValue("{\"id\":\"" WATCH_ID "\",\"e\":\"?\"}");
  service->start();
  BLEAdvertising* advertising=BLEDevice::getAdvertising();
  advertising->addServiceUUID(BLE_SERVICE_UUID);
  advertising->setScanResponse(true);
  advertising->start();
  Serial.printf("BLE advertising %s\n",name.c_str());
}

void sendEvent(const char* event) {
  const char code=(strcmp(event,"PICKUP_LIKELY")==0)?'P':'D';
  // 19 bytes, so one complete JSON object fits the default BLE notification payload.
  const char json[]="{\"id\":\"" WATCH_ID "\",\"e\":\"?\"}";
  char packet[sizeof(json)];
  memcpy(packet,json,sizeof(json));
  packet[sizeof(packet)-4]=code;
  eventCharacteristic->setValue(reinterpret_cast<uint8_t*>(packet),sizeof(packet)-1);
  if(bleConnected) eventCharacteristic->notify();
}

constexpr int SDA_PIN=13, SCL_PIN=8;
constexpr uint32_t SAMPLE_HZ=50; // V2 data: confirmed 50 Hz using the supplied collector.
constexpr uint32_t SAMPLE_MS=1000/SAMPLE_HZ;
constexpr uint32_t HOP_SAMPLES=25; // 300 samples = 6 seconds; rolling prediction every 500 ms.
static_assert(EI_CLASSIFIER_PROJECT_DEPLOY_VERSION==2,"Expected v2 export");
static_assert(EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME==6,"Expected six axes");
static_assert(EI_CLASSIFIER_FREQUENCY==100,"Export nominal rate changed: review preprocessing contract");
static_assert(EI_CLASSIFIER_RAW_SAMPLE_COUNT==300,"Expected 300-sample model");
MotionWindow<EI_CLASSIFIER_RAW_SAMPLE_COUNT,6> window;
float inferenceInput[EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE];
TaskHandle_t inferenceTaskHandle=nullptr;
portMUX_TYPE syncLock=portMUX_INITIALIZER_UNLOCKED;
bool busy=false;
uint32_t epoch=0, inferenceEpoch=0, windowEndMs=0;
uint32_t sampleMs=0,lastSampleWall=0,lastRetry=0,hop=0,skippedWindows=0;
bool sensorOnline=false;
uint8_t address=0x68;
SemaphoreHandle_t logMutex=nullptr;
constexpr uint32_t CALIBRATION_SAMPLES=3*SAMPLE_HZ;
double calibrationSum[6]={};
float sensorBias[6]={};
uint32_t calibrationCount=0;
bool calibrated=false;

// Match the supplied data collector, including its dominant-axis gravity rule.
// This is a single-pose offset estimate, not a full accelerometer calibration.
void finishCalibration() {
  for(unsigned a=0;a<6;++a) sensorBias[a]=float(calibrationSum[a]/calibrationCount);
  const float x=fabsf(sensorBias[0]),y=fabsf(sensorBias[1]),z=fabsf(sensorBias[2]);
  const unsigned axis=(z>=x && z>=y)?2:((y>=x)?1:0);
  sensorBias[axis]-=copysignf(9.80665f,sensorBias[axis]);
  calibrated=true;
}

// Serialize complete lines from both tasks instead of interleaving printf pieces.
void logLine(const char* format,...) {
  char line[480];va_list args;va_start(args,format);
  int n=vsnprintf(line,sizeof(line)-2,format,args);va_end(args);
  if(n<0 || n>=int(sizeof(line)-2)) return;
  line[n++]='\n';
  if(logMutex && xSemaphoreTake(logMutex,pdMS_TO_TICKS(50))!=pdTRUE) return;
  Serial.write(reinterpret_cast<const uint8_t*>(line),size_t(n));
  if(logMutex) xSemaphoreGive(logMutex);
}

bool writeReg(uint8_t reg,uint8_t value) {
  Wire.beginTransmission(address);Wire.write(reg);Wire.write(value);
  return Wire.endTransmission()==0;
}
bool readRegs(uint8_t reg,uint8_t* out,size_t n) {
  Wire.beginTransmission(address);Wire.write(reg);
  if(Wire.endTransmission(false)) return false;
  if(Wire.requestFrom(address,n,true)!=n) return false;
  for(size_t i=0;i<n;i++) out[i]=uint8_t(Wire.read());
  return true;
}
int16_t be16(const uint8_t* p) { return int16_t((uint16_t(p[0])<<8)|p[1]); }

bool startSensor() {
  bool found=false;
  for(uint8_t addr=0x68;addr<=0x69;addr++) {
    address=addr;uint8_t who=0;
    if(readRegs(0x75,&who,1) && who==0x68) { found=true;break; }
  }
  if(!found || !writeReg(0x6B,0x80)) return false;
  delay(100);
  // Preserve collector's +/-2 g, +/-250 deg/s and DLPF 42/44 Hz.
  // Match real training rate (50 Hz); preserve the exported DSP metadata.
  if(!writeReg(0x6B,1) || !writeReg(0x6C,0) || !writeReg(0x1A,3) ||
     !writeReg(0x19,19) || !writeReg(0x1B,0) || !writeReg(0x1C,0)) return false;
  uint8_t cfg[4];
  if(!readRegs(0x19,cfg,4) || cfg[0]!=19 || (cfg[1]&7)!=3 ||
     (cfg[2]&0x18)!=0 || (cfg[3]&0x18)!=0) return false;
  // Accel + gyro XYZ, no temperature: 12-byte frames in FIFO.
  return writeReg(0x23,0) && writeReg(0x6A,4) && writeReg(0x6A,0x40) &&
         writeReg(0x23,0x78) && writeReg(0x38,0x11);
}
void invalidate() {
  window.reset();hop=0;
  portENTER_CRITICAL(&syncLock);++epoch;portEXIT_CRITICAL(&syncLock);
}
bool currentEpoch(uint32_t expected) {
  portENTER_CRITICAL(&syncLock);bool same=epoch==expected;portEXIT_CRITICAL(&syncLock);
  return same;
}
void releaseInput() {
  portENTER_CRITICAL(&syncLock);busy=false;portEXIT_CRITICAL(&syncLock);
}
void inferenceTask(void*) {
  EventGate gate;uint32_t previousEpoch=UINT32_MAX;
  for(;;) {
    ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
    // Producer cannot alter inferenceInput or metadata while busy=true.
    uint32_t thisEpoch=inferenceEpoch,endMs=windowEndMs;
    if(previousEpoch!=thisEpoch) { gate.reset();previousEpoch=thisEpoch; }
    signal_t signal;
    ei_impulse_result_t result={};
    int signalError=ei::numpy::signal_from_buffer(inferenceInput,EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE,&signal);
    EI_IMPULSE_ERROR err=signalError?EI_IMPULSE_DSP_ERROR:run_classifier(&signal,&result,false);
    if(!currentEpoch(thisEpoch)) { gate.reset();releaseInput();continue; }
    if(err!=EI_IMPULSE_OK) {
      gate.reset();logLine("ERROR inference=%d",int(err));releaseInput();continue;
    }
    size_t best=0;float second=0;
    for(size_t i=1;i<EI_CLASSIFIER_LABEL_COUNT;i++)
      if(result.classification[i].value>result.classification[best].value) best=i;
    for(size_t i=0;i<EI_CLASSIFIER_LABEL_COUNT;i++)
      if(i!=best && result.classification[i].value>second) second=result.classification[i].value;
    const char* label=result.classification[best].label;
    float score=result.classification[best].value;
    const char* event=gate.update(label,score,second,endMs);
    char scores[180]={};size_t used=0;
    for(size_t i=0;i<EI_CLASSIFIER_LABEL_COUNT;i++)
    {
      int n=snprintf(scores+used,sizeof(scores)-used," %s=%.3f",result.classification[i].label,result.classification[i].value);
      if(n<0 || size_t(n)>=sizeof(scores)-used) break;
      used+=size_t(n);
    }
    logLine("PRED end_ms=%lu%s top=%s state=%s gate=%s dsp_ms=%d nn_ms=%d",
      (unsigned long)endMs,scores,label,gate.state(),gate.reason(),result.timing.dsp,result.timing.classification);
    if(event) {
      logLine("EVENT %s score=%.3f first_evidence_ms=%lu window_end_ms=%lu emitted_ms=%lu",
        event,score,(unsigned long)gate.firstEvidenceMs(),(unsigned long)endMs,(unsigned long)millis());
      sendEvent(event);
    }
    releaseInput();
  }
}
void addSample(const uint8_t* data) {
  float f[6]={be16(data)/16384.0f*9.80665f,be16(data+2)/16384.0f*9.80665f,
              be16(data+4)/16384.0f*9.80665f,be16(data+6)/131.0f,
              be16(data+8)/131.0f,be16(data+10)/131.0f};
  sampleMs+=SAMPLE_MS;
  if(!calibrated) {
    for(unsigned a=0;a<6;++a) calibrationSum[a]+=f[a];
    if(++calibrationCount==CALIBRATION_SAMPLES) {
      finishCalibration();
      logLine("CAL done samples=%lu accel_bias=%.4f,%.4f,%.4f gyro_bias=%.4f,%.4f,%.4f; filling window.",
        (unsigned long)calibrationCount,sensorBias[0],sensorBias[1],sensorBias[2],
        sensorBias[3],sensorBias[4],sensorBias[5]);
    }
    return; // Never train/classify on startup calibration frames.
  }
  for(unsigned a=0;a<6;++a) f[a]-=sensorBias[a];
  window.push(f);++hop;
  if(!window.full() || hop<HOP_SAMPLES) return;
  hop=0;
  portENTER_CRITICAL(&syncLock);
  bool available=!busy;
  if(available) busy=true;
  portEXIT_CRITICAL(&syncLock);
  if(!available) { ++skippedWindows;return; }
  window.copy(inferenceInput);inferenceEpoch=epoch;windowEndMs=sampleMs;
  xTaskNotifyGive(inferenceTaskHandle);
}
void setup() {
  logMutex=xSemaphoreCreateMutex();
  Serial.setTxBufferSize(2048);
  Serial.begin(115200);
  uint32_t start=millis();while(!Serial && millis()-start<2000) delay(10);
  logLine("Edge Impulse v2: idle / pick_up / put_down / random; 50 Hz acquisition, 6 s rolling window, prediction every 500 ms.");
  logLine("MPU6050 SDA=13 SCL=8. Hold still for 3 s calibration, then 300 samples before first prediction.");
  startBluetooth();
  Wire.begin(SDA_PIN,SCL_PIN,400000);Wire.setTimeOut(10);
  if(xTaskCreatePinnedToCore(inferenceTask,"inference",24576,nullptr,1,&inferenceTaskHandle,0)!=pdPASS) {
    logLine("ERROR: could not allocate inference task.");
    while(true) delay(1000);
  }
}
void loop() {
  uint32_t now=millis();
  if(!sensorOnline) {
    if(lastRetry && now-lastRetry<2000) { delay(1);return; }
    lastRetry=now;sensorOnline=startSensor();
    if(!sensorOnline) { logLine("ERROR MPU6050: check SDA=13 SCL=8, power and address. Retrying.");return; }
    invalidate();sampleMs=lastSampleWall=millis();
    if(!calibrated) {
      calibrationCount=0;
      for(double &v:calibrationSum) v=0;
      logLine("CAL start: hold still in training startup orientation for 3 seconds.");
    } else logLine("MPU6050 reconnected; keeping startup calibration; filling window.");
  }
  uint8_t status,count[2];
  bool ok=readRegs(0x3A,&status,1) && readRegs(0x72,count,2);
  unsigned bytes=ok?((unsigned(count[0])<<8)|count[1]):0;
  if(!ok || (status&0x10) || bytes>=1024 || millis()-lastSampleWall>250) {
    invalidate();sensorOnline=false;
    logLine("ERROR sensor/FIFO gap: invalidated window and state; reconnecting.");return;
  }
  unsigned frames=bytes/12;if(frames>16) frames=16;
  for(unsigned i=0;i<frames;i++) {
    uint8_t data[12];
    if(!readRegs(0x74,data,12)) { invalidate();sensorOnline=false;break; }
    lastSampleWall=millis();addSample(data);
  }
  if(Serial.available()) {
    const char cmd=char(Serial.read());
    if(cmd=='r') { invalidate();logLine("Reset: filling a fresh window; inferred state unknown."); }
    if(cmd=='s') logLine("STATUS sensor=%u skipped_windows=%lu free_heap=%lu",
      sensorOnline,(unsigned long)skippedWindows,(unsigned long)ESP.getFreeHeap());
  }
  delay(1);
}
