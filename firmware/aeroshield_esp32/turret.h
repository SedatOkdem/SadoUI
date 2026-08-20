#pragma once

#include "config.h"
#include "protocol.h"
#include "launcher.h"

#if !SIMULATION
#include <AccelStepper.h>
#endif

class Turret {
 public:
  void begin();
  void update();

  void setTargets(int16_t panDeg, int16_t tiltDeg);
  void stopMotion();
  void startHoming();
  void selfTest();
  void setFirePulse(bool active);
  void setLauncherStopped(bool stopped);
  void setLauncherSignal(int idleUs, int fireUs, uint16_t fireSpinMs);

  bool isHoming() const { return _homing; }
  bool isMoving() const;
  int16_t panDeg() const;
  int16_t tiltDeg() const;

  bool limitPan() const { return _limitPan; }
  bool limitTilt() const { return _limitTilt; }

 private:
  long _degToPanSteps(float deg) const;
  long _degToTiltSteps(float deg) const;
  float _panStepsToDeg(long steps) const;
  float _tiltStepsToDeg(long steps) const;

  void _readLimits();
  void _runHoming();
  void _applyEnable(bool enable);
  void _setupMotor(class AccelStepper* m, int stepPin, int dirPin, int enPin, bool invertDir);

  bool _limitPan;
  bool _limitTilt;
  bool _homing;
  long _lastPanTarget;
  long _lastTiltTarget;
  Launcher _launcher;

#if SIMULATION
  float _simPanDeg;
  float _simTiltDeg;
  float _targetPanDeg;
  float _targetTiltDeg;
#else
  AccelStepper* _pan;
  AccelStepper* _tilt;
#endif
};
