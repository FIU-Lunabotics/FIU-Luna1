#include <Servo.h>

Servo esc1;
Servo esc2;

constexpr byte START_BYTE = 0xFF;
constexpr byte END_BYTE = 0xFF;
constexpr byte start_byte_bm = 0b11111111;
constexpr byte end_byte_bm = 0b11111111;

constexpr uint8_t PACKET_SIZE = 7;        // start + 6 data bytes
constexpr uint8_t PACKET_TOTAL_SIZE = 8;  // start + 6 data bytes + end
constexpr size_t BUFFER_SIZE = 64;

constexpr byte DXP_POS_MASK = 1 << 2;
constexpr byte DXP_NEG_MASK = 1 << 3;
constexpr byte DYP_NEG_MASK = 1 << 4;
constexpr byte DYP_POS_MASK = 1 << 5;
constexpr bool DPAD_Y_NEG_IS_UP = false;  // Observed controller reports DOWN as -1 and UP as +1.

struct ControlPacket {
  byte start_byte = 0;
  byte primary_buttons = 0;
  byte secondary_buttons = 0;
  byte joy_left_y = 0;
  byte joy_right_y = 0;
  byte trigger_left = 0;
  byte trigger_right = 0;
  byte end_byte = 0;

  byte barr[BUFFER_SIZE] = {0};
  int sizeOfBarr = 0;

  bool isStartByte(byte v) {
    return (v & start_byte_bm) == START_BYTE;
  }

  bool isEndByte(byte v) {
    return (v & end_byte_bm) == END_BYTE;
  }

  int32_t update() {
    byte tempBarr[PACKET_TOTAL_SIZE] = {0};
    int32_t result = Serial.readBytes(tempBarr, PACKET_TOTAL_SIZE);

    if (result <= 0) {
      return -1;
    }

    if (sizeOfBarr + result > static_cast<int>(BUFFER_SIZE)) {
      sizeOfBarr = 0;
      memset(barr, 0, sizeof(barr));
      return -1;
    }

    for (int i = 0; i < result; i++) {
      barr[sizeOfBarr + i] = tempBarr[i];
    }
    sizeOfBarr += result;

    if (sizeOfBarr < PACKET_TOTAL_SIZE) {
      return -1;
    }

    bool foundPacket = false;
    int packetStartIndex = -1;

    for (int i = 0; i <= (sizeOfBarr - PACKET_TOTAL_SIZE); i++) {
      if (isStartByte(barr[i]) && isEndByte(barr[i + PACKET_SIZE])) {
        foundPacket = true;
        packetStartIndex = i;
        break;
      }
    }

    if (!foundPacket) {
      // Keep only the last possible frame prefix to avoid unbounded growth.
      int keepFrom = sizeOfBarr - (PACKET_TOTAL_SIZE - 1);
      if (keepFrom < 0) {
        keepFrom = 0;
      }
      int newSize = sizeOfBarr - keepFrom;
      for (int i = 0; i < newSize; i++) {
        barr[i] = barr[keepFrom + i];
      }
      for (int i = newSize; i < sizeOfBarr; i++) {
        barr[i] = 0;
      }
      sizeOfBarr = newSize;
      return -1;
    }

    for (int i = 0; i < PACKET_TOTAL_SIZE; i++) {
      tempBarr[i] = barr[packetStartIndex + i];
    }

    int consumed = packetStartIndex + PACKET_TOTAL_SIZE;
    int remaining = sizeOfBarr - consumed;
    for (int i = 0; i < remaining; i++) {
      barr[i] = barr[consumed + i];
    }
    for (int i = remaining; i < sizeOfBarr; i++) {
      barr[i] = 0;
    }
    sizeOfBarr = remaining;

    this->start_byte = tempBarr[0];
    this->primary_buttons = tempBarr[1];
    this->secondary_buttons = tempBarr[2];
    this->joy_left_y = tempBarr[3];
    this->joy_right_y = tempBarr[4];
    this->trigger_left = tempBarr[5];
    this->trigger_right = tempBarr[6];
    this->end_byte = tempBarr[7];

    return result;
  }

  bool dPadRight() const {
    return (secondary_buttons & DXP_POS_MASK) != 0;
  }

  bool dPadLeft() const {
    return (secondary_buttons & DXP_NEG_MASK) != 0;
  }

