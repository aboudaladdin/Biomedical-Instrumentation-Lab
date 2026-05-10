/*
  Heart beat plotting!
  By: Nathan Seidle @ SparkFun Electronics
  Date: October 20th, 2016
  https://github.com/sparkfun/MAX30105_Breakout

  Shows the user's heart beat on Arduino's serial plotter

  Instructions:
  1) Load code onto Redboard
  2) Attach sensor to your finger with a rubber band (see below)
  3) Open Tools->'Serial Plotter'
  4) Make sure the drop down is set to 115200 baud
  5) Checkout the blips!
  6) Feel the pulse on your neck and watch it mimic the blips

  It is best to attach the sensor to your finger using a rubber band or other tightening
  device. Humans are generally bad at applying constant pressure to a thing. When you
  press your finger against the sensor it varies enough to cause the blood in your
  finger to flow differently which causes the sensor readings to go wonky.

  Hardware Connections (Breakoutboard to Arduino):
  -5V = 5V (3.3V is allowed)
  -GND = GND
  -SDA = A4 (or SDA)
  -SCL = A5 (or SCL)
  -INT = Not connected

  The MAX30105 Breakout can handle 5V or 3.3V I2C logic. We recommend powering the board with 5V
  but it will also run at 3.3V.
*/

#include <Wire.h>
#include "MAX30105.h"

MAX30105 particleSensor;
const float t1_slope = 23.97221557; // From Curve Fitting
const float t1_intercept = 22.15989763; // From Curve Fitting

const float t2_slope = 21.11960757; // From Curve Fitting
const float t2_intercept = 28.57461637; // From Curve Fitting

void setup()
{
  Serial.begin(115200);
  Serial.println("Initializing...");

  // Initialize sensor
  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) //Use default I2C port, 400kHz speed
  {
    Serial.println("MAX30105 was not found. Please check wiring/power. ");
    while (1);
  }

  //Setup to sense a nice looking saw tooth on the plotter
  byte ledBrightness = 42; //Options: 0=Off to 255=50mA
  byte sampleAverage = 1; //Options: 1, 2, 4, 8, 16, 32
  byte ledMode = 2; //Options: 1 = Red only, 2 = Red + IR, 3 = Red + IR + Green
  int sampleRate = 100; //Options: 50, 100, 200, 400, 800, 1000, 1600, 3200
  int pulseWidth = 411; //Options: 69, 118, 215, 411
  int adcRange = 8192; //Options: 2048, 4096, 8192, 16384

  particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange); //Configure sensor with these settings
}

void loop() {
  // read tempratures
  int t1_valD = analogRead(A0);
  int t1_valB = analogRead(A1);
  int t2_valD = analogRead(A2);
  int t2_valB = analogRead(A3);

  // ADC
  float t1_Vd= t1_valD * (5.0 / 1023.0);
  float t1_Vb = t1_valB * (5.0 / 1023.0);
  float t2_Vd= t2_valD * (5.0 / 1023.0);
  float t2_Vb = t2_valB * (5.0 / 1023.0);

  // Calculate the voltage at each node (D abd B)
  float t1_Vo = t1_Vd-t1_Vb;
  float t2_Vo = t2_Vd-t2_Vb;

  float t1_temperature = t1_slope * t1_Vo + t1_intercept;
  float t2_temperature = t2_slope * t2_Vo + t2_intercept;


  // Send to Serial in CSV format: T1,T2, RED,IR
  Serial.print(t1_temperature);
  Serial.print(",");
  Serial.print(t2_temperature);
  Serial.print(",");
  Serial.print(particleSensor.getRed());
  Serial.print(",");
  Serial.println(particleSensor.getIR()); 
  
}