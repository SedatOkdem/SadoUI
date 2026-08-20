#include "launcher.h"

static uint32_t dutyFromUs(int us) {
  if (us < 0) {
    us = 0;
  }
  const uint32_t maxDuty = (1u << ESC_PWM_BITS) - 1u;
  return (static_cast<uint32_t>(us) * maxDuty) / static_cast<uint32_t>(ESC_PERIOD_US);
}

int Launcher::_clamped(int us) const {
  if (us < ESC_STOP_US) {
    return ESC_STOP_US;
  }
  if (us > ESC_MAX_US) {
    return ESC_MAX_US;
  }
  return us;
}

void Launcher::_attachPwm() {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
  ledcAttach(PIN_ESC_L, ESC_PWM_HZ, ESC_PWM_BITS);
  ledcAttach(PIN_ESC_R, ESC_PWM_HZ, ESC_PWM_BITS);
#else
  ledcSetup(ESC_LEDC_CH_L, ESC_PWM_HZ, ESC_PWM_BITS);
  ledcSetup(ESC_LEDC_CH_R, ESC_PWM_HZ, ESC_PWM_BITS);
  ledcAttachPin(PIN_ESC_L, ESC_LEDC_CH_L);
  ledcAttachPin(PIN_ESC_R, ESC_LEDC_CH_R);
#endif
}

void Launcher::_writeUs(int us) {
  us = _clamped(us);
  _currentUs = us;
  // Ateş / idle: sol ve sağ ESC her zaman aynı PWM (flywheel sıkıştırma).
  const uint32_t duty = dutyFromUs(us);
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
  ledcWrite(PIN_ESC_L, duty);
  ledcWrite(PIN_ESC_R, duty);
#else
  ledcWrite(ESC_LEDC_CH_L, duty);
  ledcWrite(ESC_LEDC_CH_R, duty);
#endif
}

void Launcher::begin() {
  _stopped = false;
  _firing = false;
  _fireUntilMs = 0;
  _lastRampMs = millis();
  _idleUs = ESC_IDLE_US;
  _fireUs = ESC_FIRE_US;
  _fireSpinMs = FIRE_SPIN_MS;
  _targetUs = _idleUs;
  _attachPwm();
  _writeUs(ESC_STOP_US);
  delay(1500);
  _writeUs(_idleUs);
}

void Launcher::setSignalUs(int idleUs, int fireUs) {
  _idleUs = _clamped(idleUs);
  _fireUs = _clamped(fireUs);
  if (_fireUs < _idleUs) {
    _fireUs = _idleUs;
  }
  if (_stopped) {
    return;
  }
  if (_firing) {
    _targetUs = _fireUs;
  } else {
    _targetUs = _idleUs;
  }
}

void Launcher::setFireSpinMs(uint16_t ms) {
  if (ms < 50) {
    ms = 50;
  }
  if (ms > 2550) {
    ms = 2550;
  }
  _fireSpinMs = ms;
}

void Launcher::setStopped(bool stopped) {
  _stopped = stopped;
  if (stopped) {
    _firing = false;
    _fireUntilMs = 0;
    _targetUs = ESC_STOP_US;
    _writeUs(ESC_STOP_US);
  } else if (!_firing) {
    _targetUs = _idleUs;
  }
}

void Launcher::setFire(bool active) {
  if (_stopped) {
    return;
  }
  if (active) {
    _firing = true;
    _fireUntilMs = millis() + _fireSpinMs;
    _targetUs = _fireUs;
  } else {
    _firing = false;
    _fireUntilMs = 0;
    _targetUs = _idleUs;
  }
}

void Launcher::update() {
  if (_stopped) {
    if (_currentUs != ESC_STOP_US) {
      _writeUs(ESC_STOP_US);
    }
    return;
  }

  if (_firing && millis() >= _fireUntilMs) {
    _firing = false;
    _targetUs = _idleUs;
  }

  const uint32_t now = millis();
  if (now - _lastRampMs < 10) {
    return;
  }
  _lastRampMs = now;

  int next = _currentUs;
  if (next < _targetUs) {
    next += ESC_RAMP_US;
    if (next > _targetUs) {
      next = _targetUs;
    }
  } else if (next > _targetUs) {
    next -= ESC_RAMP_US;
    if (next < _targetUs) {
      next = _targetUs;
    }
  }
  if (next != _currentUs) {
    _writeUs(next);
  } else if (_firing || _targetUs != ESC_STOP_US) {
    // Aynı duty'yi yeniden bas — L/R drift olmasın.
    _writeUs(_currentUs);
  }
}
