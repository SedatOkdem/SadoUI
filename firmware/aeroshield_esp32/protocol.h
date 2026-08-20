#pragma once

#include <Arduino.h>

namespace proto {

constexpr uint8_t SOF = 0xAA;
constexpr uint8_t EOF_MARK = 0xFF;
// [AA][MOD][PAN_H][PAN_L][TILT_H][TILT_L][FIRE]
// [IDLE_H][IDLE_L][FIRE_H][FIRE_L][SPIN_CS][CHK][FF]
constexpr size_t CMD_LEN = 14;
constexpr size_t STATUS_LEN = 6;
constexpr int ESC_MIN_US = 1000;
constexpr int ESC_CAP_US = 2000;  // no soft 1200 cap for now

enum ModCode : uint8_t {
  MOD_MANUAL = 0,
  MOD_SEMI   = 1,
  MOD_AUTO   = 2,
  MOD_ESTOP  = 3,
  MOD_HOME   = 4,
};

enum StatusCode : uint8_t {
  ST_OK        = 0,
  ST_LIMIT     = 1,
  ST_FAILSAFE  = 2,
  ST_ESTOP     = 3,
  ST_HOME      = 4,
  ST_BUSY      = 5,
};

struct CommandPacket {
  uint8_t mod;
  int16_t pan;
  int16_t tilt;
  bool fire;
  uint16_t escIdleUs;
  uint16_t escFireUs;
  uint16_t fireSpinMs;
};

struct StatusPacket {
  uint8_t status;
  uint8_t limitPan;
  uint8_t limitTilt;
};

uint8_t xorChecksum(const uint8_t* data, size_t len);
int16_t decodeI16(uint8_t hi, uint8_t lo);
bool parseCommandByte(uint8_t byte, CommandPacket& out, bool& complete);
void buildStatusFrame(const StatusPacket& pkt, uint8_t* out6);

}  // namespace proto
