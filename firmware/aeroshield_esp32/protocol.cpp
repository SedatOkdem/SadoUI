#include "protocol.h"

namespace proto {

uint8_t xorChecksum(const uint8_t* data, size_t len) {
  uint8_t c = 0;
  for (size_t i = 0; i < len; ++i) {
    c ^= data[i];
  }
  return c;
}

int16_t decodeI16(uint8_t hi, uint8_t lo) {
  uint16_t raw = (static_cast<uint16_t>(hi) << 8) | lo;
  if (raw >= 32768) {
    return static_cast<int16_t>(static_cast<int32_t>(raw) - 65536);
  }
  return static_cast<int16_t>(raw);
}

namespace {

enum ParseState : uint8_t {
  WAIT_SOF,
  READ_BODY,
};

ParseState state = WAIT_SOF;
uint8_t buf[CMD_LEN];
size_t idx = 0;

uint16_t clampEscUs(uint16_t us) {
  if (us < ESC_MIN_US) {
    return ESC_MIN_US;
  }
  if (us > ESC_CAP_US) {
    return ESC_CAP_US;
  }
  return us;
}

}  // namespace

bool parseCommandByte(uint8_t byte, CommandPacket& out, bool& complete) {
  complete = false;

  if (state == WAIT_SOF) {
    if (byte == SOF) {
      buf[0] = byte;
      idx = 1;
      state = READ_BODY;
    }
    return false;
  }

  buf[idx++] = byte;
  if (idx < CMD_LEN) {
    return false;
  }

  state = WAIT_SOF;
  idx = 0;

  if (buf[CMD_LEN - 1] != EOF_MARK) {
    return false;
  }

  // MOD..SPIN_CS = 11 bytes starting at buf[1]
  const uint8_t checksum = xorChecksum(buf + 1, 11);
  if (checksum != buf[12]) {
    return false;
  }

  out.mod = buf[1];
  out.pan = decodeI16(buf[2], buf[3]);
  out.tilt = decodeI16(buf[4], buf[5]);
  out.fire = (buf[6] != 0);
  out.escIdleUs = clampEscUs(static_cast<uint16_t>(decodeI16(buf[7], buf[8])));
  out.escFireUs = clampEscUs(static_cast<uint16_t>(decodeI16(buf[9], buf[10])));
  if (out.escFireUs < out.escIdleUs) {
    out.escFireUs = out.escIdleUs;
  }
  out.fireSpinMs = static_cast<uint16_t>(buf[11]) * 10u;
  if (out.fireSpinMs < 50u) {
    out.fireSpinMs = 50u;
  }
  complete = true;
  return true;
}

void buildStatusFrame(const StatusPacket& pkt, uint8_t* out6) {
  const uint8_t payload[3] = {pkt.status, pkt.limitPan, pkt.limitTilt};
  out6[0] = SOF;
  out6[1] = payload[0];
  out6[2] = payload[1];
  out6[3] = payload[2];
  out6[4] = xorChecksum(payload, 3);
  out6[5] = EOF_MARK;
}

}  // namespace proto
