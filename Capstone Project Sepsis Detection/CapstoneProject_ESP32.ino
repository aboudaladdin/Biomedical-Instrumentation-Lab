#include <Wire.h>
#include "MAX30105.h"
#include "WiFi.h"
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <time.h>

MAX30105 particleSensor;

const char* ssid = "Redmi Note 7";
const char* password = "123456abc";

// ============ Signal Processing Configuration ============
const int SAMPLING_RATE = 50;   // Effective rate with sampleAverage (50 Hz)
const int BUFFER_SIZE = 500;    // 10 seconds of data at 50 Hz

float redBuffer[BUFFER_SIZE];
float irBuffer[BUFFER_SIZE];
int bufferIndex = 0;


typedef struct {
  float hr;
  float hrv;
  float pi;
  float spo2;
  float signal_quality;  // 0-100%, higher = better
  float respiration_rate; // breaths per minute
} Metrics;

// Endpoint configuration
const char* baseUrl = "https://db65.uef.fi/api/v1/sepsis-session-data/update";
const int groupId = 2;
const char* sessionId = "1O6SW5VD";

// ============ Temperature Sensors (Wheatstone Bridges) ============
// XIAO ESP32-S3 analog pin assignments (3.3V reference, 12-bit = 4096)
const int T1_VD_PIN = A0;   // A0 (GPIO9) - T1 Wheatstone bridge output D
const int T1_VB_PIN = A1;   // A1 (GPIO8) - T1 Wheatstone bridge output B
const int T2_VD_PIN = A2;   // A2 (GPIO7) - T2 Wheatstone bridge output D
const int T2_VB_PIN = A3;   // A3 (GPIO6) - T2 Wheatstone bridge output B

// Curve fitting coefficients (from calibration)
const float t1_slope = 23.97221557;
const float t1_intercept = 22.15989763;
const float t2_slope = 21.11960757;
const float t2_intercept = 28.57461637;

// ESP32 ADC reference (3.3V) and resolution (12-bit = 4096 levels)
const float ADC_VREF = 3.3;
const int ADC_RESOLUTION = 4096;

// detect finger prescense above this threshold
uint32_t irFingerThreshold = 10000;

void readTemperatures(float &t1, float &t2) {
  // Read ADC values from Wheatstone bridge outputs
  // Lab 1
  int t1_valD = analogRead(T1_VD_PIN);
  int t1_valB = analogRead(T1_VB_PIN);
  int t2_valD = analogRead(T2_VD_PIN);
  int t2_valB = analogRead(T2_VB_PIN);

  // Convert ADC to voltage (ESP32: 3.3V reference, 12-bit resolution)
  float t1_Vd = t1_valD * (ADC_VREF / ADC_RESOLUTION);
  float t1_Vb = t1_valB * (ADC_VREF / ADC_RESOLUTION);
  float t2_Vd = t2_valD * (ADC_VREF / ADC_RESOLUTION);
  float t2_Vb = t2_valB * (ADC_VREF / ADC_RESOLUTION);

  // Calculate differential voltage (Vd - Vb)
  float t1_Vo = t1_Vd - t1_Vb;
  float t2_Vo = t2_Vd - t2_Vb;

  // Apply curve fitting to get temperature
  t1 = t1_slope * t1_Vo + t1_intercept;
  t2 = t2_slope * t2_Vo + t2_intercept;
}

void initWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi ..");
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print('.');
    delay(1000);
  }
  Serial.println();
  Serial.println("Connected As:");
  Serial.println(WiFi.localIP());
}

// ============ Signal Processing Functions ============

void convolve(float *signal, int signal_len, float *filter, int filter_len, float *output) {
  // in case of signal smaller than filter, return the signal
  if (signal_len < filter_len) {
    memcpy(output, signal, signal_len * sizeof(float));
    return;
  }
  // to normalize 
  float filter_sum = 0;
  for (int i = 0; i < filter_len; i++) {
    filter_sum += fabs(filter[i]);
  }
  if (filter_sum == 0) filter_sum = 1;
  
  // Apply convolution with edge padding
  for (int i = 0; i < signal_len; i++) {
    float sum = 0;
    for (int j = 0; j < filter_len; j++) {
      int idx = i - (filter_len - 1) + j;
      if (idx < 0) idx = 0;  // Edge padding
      sum += signal[idx] * filter[j];
    }
    output[i] = sum / filter_sum;
  }
}

void peak_detector(float *signal, int signal_len, int min_time, float min_threshold,
                   int *peak_indices, float *peak_values, int *peak_count) {
  *peak_count = 0;

  for (int i = 1; i < signal_len - 1; i++) {
    if (signal[i] < min_threshold) continue;

    if (signal[i] > signal[i - 1] && signal[i] > signal[i + 1]) {
      if (*peak_count > 0) {
        int last_peak = peak_indices[*peak_count - 1];
        if (i - last_peak > min_time) {
          peak_indices[*peak_count] = i;
          peak_values[*peak_count] = signal[i];
          (*peak_count)++;
        }
      } else {
        peak_indices[*peak_count] = i;
        peak_values[*peak_count] = signal[i];
        (*peak_count)++;
      }
    }
  }
}

