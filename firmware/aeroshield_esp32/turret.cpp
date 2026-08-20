#include "turret.h"

void Turret::begin() {
  _limitPan = false;
  _limitTilt = false;
  _homing = false;
  _lastPanTarget = _degToPanSteps(HOME_PAN_DEG);
  _lastTiltTarget = _degToTiltSteps(HOME_TILT_DEG);

#if USE_LIMIT_SWITCHES
  pinMode(PIN_LIMIT_PAN_MIN, INPUT_PULLUP);
  pinMode(PIN_LIMIT_PAN_MAX, INPUT_PULLUP);
  pinMode(PIN_LIMIT_TILT_MIN, INPUT_PULLUP);
  pinMode(PIN_LIMIT_TILT_MAX, INPUT_PULLUP);
#endif

#if SIMULATION
  _simPanDeg = HOME_PAN_DEG;
  _simTiltDeg = HOME_TILT_DEG;
  _targetPanDeg = _simPanDeg;
  _targetTiltDeg = _simTiltDeg;
#else
  _pan = NULL;
  _tilt = NULL;

  _pan = new AccelStepper(AccelStepper::DRIVER, PAN_STEP_GPIO, PAN_DIR_GPIO);
  _tilt = new AccelStepper(AccelStepper::DRIVER, TILT_STEP_GPIO, TILT_DIR_GPIO);
  _setupMotor(_pan, PAN_STEP_GPIO, PAN_DIR_GPIO, PIN_PAN_EN, INVERT_PAN_DIR);
  _setupMotor(_tilt, TILT_STEP_GPIO, TILT_DIR_GPIO, PIN_TILT_EN, INVERT_TILT_DIR);
  _tilt->setMinPulseWidth(TILT_STEP_PULSE_US);
  _tilt->setMaxSpeed(TILT_MAX_SPEED_STEPS_S);
  _tilt->setAcceleration(TILT_MAX_ACCEL_STEPS_S2);
  _pan->setCurrentPosition(_lastPanTarget);
  _tilt->setCurrentPosition(_lastTiltTarget);
  _applyEnable(true);
#endif
  _launcher.begin();
}

void Turret::_setupMotor(AccelStepper* m, int stepPin, int dirPin, int enPin, bool invertDir) {
  pinMode(stepPin, OUTPUT);
  pinMode(dirPin, OUTPUT);
  pinMode(enPin, OUTPUT);
  m->setMinPulseWidth(STEP_PULSE_US);
  m->setMaxSpeed(MAX_SPEED_STEPS_S);
  m->setAcceleration(MAX_ACCEL_STEPS_S2);
  m->setEnablePin(enPin);
  m->setPinsInverted(invertDir, false, STEPPER_ENABLE_ACTIVE_LOW);
  m->enableOutputs();
}

void Turret::_applyEnable(bool enable) {
#if !SIMULATION
  if (_pan) {
    if (enable) {
      _pan->enableOutputs();
    } else {
      _pan->disableOutputs();
    }
  }
  if (_tilt) {
    if (enable) {
      _tilt->enableOutputs();
    } else {
      _tilt->disableOutputs();
    }
  }
#endif
}

long Turret::_degToPanSteps(float deg) const {
  return lroundf(deg * STEPS_PER_DEG_PAN);
}

long Turret::_degToTiltSteps(float deg) const {
  return lroundf(deg * STEPS_PER_DEG_TILT);
}

float Turret::_panStepsToDeg(long steps) const {
  return static_cast<float>(steps) / STEPS_PER_DEG_PAN;
}

float Turret::_tiltStepsToDeg(long steps) const {
  return static_cast<float>(steps) / STEPS_PER_DEG_TILT;
}

void Turret::_readLimits() {
#if USE_LIMIT_SWITCHES
  _limitPan = (digitalRead(PIN_LIMIT_PAN_MIN) == LOW) || (digitalRead(PIN_LIMIT_PAN_MAX) == LOW);
  _limitTilt = (digitalRead(PIN_LIMIT_TILT_MIN) == LOW) || (digitalRead(PIN_LIMIT_TILT_MAX) == LOW);
#else
  _limitPan = false;
  _limitTilt = false;
#endif
}

void Turret::setTargets(int16_t panDeg, int16_t tiltDeg) {
  int16_t pan = panDeg;
  int16_t tilt = tiltDeg;
  if (pan < PAN_MIN_DEG) pan = PAN_MIN_DEG;
  if (pan > PAN_MAX_DEG) pan = PAN_MAX_DEG;
  if (tilt < TILT_MIN_DEG) tilt = TILT_MIN_DEG;
  if (tilt > TILT_MAX_DEG) tilt = TILT_MAX_DEG;

#if SIMULATION
  _targetPanDeg = static_cast<float>(pan);
  _targetTiltDeg = static_cast<float>(tilt);
#else
  if (!_pan || !_tilt) {
    return;
  }
  const long panSteps = _degToPanSteps(pan);
  const long tiltSteps = _degToTiltSteps(tilt);
  if (abs(panSteps - _lastPanTarget) >= TARGET_DEADBAND_STEPS) {
    _lastPanTarget = panSteps;
    _pan->moveTo(panSteps);
  }
  const long tiltErr = tiltSteps - _lastTiltTarget;
  if (abs(tiltErr) >= TILT_MIN_CMD_STEPS) {
    const long going = _tilt->distanceToGo();
    const bool idle = (going == 0);
    const bool sameWay = (tiltErr > 0 && going > 0) || (tiltErr < 0 && going < 0);
    if (idle || sameWay) {
      _lastTiltTarget = tiltSteps;
      _tilt->moveTo(tiltSteps);
    }
  }
#endif
}