  bool dPadUp() const {
    byte mask = DPAD_Y_NEG_IS_UP ? DYP_NEG_MASK : DYP_POS_MASK;
    return (secondary_buttons & mask) != 0;
  }

  bool dPadDown() const {
    byte mask = DPAD_Y_NEG_IS_UP ? DYP_POS_MASK : DYP_NEG_MASK;
    return (secondary_buttons & mask) != 0;
  }

  bool getYButton() const {
    return (primary_buttons & (1 << 0)) != 0;  // Check bit 0 for Y button
  }
};

ControlPacket controllerPacket;

bool vibrationOn = false;
bool lastYButtonState = false;

int escPin1 = 9;
int escPin2 = 10;
int liftPwmPin = 11;  // Lift actuator MD20A PWM input
int tiltPwmPin = 12;  // Tilt actuator MD20A PWM input
int vibrationPin = 3;  // PWM pin for vibration motor
constexpr uint8_t HALL1_A = 22;
constexpr uint8_t HALL1_B = 24;
constexpr uint8_t HALL1_C = 26;
constexpr uint8_t HALL2_A = 28;
constexpr uint8_t HALL2_B = 30;
constexpr uint8_t HALL2_C = 32;
// int button1 = 2; removed these lines to establish
// int button2 = 4;  (was 3, now used for vibration)
// int button3 = 5;  (was 4)
// int button4 = 8;  (was 5)
int liftDirPin = 6;  // Lift actuator MD20A DIR input
int tiltDirPin = 7;  // Tilt actuator MD20A DIR input
// int potPin = A0;

constexpr bool BIDIRECTIONAL_ESC = true;
constexpr byte joystickCenter = 128;
constexpr byte joystickDeadband = 8;
constexpr bool DEBUG_SERIAL = false;
constexpr bool HALL_TELEMETRY_SERIAL = true;  // Serial is also the command link; enable only when something reads telemetry.
constexpr unsigned long DEBUG_INTERVAL_MS = 1000;
constexpr unsigned long HALL_TELEMETRY_INTERVAL_MS = 500;

int escPulseMin = 1000;
int escPulseNeutral = 1500;
int escPulseMax = 1750;
unsigned long lastDebugPrintMs = 0;
unsigned long lastHallTelemetryPrintMs = 0;

constexpr uint8_t POLE_PAIRS = 4;
constexpr uint8_t HALL_TRANSITIONS_PER_REV = POLE_PAIRS * 6;
constexpr uint32_t RPM_UPDATE_INTERVAL_MS = 500;

uint32_t lastRpmCalcMs1 = 0;
uint32_t hallPulseCount1 = 0;
float currentRpm1 = 0.0f;
int lastHallState1 = -1;

uint32_t lastRpmCalcMs2 = 0;
uint32_t hallPulseCount2 = 0;
float currentRpm2 = 0.0f;
int lastHallState2 = -1;

constexpr int ACTUATOR_STOP = 0;
constexpr int ACTUATOR_FORWARD = 1;
constexpr int ACTUATOR_REVERSE = -1;

// Note for how to use the MD20A PWM/DIR actuator (THIS IS FOR PROG TEAM):
//   PWM LOW  = brake/stop, DIR ignored
//   PWM HIGH + DIR LOW  = one motor direction
//   PWM HIGH + DIR HIGH = opposite motor direction
constexpr int LIFT_UP_DIRECTION = ACTUATOR_FORWARD;
constexpr int LIFT_DOWN_DIRECTION = ACTUATOR_REVERSE;
// Tilt direction was inverted in software so D-pad Up/Down matches physical tilt motion.
constexpr int TILT_UP_DIRECTION = ACTUATOR_REVERSE;
constexpr int TILT_DOWN_DIRECTION = ACTUATOR_FORWARD;

// D-pad actuator commands (THIS IS FOR PROG TEAM):
//   N/Up    -> lift box up
//   S/Down  -> bring box down
//   E/Right -> tilt actuator up
//   W/Left  -> tilt actuator down
constexpr uint8_t MD20A_FORWARD_DIR_LEVEL = LOW;
constexpr uint8_t MD20A_REVERSE_DIR_LEVEL = HIGH;
constexpr uint8_t MD20A_IDLE_DIR_LEVEL = LOW;

int escStopPulse() {
  return BIDIRECTIONAL_ESC ? escPulseNeutral : escPulseMin;
}

