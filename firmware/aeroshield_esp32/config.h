#pragma once

// ── Pin map (ESP32 DevKit) ────────────────────────────────────────────────
#define PIN_PAN_STEP   18
#define PIN_PAN_DIR    19
#define PIN_PAN_EN     23
#define PIN_TILT_STEP  21
#define PIN_TILT_DIR   22
#define PIN_TILT_EN    5

// 1 = STEP ve DIR kabloları ters (motor hiç dönmez / sadece titrer)
#define SWAP_PAN_STEP_DIR   1
#define SWAP_TILT_STEP_DIR  1

#if SWAP_PAN_STEP_DIR
#define PAN_STEP_GPIO  PIN_PAN_DIR
#define PAN_DIR_GPIO   PIN_PAN_STEP
#else
#define PAN_STEP_GPIO  PIN_PAN_STEP
#define PAN_DIR_GPIO   PIN_PAN_DIR
#endif

#if SWAP_TILT_STEP_DIR
#define TILT_STEP_GPIO PIN_TILT_DIR
#define TILT_DIR_GPIO  PIN_TILT_STEP
#else
#define TILT_STEP_GPIO PIN_TILT_STEP
#define TILT_DIR_GPIO  PIN_TILT_DIR
#endif

#define PIN_LIMIT_PAN_MIN  32
#define PIN_LIMIT_PAN_MAX  33
#define PIN_LIMIT_TILT_MIN 25
#define PIN_LIMIT_TILT_MAX 26

#define PIN_ESTOP      13
#define PIN_LED        2

// RS2205 × 2 flywheel (ESC sinyal, ortak GND). 3.3 V PWM; ESC kabul etmezse level-shift.
#define PIN_ESC_L      27
#define PIN_ESC_R      14
#define ESC_LEDC_CH_L  0
#define ESC_LEDC_CH_R  1
#define ESC_PWM_HZ     50
#define ESC_PWM_BITS   16
#define ESC_PERIOD_US  20000
#define ESC_STOP_US    1000
#define ESC_IDLE_US    1060   // yavaş dönüş (silah hazır)
#define ESC_FIRE_US    1200   // varsayılan ateş; panelden değiştirilir
#define ESC_MAX_US     2000   // şimdilik soft limit yok (tam ESC aralığı)
#define ESC_RAMP_US    4
#define FIRE_SPIN_MS   450

#define PIN_FIRE       PIN_ESC_L  // eski isim / tek pin test

#define USE_HW_ESTOP         0
#define IGNORE_CMD_ESTOP     1
#define USE_LIMIT_SWITCHES   0
#define STEPPER_ENABLE_ACTIVE_LOW  1

// Motor direction (1 = reverse that axis)
#define INVERT_PAN_DIR   0
#define INVERT_TILT_DIR  0

// Calibrate: steps for 90° / 90
#define STEPS_PER_DEG_PAN   40.0f
#define STEPS_PER_DEG_TILT  40.0f

#define PAN_MIN_DEG   0
#define PAN_MAX_DEG   270
#define TILT_MIN_DEG  -30
#define TILT_MAX_DEG  60

#define HOME_PAN_DEG  135  // ileri bakış = orta; 0 ve 270 karşı yönler
#define HOME_TILT_DEG 0

#define MAX_SPEED_STEPS_S        2000.0f
#define MAX_ACCEL_STEPS_S2       1600.0f
#define STEP_PULSE_US            8
#define TARGET_DEADBAND_STEPS    3
#define TILT_MAX_SPEED_STEPS_S   1000.0f
#define TILT_MAX_ACCEL_STEPS_S2  700.0f
#define TILT_STEP_PULSE_US       10
#define TILT_MIN_CMD_STEPS       24

#define FAILSAFE_MS        1500
#define FIRE_PULSE_MS      FIRE_SPIN_MS
#define STATUS_RATE_HZ     40
#define SERIAL_BAUD        115200

#define MOTOR_SELFTEST     0
#define SELFTEST_STEPS     400

#ifndef SIMULATION
#define SIMULATION 0
#endif
