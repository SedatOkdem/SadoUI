#pragma once

#include <Arduino.h>
#include "config.h"

// Two RS2205 via standard ESC PWM (1000–2000 µs). L=R same command.
class Launcher {
 public:
  void begin();
  void update();
  void setFire(bool active);
  void setStopped(bool stopped);  // E-STOP / failsafe → 1000 µs
  void setSignalUs(int idleUs, int fireUs);
  void setFireSpinMs(uint16_t ms);
  bool isFiring() const { return _firing; }

 private:
  void _writeUs(int us);
  void _attachPwm();
  int _clamped(int us) const;

  bool _stopped;
  bool _firing;
  int _currentUs;
  int _targetUs;
  int _idleUs;
  int _fireUs;
  uint16_t _fireSpinMs;
  uint32_t _fireUntilMs;
  uint32_t _lastRampMs;
};
