const float slope = 23.97221557; // From Curve Fitting
const float intercept = 22.15989763; // From Curve Fitting

void setup() {
  Serial.begin(9600);
}

void loop() {
  int valD = analogRead(A0);
  int valB = analogRead(A1);

  // Calculate the voltage at each node (D abd B)
  float Vd= valD * (5.0 / 1023.0);
  float Vb = valB * (5.0 / 1023.0);

  float Vo = Vd-Vb;

  // Use our derived parameters 
  float temperature = slope * Vo + intercept;

  Serial.println("============================");
  Serial.print("Bridge Vo: ");
  Serial.print(Vo * 1000);
  Serial.print("mV | Temperature: ");
  Serial.print(temperature);
  Serial.println(" °C");

  delay(1000);
}