float calculate_hr(int *peak_indices, int peak_count) {
  if (peak_count < 2) return 0;

  float mean_interval = 0;
  for (int i = 1; i < peak_count; i++) {
    float interval = (peak_indices[i] - peak_indices[i - 1]) / (float)SAMPLING_RATE;
    mean_interval += interval;
  }
  mean_interval /= (peak_count - 1);

  return (mean_interval > 0) ? (60.0f / mean_interval) : 0;
}

float calculate_hrv(int *peak_indices, int peak_count) {
  if (peak_count < 2) return 0;

  int num_intervals = peak_count - 1;
  if (num_intervals < 2) return 0;

  // Calculate RMSSD without storing all intervals (reduces stack usage)
  float mean_sq_diff = 0;
  float prev_interval = (peak_indices[1] - peak_indices[0]) / (float)SAMPLING_RATE * 1000;

  for (int i = 1; i < num_intervals; i++) {
    float interval = (peak_indices[i + 1] - peak_indices[i]) / (float)SAMPLING_RATE;
    float interval_ms = interval * 1000;
    float diff = interval_ms - prev_interval;
    mean_sq_diff += diff * diff;
    prev_interval = interval_ms;
  }
  mean_sq_diff /= (num_intervals - 1);

  return sqrt(mean_sq_diff);
}

float calculate_pi(float *ir_data, int data_len, int window_samples) {
  if (data_len < window_samples) return 0;

  float *recent = ir_data + (data_len - window_samples);
  float dc = 0;
  for (int i = 0; i < window_samples; i++) {
    dc += recent[i];
  }
  dc /= window_samples;

  if (dc == 0) return 0;

  float ac = 0;
  for (int i = 0; i < window_samples; i++) {
    float diff = recent[i] - dc;
    ac += diff * diff;
  }
  ac = sqrt(ac / window_samples);

  float pi = (ac / dc) * 100;

  // Debug
  // Serial.print("[PI_DEBUG] DC="); Serial.print(dc);
  // Serial.print(" AC="); Serial.print(ac);
  // Serial.print(" PI="); Serial.println(pi);

  return pi;
}

float calculate_spo2(float *red_data, float *ir_data, int data_len, int window) {
  if (data_len < window) return 0;

  float *red_recent = red_data + (data_len - window);
  float *ir_recent = ir_data + (data_len - window);

  float dc_red = 0;
  for (int i = 0; i < window; i++) {
    dc_red += red_recent[i];
  }
  dc_red /= window;

  float ac_red = 0;
  for (int i = 0; i < window; i++) {
    float diff = red_recent[i] - dc_red;
    ac_red += diff * diff;
  }
  ac_red = sqrt(ac_red / window);

  float dc_ir = 0;
  for (int i = 0; i < window; i++) {
    dc_ir += ir_recent[i];
  }
  dc_ir /= window;

  float ac_ir = 0;
  for (int i = 0; i < window; i++) {
    float diff = ir_recent[i] - dc_ir;
    ac_ir += diff * diff;
  }
  ac_ir = sqrt(ac_ir / window);

  // Serial.print("[SPO2_DEBUG] DC_red="); Serial.print(dc_red);
  // Serial.print(" AC_red="); Serial.print(ac_red);
  // Serial.print(" DC_ir="); Serial.print(dc_ir);
  // Serial.print(" AC_ir="); Serial.println(ac_ir);

  if (dc_red <= 0 || dc_ir <= 0 || ac_ir <= 0 || ac_red <= 0) {
    //Serial.println("[SPO2_DEBUG] FAILED validation - returning 0");
    return 0;
  }

  float R = (ac_red / dc_red) / (ac_ir / dc_ir);
  float spo2 = 110 - 25 * R;

  // Serial.print("[SPO2_DEBUG] R="); Serial.print(R);
  // Serial.print(" Raw_SpO2="); Serial.println(spo2);

  if (spo2 < 70) spo2 = 70;
  if (spo2 > 100) spo2 = 100;

  return spo2;
}

