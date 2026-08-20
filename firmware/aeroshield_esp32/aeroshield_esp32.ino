/**
 * AeroShield ESP32 taret — çalışan kablo düzeni (STEP/DIR yazılımdan swap).
 * Board: ESP32 Dev Module · 115200 · AccelStepper
 *
 * Fiziksel GPIO: Pan 18/19  Tilt 21/22  EN 23/5
 * RS2205 ESC sinyal: GPIO 27 + 14 (50 Hz, max 1200 µs; IDLE/ATEŞ panelden)
 * UART komut 14 bayt (IDLE/FIRE mutlak µs + SPIN_CS)
 * Pan ve tilt STEP/DIR swap=1. Tilt sürücü akımını düşük tut.
 */

#include <AccelStepper.h>

#include "config.h"
#include "protocol.h"
#include "turret.h"

Turret turret;

proto::CommandPacket lastCmd = {proto::MOD_MANUAL, 0, 0, false, ESC_IDLE_US, ESC_FIRE_US, FIRE_SPIN_MS};
uint32_t lastValidPacketMs = 0;
bool failsafeHeld = false;

void sendStatus(const proto::StatusPacket& pkt) {
  uint8_t frame[proto::STATUS_LEN];
  proto::buildStatusFrame(pkt, frame);
  Serial.write(frame, proto::STATUS_LEN);
}

bool readHardwareEstop() {
#if USE_HW_ESTOP
  return digitalRead(PIN_ESTOP) == LOW;
#else
  return false;
#endif
}

bool commandBlocked() {
#if IGNORE_CMD_ESTOP
  return readHardwareEstop();
#else
  return readHardwareEstop() || (lastCmd.mod == proto::MOD_ESTOP);
#endif
}

proto::StatusPacket buildCurrentStatus() {
  proto::StatusPacket st;
  st.limitPan = turret.limitPan() ? 1 : 0;
  st.limitTilt = turret.limitTilt() ? 1 : 0;

  const bool failsafe = (millis() - lastValidPacketMs) > FAILSAFE_MS;
  if (commandBlocked()) {
    st.status = proto::ST_ESTOP;
  } else if (failsafe) {
    st.status = proto::ST_FAILSAFE;
  } else if (turret.isHoming()) {
    st.status = proto::ST_HOME;
  } else if (st.limitPan || st.limitTilt) {
    st.status = proto::ST_LIMIT;
  } else {
    st.status = proto::ST_OK;
  }
  return st;
}

void applyCommand(const proto::CommandPacket& cmd) {
  lastCmd = cmd;
  lastValidPacketMs = millis();
  failsafeHeld = false;

  if (commandBlocked()) {
    turret.stopMotion();
    return;
  }

  turret.setLauncherStopped(false);
  turret.setLauncherSignal(cmd.escIdleUs, cmd.escFireUs, cmd.fireSpinMs);

  switch (cmd.mod) {
    case proto::MOD_HOME:
      turret.startHoming();
      break;
    case proto::MOD_MANUAL:
    case proto::MOD_SEMI:
    case proto::MOD_AUTO:
      turret.setTargets(cmd.pan, cmd.tilt);
      break;
    default:
      turret.setTargets(cmd.pan, cmd.tilt);
      break;
  }

  if (cmd.fire) {
    turret.setFirePulse(true);
  }
}

void setup() {
#if USE_HW_ESTOP
  pinMode(PIN_ESTOP, INPUT_PULLUP);
#endif
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_PAN_STEP, OUTPUT);
  pinMode(PIN_PAN_DIR, OUTPUT);
  pinMode(PIN_PAN_EN, OUTPUT);
  pinMode(PIN_TILT_STEP, OUTPUT);
  pinMode(PIN_TILT_DIR, OUTPUT);
  pinMode(PIN_TILT_EN, OUTPUT);
  digitalWrite(PIN_PAN_EN, LOW);
  digitalWrite(PIN_TILT_EN, LOW);

  Serial.begin(SERIAL_BAUD);
  delay(30);

  turret.begin();
  lastValidPacketMs = millis();
  digitalWrite(PIN_LED, HIGH);
}

void loop() {
  turret.update();

  int n = 0;
  while (Serial.available() > 0 && n < 48) {
    const uint8_t b = static_cast<uint8_t>(Serial.read());
    proto::CommandPacket cmd;
    bool complete = false;
    if (proto::parseCommandByte(b, cmd, complete) && complete) {
      applyCommand(cmd);
    }
    n++;
  }

  turret.update();

  const uint32_t now = millis();
  if ((now - lastValidPacketMs) > FAILSAFE_MS) {
    if (!failsafeHeld) {
      turret.stopMotion();
      failsafeHeld = true;
    }
  }

  static uint32_t lastStatusMs = 0;
  if ((now - lastStatusMs) >= (1000 / STATUS_RATE_HZ)) {
    lastStatusMs = now;
    const proto::StatusPacket st = buildCurrentStatus();
    sendStatus(st);
    const bool ok = (st.status == proto::ST_OK || st.status == proto::ST_HOME);
    const bool ledOn = turret.isMoving() ? (((now / 80) % 2) == 0) : ok;
    digitalWrite(PIN_LED, ledOn ? HIGH : LOW);
  }
}