int axisToPulse(byte axisValue) {
  if (BIDIRECTIONAL_ESC) {
    if (abs(static_cast<int>(axisValue) - joystickCenter) <= joystickDeadband) {
      return escPulseNeutral;
    }
    return map(axisValue, 0, 255, escPulseMin, escPulseMax);
  }

  int deflection = abs(static_cast<int>(axisValue) - joystickCenter);
  if (deflection <= joystickDeadband) {
    return escPulseMin;
  }

  int maxDeflection = max(joystickCenter, static_cast<byte>(255 - joystickCenter));
  return map(deflection, joystickDeadband, maxDeflection, escPulseMin, escPulseMax);
}

void driveActuator(int pwmPin, int dirPin, int direction) {
  if (direction == ACTUATOR_STOP) {
    digitalWrite(dirPin, MD20A_IDLE_DIR_LEVEL);
    digitalWrite(pwmPin, LOW);
    return;
  }

  uint8_t dirLevel = direction == ACTUATOR_REVERSE ? MD20A_REVERSE_DIR_LEVEL : MD20A_FORWARD_DIR_LEVEL;
  digitalWrite(dirPin, dirLevel);
  digitalWrite(pwmPin, HIGH);
}

int directionFromButtons(bool forwardPressed, bool reversePressed, int forwardDirection, int reverseDirection) {
  if (forwardPressed == reversePressed) {
    return ACTUATOR_STOP;
  }
  return forwardPressed ? forwardDirection : reverseDirection;
}

bool isValidHallState(int state) {
  return state >= 1 && state <= 6;
}

int readHallState1() {
  return (digitalRead(HALL1_A) << 2) |
         (digitalRead(HALL1_B) << 1) |
         digitalRead(HALL1_C);
}

int readHallState2() {
  return (digitalRead(HALL2_A) << 2) |
         (digitalRead(HALL2_B) << 1) |
         digitalRead(HALL2_C);
}

void updateRpm1() {
  const int state = readHallState1();
  if (isValidHallState(state)) {
    if (lastHallState1 == -1) {
      lastHallState1 = state;
    } else if (state != lastHallState1) {
      hallPulseCount1++;
      lastHallState1 = state;
    }
  }

  const uint32_t now = millis();
  if (now - lastRpmCalcMs1 >= RPM_UPDATE_INTERVAL_MS) {
    const float elapsedSeconds = (now - lastRpmCalcMs1) / 1000.0f;
    currentRpm1 = (hallPulseCount1 / static_cast<float>(HALL_TRANSITIONS_PER_REV)) *
                  (60.0f / elapsedSeconds);
    hallPulseCount1 = 0;
    lastRpmCalcMs1 = now;
  }
}

void updateRpm2() {
  const int state = readHallState2();
  if (isValidHallState(state)) {
    if (lastHallState2 == -1) {
      lastHallState2 = state;
    } else if (state != lastHallState2) {
      hallPulseCount2++;
      lastHallState2 = state;
    }
  }

  const uint32_t now = millis();
  if (now - lastRpmCalcMs2 >= RPM_UPDATE_INTERVAL_MS) {
    const float elapsedSeconds = (now - lastRpmCalcMs2) / 1000.0f;
    currentRpm2 = (hallPulseCount2 / static_cast<float>(HALL_TRANSITIONS_PER_REV)) *
                  (60.0f / elapsedSeconds);
    hallPulseCount2 = 0;
    lastRpmCalcMs2 = now;
  }
}

void printHallSensorStates() {
  const int hallState1 = readHallState1();
  const int hallState2 = readHallState2();

  Serial.print("Hall1: ");
  Serial.print((hallState1 >> 2) & 0x01);
  Serial.print(" ");
  Serial.print((hallState1 >> 1) & 0x01);
  Serial.print(" ");
  Serial.print(hallState1 & 0x01);
  Serial.print("  RPM1: ");
  Serial.print(currentRpm1, 1);

  Serial.print("  Hall2: ");
  Serial.print((hallState2 >> 2) & 0x01);
  Serial.print(" ");
  Serial.print((hallState2 >> 1) & 0x01);
  Serial.print(" ");
  Serial.print(hallState2 & 0x01);
  Serial.print("  RPM2: ");
  Serial.print(currentRpm2, 1);
}

