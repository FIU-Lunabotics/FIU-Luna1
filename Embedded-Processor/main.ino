#include <Servo.h>

Servo esc1;
Servo esc2;

constexpr byte START_BYTE = 0b10101000;
constexpr byte END_BYTE = 0b00010101;
constexpr byte start_byte_bm = 0b11111111;
constexpr byte end_byte_bm = 0b11111111;

constexpr uint8_t PACKET_SIZE = 5;        // start + 4 data bytes
constexpr uint8_t PACKET_TOTAL_SIZE = 6;  // start + 4 data bytes + end
constexpr size_t BUFFER_SIZE = 64;

struct ControlPacket {
  byte start_byte = 0;
  byte joy_left_x = 0;
  byte joy_left_y = 0;
  byte joy_right_y = 0;
  byte trigger = 0;
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
    this->joy_left_x = tempBarr[1];
    this->joy_left_y = tempBarr[2];
    this->joy_right_y = tempBarr[3];
    this->trigger = tempBarr[4];
    this->end_byte = tempBarr[5];

    return result;
  }
};

ControlPacket controllerPacket;

int escPin1 = 9;
int escPin2 = 10;
int linAcc1 = 11;
int linAcc2 = 12;
constexpr uint8_t HALL1_A = 22;
constexpr uint8_t HALL1_B = 24;
constexpr uint8_t HALL1_C = 26;
constexpr uint8_t HALL2_A = 28;
constexpr uint8_t HALL2_B = 30;
constexpr uint8_t HALL2_C = 32;
// int button1 = 2; removed these lines to establish 
// int button2 = 3;
// int button3 = 4;
// int button4 = 5;
int dir1 = 6;
int dir2 = 7;
// int potPin = A0;

int throttleMin = 0;   // ESC minimum throttle
int throttleMax = 2000;   // ESC maximum throttle

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

  Serial.print("  Hall1: ");
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
  pinMode(linAcc1, OUTPUT);
  pinMode(linAcc2, OUTPUT);
  pinMode(dir1, OUTPUT);
  pinMode(dir2, OUTPUT);

  esc1.attach(escPin1, throttleMin, throttleMax);
  esc2.attach(escPin2, throttleMin, throttleMax);

  // ---- ARM ESC ----
  Serial.println("Arming ESC...");
  esc1.writeMicroseconds(throttleMin);
  esc2.writeMicroseconds(throttleMin);
  delay(2000);  // most ESCs need 2 seconds of minimum throttle
  lastRpmCalcMs1 = millis();
  lastRpmCalcMs2 = millis();
  Serial.println("ESC Ready");
}

void loop() {
  updateRpm1();
  updateRpm2();

  if (Serial.available() >= PACKET_TOTAL_SIZE && controllerPacket.update() > 0) {
    int throttle = map(controllerPacket.joy_left_y, 0, 255, 1000, 2000);
    esc1.writeMicroseconds(throttle);
    esc2.writeMicroseconds(throttle);

    digitalWrite(linAcc1, controllerPacket.joy_right_y > 128 ? HIGH : LOW);
    digitalWrite(linAcc2, controllerPacket.joy_right_y > 128 ? HIGH : LOW);
    digitalWrite(dir1, controllerPacket.trigger > 128 ? HIGH : LOW);
    digitalWrite(dir2, controllerPacket.trigger > 128 ? HIGH : LOW);

    Serial.print("Throttle: ");
    Serial.print(throttle);
    Serial.print("  LJoyX: ");
    Serial.print(controllerPacket.joy_left_x);
    Serial.print("  RJoyY: ");
    Serial.print(controllerPacket.joy_right_y);
    Serial.print("  RT: ");
    Serial.print(controllerPacket.trigger);
    printHallSensorStates();
    Serial.println();
  }
}
