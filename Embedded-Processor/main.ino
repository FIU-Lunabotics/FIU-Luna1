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
    return (secondary_buttons & DYP_NEG_MASK) != 0;
  }

  bool dPadDown() const {
    return (secondary_buttons & DYP_POS_MASK) != 0;
  }
};

ControlPacket controllerPacket;

int escPin1 = 9;
int escPin2 = 10;
int linAcc1 = 11;
int linAcc2 = 12;
// int button1 = 2; removed these lines to establish 
// int button2 = 3;
// int button3 = 4;
// int button4 = 5;
int dir1 = 6;
int dir2 = 7;
// int potPin = A0;

constexpr bool BIDIRECTIONAL_ESC = true;
constexpr byte joystickCenter = 128;
constexpr byte joystickDeadband = 8;

int escPulseMin = 1000;
int escPulseNeutral = 1500;
int escPulseMax = 2000;

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

void setup() {
  // pinMode(button1,INPUT);
  // pinMode(button2,INPUT);
  // pinMode(button3,INPUT);
  // pinMode(button4,INPUT);
  Serial.begin(9600);
  pinMode(linAcc1, OUTPUT);
  pinMode(linAcc2, OUTPUT);
  pinMode(dir1, OUTPUT);
  pinMode(dir2, OUTPUT);

  esc1.attach(escPin1, escPulseMin, escPulseMax);
  esc2.attach(escPin2, escPulseMin, escPulseMax);

  // ---- ARM ESC ----
  Serial.println("Arming ESC...");
  esc1.writeMicroseconds(escStopPulse());
  esc2.writeMicroseconds(escStopPulse());
  delay(2000);
  Serial.println("ESC Ready");
}

void loop() {
  if (Serial.available() >= PACKET_TOTAL_SIZE && controllerPacket.update() > 0) {
    int leftPulse = axisToPulse(controllerPacket.joy_left_y);
    int rightPulse = axisToPulse(controllerPacket.joy_right_y);
    esc1.writeMicroseconds(leftPulse);
    esc2.writeMicroseconds(rightPulse);

    // Keep auxiliary outputs simple and deterministic until their final mapping
    // is locked in with the embedded pinout and actuator expectations.
    digitalWrite(linAcc1, controllerPacket.dPadUp() ? HIGH : LOW);
    digitalWrite(linAcc2, controllerPacket.dPadDown() ? HIGH : LOW);
    digitalWrite(dir1, controllerPacket.dPadRight() ? HIGH : LOW);
    digitalWrite(dir2, controllerPacket.dPadLeft() ? HIGH : LOW);

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