void printHallTelemetryIfEnabled() {
  if (!HALL_TELEMETRY_SERIAL) {
    return;
  }

  const unsigned long now = millis();
  if (now - lastHallTelemetryPrintMs < HALL_TELEMETRY_INTERVAL_MS) {
    return;
  }

  lastHallTelemetryPrintMs = now;
  printHallSensorStates();
  Serial.println();
}

void setup() {
  // pinMode(button1,INPUT);
  // pinMode(button2,INPUT);
  // pinMode(button3,INPUT);
  // pinMode(button4,INPUT);
  Serial.begin(9600);
  pinMode(HALL1_A, INPUT_PULLUP);
  pinMode(HALL1_B, INPUT_PULLUP);
  pinMode(HALL1_C, INPUT_PULLUP);
  pinMode(HALL2_A, INPUT_PULLUP);
  pinMode(HALL2_B, INPUT_PULLUP);
  pinMode(HALL2_C, INPUT_PULLUP);
  pinMode(liftPwmPin, OUTPUT);
  pinMode(tiltPwmPin, OUTPUT);
  pinMode(liftDirPin, OUTPUT);
  pinMode(tiltDirPin, OUTPUT);
  pinMode(vibrationPin, OUTPUT);

  esc1.attach(escPin1, escPulseMin, escPulseMax);
  esc2.attach(escPin2, escPulseMin, escPulseMax);

  // ---- ARM ESC ----
  if (DEBUG_SERIAL) {
    Serial.println("Arming ESC...");
  }
  esc1.writeMicroseconds(escStopPulse());
  esc2.writeMicroseconds(escStopPulse());
  delay(2000);
  lastRpmCalcMs1 = millis();
  lastRpmCalcMs2 = millis();
  if (DEBUG_SERIAL) {
    Serial.println("ESC Ready");
  }
}

void loop() {
  updateRpm1();
  updateRpm2();
  printHallTelemetryIfEnabled();

  if (Serial.available() >= PACKET_TOTAL_SIZE && controllerPacket.update() > 0) {
    int leftPulse = axisToPulse(controllerPacket.joy_left_y);
    int rightPulse = axisToPulse(controllerPacket.joy_right_y);
    esc1.writeMicroseconds(leftPulse);
    esc2.writeMicroseconds(rightPulse);

    // Controls were swapped so Left/Right now drive the lift actuator.
    int liftDirection = directionFromButtons(
      controllerPacket.dPadRight(),
      controllerPacket.dPadLeft(),
      LIFT_UP_DIRECTION,
      LIFT_DOWN_DIRECTION
    );
    // Controls were swapped so Up/Down now drive the tilt actuator.
    int tiltDirection = directionFromButtons(
      controllerPacket.dPadUp(),
      controllerPacket.dPadDown(),
      TILT_UP_DIRECTION,
      TILT_DOWN_DIRECTION
    );

    driveActuator(liftPwmPin, liftDirPin, liftDirection);
    driveActuator(tiltPwmPin, tiltDirPin, tiltDirection);

    //Adding toggle logic for vibration motor on Y button press.
    bool currentYButton = controllerPacket.getYButton();
    if (currentYButton && !lastYButtonState) {
      vibrationOn = !vibrationOn;
      if (vibrationOn) {
        // Changed the value into a moderate intensity for the vibration motor. Adjust as needed.
        analogWrite(vibrationPin, 62);
      } else {
        analogWrite(vibrationPin, 0);
      }
      Serial.println(vibrationOn ? 1 : 0);
    }
    lastYButtonState = currentYButton;

    if (DEBUG_SERIAL && millis() - lastDebugPrintMs >= DEBUG_INTERVAL_MS) {
      lastDebugPrintMs = millis();
      Serial.print("LeftPulse: ");
      Serial.print(leftPulse);
      Serial.print("  RightPulse: ");
      Serial.print(rightPulse);
      Serial.print("  LT: ");
      Serial.print(controllerPacket.trigger_left);
      Serial.print("  RT: ");
      Serial.print(controllerPacket.trigger_right);
      Serial.print("  DPad(U,D,L,R): ");
      Serial.print(controllerPacket.dPadUp());
      Serial.print(",");
      Serial.print(controllerPacket.dPadDown());
      Serial.print(",");
      Serial.print(controllerPacket.dPadLeft());
      Serial.print(",");
      Serial.println(controllerPacket.dPadRight());
    }
  }
}