float calculate_signal_quality(float *ir_data, int data_len) {
  // Signal quality based on AC/DC ratio
  // Higher AC relative to DC = better pulsatile signal
  // Good signal: AC/DC > 1% → quality > 50%
  // Excellent signal: AC/DC > 2% → quality 100%

  float dc = 0;
  for (int i = 0; i < data_len; i++) {
    dc += ir_data[i];
  }
  dc /= data_len;

  if (dc == 0) return 0;

  float ac = 0;
  for (int i = 0; i < data_len; i++) {
    float diff = ir_data[i] - dc;
    ac += diff * diff;
  }
  ac = sqrt(ac / data_len);

  // AC/DC ratio in percent
  float ac_dc_percent = (ac / dc) * 100;

  // Map to quality: 0.5% = 25%, 1% = 50%, 2% = 100%
  float quality = (ac_dc_percent / 2.0f) * 100;

  // Clamp to 0-100%
  if (quality < 0) quality = 0;
  if (quality > 100) quality = 100;

  return quality;
}

float calculate_respiration_rate(float *ir_data, int data_len) {
  // Detect respiration from low-frequency modulation of PPG signal
  // Breathing causes slow amplitude changes (0.12-0.5 Hz = 7-30 breaths/min)
  // Strategy: Apply lowpass filter, detect peaks in envelope

  if (data_len < 50) return 0;

  // Simple lowpass filter (moving average) to extract envelope
  int envelope_window = data_len / 5;  // ~2 second window
  if (envelope_window < 10) envelope_window = 10;

  float envelope[BUFFER_SIZE];
  for (int i = 0; i < data_len; i++) {
    float sum = 0;
    int count = 0;
    for (int j = i - envelope_window / 2; j <= i + envelope_window / 2; j++) {
      if (j >= 0 && j < data_len) {
        sum += ir_data[j];
        count++;
      }
    }
    envelope[i] = sum / count;
  }

  // Detect peaks in envelope (breathing cycles)
  int breath_peaks[50];
  int breath_count = 0;

  for (int i = 10; i < data_len - 10; i++) {
    if (envelope[i] > envelope[i - 1] && envelope[i] > envelope[i + 1]) {
      // Found a local maximum (inhalation peak)
      if (breath_count == 0 || (i - breath_peaks[breath_count - 1]) > envelope_window / 2) {
        breath_peaks[breath_count] = i;
        breath_count++;
        if (breath_count >= 50) break;
      }
    }
  }

  // Calculate breathing rate from peak intervals
  if (breath_count < 2) return 0;

  float mean_breath_interval = 0;
  for (int i = 1; i < breath_count; i++) {
    float interval = (breath_peaks[i] - breath_peaks[i - 1]) / (float)SAMPLING_RATE;
    mean_breath_interval += interval;
  }
  mean_breath_interval /= (breath_count - 1);

  if (mean_breath_interval > 0) {
    return 60.0f / mean_breath_interval;  // Convert to breaths per minute
  }

  return 0;
}

Metrics process_signal(float *red_data, float *ir_data, int data_len) {
  Metrics result = {0, 0, 0, 0};

  // Static arrays to avoid stack overflow (allocated once)
  static float filtered[BUFFER_SIZE];
  static float temp[BUFFER_SIZE];
  static int peak_indices[BUFFER_SIZE];
  static float peak_values[BUFFER_SIZE];

  // Multi-stage filtering
  float filter1[] = {1, 1, 1};
  float filter2[] = {1, 0, -1};
  float filter3[] = {1, 1, 1, 1, 1};

  convolve(ir_data, data_len, filter1, 3, temp);
  convolve(temp, data_len, filter2, 3, filtered);
  convolve(filtered, data_len, filter3, 5, temp);
  memcpy(filtered, temp, data_len * sizeof(float));

  // Find max for adaptive threshold
  float max_value = 0;
  for (int i = 0; i < data_len; i++) {
    if (filtered[i] > max_value) max_value = filtered[i];
  }

  // Detect peaks
  int peak_count = 0;

  peak_detector(filtered, data_len, 15, max_value * 0.2f, peak_indices, peak_values, &peak_count);

  // Calculate metrics
  result.hr = calculate_hr(peak_indices, peak_count);
  result.hrv = calculate_hrv(peak_indices, peak_count);
  result.pi = calculate_pi(ir_data, data_len, 50);
  result.spo2 = calculate_spo2(red_data, ir_data, data_len, 50);
  result.signal_quality = calculate_signal_quality(ir_data, data_len);
  result.respiration_rate = calculate_respiration_rate(ir_data, data_len);

  return result;
}