void Turret::stopMotion() {
#if SIMULATION
  _targetPanDeg = _simPanDeg;
  _targetTiltDeg = _simTiltDeg;
#else
  if (_pan && _tilt) {
    _pan->stop();
    _tilt->stop();
    _lastPanTarget = _pan->targetPosition();
    _lastTiltTarget = _tilt->targetPosition();
  }
#endif
  setLauncherStopped(true);
}

void Turret::startHoming() {
  _homing = true;
  setTargets(HOME_PAN_DEG, HOME_TILT_DEG);
}

void Turret::_runHoming() {
  if (!isMoving()) {
    _homing = false;
  }
}

void Turret::setFirePulse(bool active) {
  _launcher.setFire(active);
}

void Turret::setLauncherStopped(bool stopped) {
  _launcher.setStopped(stopped);
}

void Turret::setLauncherSignal(int idleUs, int fireUs, uint16_t fireSpinMs) {
  _launcher.setSignalUs(idleUs, fireUs);
  _launcher.setFireSpinMs(fireSpinMs);
}

static void _blockingSteps(int stepPin, int dirPin, int steps, int dirLevel) {
  pinMode(stepPin, OUTPUT);
  pinMode(dirPin, OUTPUT);
  digitalWrite(dirPin, dirLevel ? HIGH : LOW);
  delayMicroseconds(20);
  const int n = (steps < 0) ? -steps : steps;
  for (int i = 0; i < n; ++i) {
    digitalWrite(stepPin, HIGH);
    delayMicroseconds(STEP_PULSE_US);
    digitalWrite(stepPin, LOW);
    delayMicroseconds(600);
  }
}

void Turret::selfTest() {
#if !SIMULATION && MOTOR_SELFTEST
  _applyEnable(true);
  _blockingSteps(PAN_STEP_GPIO, PAN_DIR_GPIO, SELFTEST_STEPS, 1);
  delay(150);
  _blockingSteps(PAN_STEP_GPIO, PAN_DIR_GPIO, SELFTEST_STEPS, 0);
  delay(150);
  _blockingSteps(TILT_STEP_GPIO, TILT_DIR_GPIO, SELFTEST_STEPS, 1);
  delay(150);
  _blockingSteps(TILT_STEP_GPIO, TILT_DIR_GPIO, SELFTEST_STEPS, 0);
  if (_pan) {
    _pan->setCurrentPosition(_degToPanSteps(HOME_PAN_DEG));
  }
  if (_tilt) {
    _tilt->setCurrentPosition(_degToTiltSteps(HOME_TILT_DEG));
  }
#endif
}

bool Turret::isMoving() const {
#if SIMULATION
  return (_simPanDeg != _targetPanDeg) || (_simTiltDeg != _targetTiltDeg);
#else
  if (!_pan || !_tilt) {
    return false;
  }
  return (_pan->distanceToGo() != 0) || (_tilt->distanceToGo() != 0);
#endif
}

int16_t Turret::panDeg() const {
#if SIMULATION
  return static_cast<int16_t>(lroundf(_simPanDeg));
#else
  if (!_pan) {
    return 0;
  }
  return static_cast<int16_t>(lroundf(_panStepsToDeg(_pan->currentPosition())));
#endif
}

int16_t Turret::tiltDeg() const {
#if SIMULATION
  return static_cast<int16_t>(lroundf(_simTiltDeg));
#else
  if (!_tilt) {
    return 0;
  }
  return static_cast<int16_t>(lroundf(_tiltStepsToDeg(_tilt->currentPosition())));
#endif
}

void Turret::update() {
  _readLimits();
  _launcher.update();

#if SIMULATION
  const float rate = 180.0f / static_cast<float>(STATUS_RATE_HZ);
  if (_simPanDeg < _targetPanDeg) {
    _simPanDeg = (_simPanDeg + rate > _targetPanDeg) ? _targetPanDeg : (_simPanDeg + rate);
  } else if (_simPanDeg > _targetPanDeg) {
    _simPanDeg = (_simPanDeg - rate < _targetPanDeg) ? _targetPanDeg : (_simPanDeg - rate);
  }
  if (_simTiltDeg < _targetTiltDeg) {
    _simTiltDeg = (_simTiltDeg + rate > _targetTiltDeg) ? _targetTiltDeg : (_simTiltDeg + rate);
  } else if (_simTiltDeg > _targetTiltDeg) {
    _simTiltDeg = (_simTiltDeg - rate < _targetTiltDeg) ? _targetTiltDeg : (_simTiltDeg - rate);
  }
#else
  if (_pan && _tilt) {
    const bool tiltMove = _tilt->distanceToGo() != 0;
    if (!tiltMove) {
      _tilt->setSpeed(0.0f);
    }
    for (int i = 0; i < 16; ++i) {
      if (_limitPan) {
        _pan->stop();
      } else {
        _pan->run();
      }
      if (tiltMove && !_limitTilt) {
        _tilt->run();
      }
    }
  }
#endif

  if (_homing) {
    _runHoming();
  }
}