void sendSensorData(float t1, float t2, float spo2, int hr, int resp_rate, float hrv_rmssd, float pi) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi not connected!");
    return;
  }

  WiFiClientSecure client;
  client.setInsecure();  // Disable SSL verification (for testing only)

  HTTPClient http;
  String url = String(baseUrl) + "/" + String(groupId) + "/" + String(sessionId);

  Serial.print("[HTTP] Sending to: ");
  Serial.println(url);

  http.begin(client, url);
  http.addHeader("Content-Type", "application/json");

  // Create JSON payload
  JsonDocument doc;
  doc["t1"] = t1;
  doc["t2"] = t2;
  doc["delta_t"] = (int)(t1 - t2);
  doc["spo2"] = spo2;
  doc["hr"] = hr;
  doc["resp_rate"] = resp_rate;
  doc["hrv_rmssd"] = hrv_rmssd;
  doc["perfusion_index"] = pi;
  doc["timestamp"] = millis() / 1000.0;  // Unix timestamp approximation

  String payload;
  serializeJson(doc, payload);

  Serial.print("[HTTP] Payload: ");
  Serial.println(payload);

  // Make PUT request
  int httpCode = http.PUT(payload);

  Serial.print("[HTTP] Status code: ");
  Serial.println(httpCode);
  Serial.print("[HTTP] Response: ");
  Serial.println(http.getString());

  http.end();
}


void setup()
{
  Serial.begin(115200);

  // Set WiFi to station mode and disconnect from an AP if it was previously connected
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);

  initWiFi();

  Serial.println("Initializing Sensors...");

  // Initialize sensor
  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) //Use default I2C port, 400kHz speed
  {
    Serial.println("MAX30105 was not found. Please check wiring/power. ");
    while (1);
  }

  //Setup sensor parameters
  byte ledBrightness = 30;      // Options: 0=Off to 255=50mA
  byte sampleAverage = 1;       // Options: 1, 2, 4, 8, 16, 32
  byte ledMode = 2;             // Options: 1 = Red only, 2 = Red + IR, 3 = Red + IR + Green
  int sampleRate = 100;         // Options: 50, 100, 200, 400, 800, 1000, 1600, 3200
  int pulseWidth = 411;         // Options: 69, 118, 215, 411
  int adcRange = 4096;          // Options: 2048, 4096, 8192, 16384

  particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);

  Serial.println("Sensor configured. Place finger on sensor...");
}

void loop()
{
  // Read sensor data
  uint32_t irValue = particleSensor.getIR();
  uint32_t redValue = particleSensor.getRed();

  if (irValue < irFingerThreshold)
  {
    Serial.println("Place your finger on the sensor...");
    delay(3000);
    return;
  }
  // Convert to float and buffer
  if (bufferIndex < BUFFER_SIZE) {
    Serial.print(".");
    irBuffer[bufferIndex] = (float)irValue;
    redBuffer[bufferIndex] = (float)redValue;
    bufferIndex++;
  }

  // Process signal when buffer is full
  if (bufferIndex >= BUFFER_SIZE) {
    Serial.println("[PROCESSING] Computing metrics...");

    // Calculate metrics from buffered data
    Metrics metrics = process_signal(redBuffer, irBuffer, BUFFER_SIZE);

    // Read actual temperatures from Wheatstone bridges
    float coreTemp, extremityTemp;
    readTemperatures(coreTemp, extremityTemp);

    // Use detected respiration rate, or fallback to 16 if not detected
    int respRate = (metrics.respiration_rate >= 10 && metrics.respiration_rate <= 40) ?
                   (int)metrics.respiration_rate : 16;

    // Validate metrics
    float finalHR = (metrics.hr >= 30 && metrics.hr <= 200) ? metrics.hr : 0;
    float finalHRV = (metrics.hrv >= 1 && metrics.hrv <= 110) ? metrics.hrv : 0;
    float finalPI = (metrics.pi >= 0.1 && metrics.pi <= 20) ? metrics.pi : 0;
    float finalSpO2 = (metrics.spo2 >= 70 && metrics.spo2 <= 100) ? metrics.spo2 : 0;

    // Signal quality indicator
    String quality_bar = "";
    if (metrics.signal_quality >= 80) quality_bar = "[========]";
    else if (metrics.signal_quality >= 60) quality_bar = "[======  ]";
    else if (metrics.signal_quality >= 40) quality_bar = "[====    ]";
    else if (metrics.signal_quality >= 20) quality_bar = "[==      ]";
    else quality_bar = "[        ]";

    Serial.print("[METRICS] SQ:");
    Serial.print(metrics.signal_quality, 0);
    Serial.print("% ");
    Serial.print(quality_bar);
    Serial.print(" | HR: ");
    Serial.print(finalHR);
    Serial.print(" | RR: ");
    Serial.print(respRate);
    Serial.print(" | SpO2: ");
    Serial.print(finalSpO2);
    Serial.print(" | T1: ");
    Serial.print(coreTemp, 1);
    Serial.print(" | T2: ");
    Serial.print(extremityTemp, 1);
    Serial.print(" | HRV: ");
    Serial.print(finalHRV, 1);
    Serial.println();
    

    // Send to server
    sendSensorData(coreTemp, extremityTemp, finalSpO2, (int)finalHR, respRate, finalHRV, finalPI);

    // Reset buffer
    bufferIndex = 0;
  }

  // Small delay 
  delay(5);
}
