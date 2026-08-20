# AeroShield GCS

TEKNOFEST Çelikkubbe hava savunma sistemi için PyQt5 yer kontrol istasyonu (GCS).

## Özellikler

- USB kamera (OpenCV) + Ultralytics YOLO tespit/takip
- Multiprocessing görüntü hattı (kamera → inference → kontrol → UART)
- ESP32 UART protokolü (115200, XOR checksum) veya mock serial
- Aşama 1 / 2 / 3, E-Stop, yasak pan bölgesi, bakım süresi
- PID + Kalman + FSM (SEARCH → TRACK → LOCK → ENGAGE → BDA)

## Kurulum

```bash
cd d:\AeroShieldArayuz
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Çalıştırma

```bash
python main.py
```

Varsayılan `config.yaml` içinde `serial.mock: true` — ESP32 olmadan çalışır. Kamerasız ortamda sentetik görüntü üretilir.

### ESP32 bağlama

1. `config.yaml` → `serial.mock: false`
2. `serial.port: COMx` (arayüzden COM Tara da kullanılabilir)
3. Baud: **115200**
4. Firmware: Arduino IDE ile [`firmware/aeroshield_esp32/aeroshield_esp32.ino`](firmware/aeroshield_esp32/aeroshield_esp32.ino) dosyasını açıp yükleyin. Adımlar: [`firmware/aeroshield_esp32/README.md`](firmware/aeroshield_esp32/README.md).

Fiziksel E-Stop butonu yoksa `config.h` içinde `USE_HW_ESTOP 0` kalmalı (varsayılan). GPIO 34 pull-up’sızdır; boş pin E-STOP kilitlenmesi yapıyordu.

Komut paketi (PC → ESP32):

`[0xAA][MOD][PAN_H][PAN_L][TILT_H][TILT_L][FIRE][CHECKSUM][0xFF]`

`FIRE=1` → ESP iki RS2205 ESC’yi paneldeki ATEŞ µs’e rampeler, SÜRE bitince IDLE µs’e döner.
E-STOP / GCS kopması: 1000 µs (dur). Panel: **RS2205 · ESC SİNYAL** (IDLE / ATEŞ / SÜRE).
Sinyal pinleri: GPIO 27 ve 14. Varsayılan ateş **1200 µs**; panelden 1000–2000 arası değiştirilir (şimdilik 1200 soft limiti yok).

Komut paketi (14 bayt):

`[0xAA][MOD][PAN_H][PAN_L][TILT_H][TILT_L][FIRE][IDLE_H][IDLE_L][FIRE_H][FIRE_L][SPIN_CS][CHECKSUM][0xFF]`

`IDLE` / `FIRE` = mutlak µs (big-endian). `SPIN_CS` = ateş süresi / 10 ms.

Durum paketi (ESP32 → PC):

`[0xAA][STATUS][LIMIT_PAN][LIMIT_TILT][CHECKSUM][0xFF]`

### Model (Çelik Kubbe v8)

Roboflow zip (`Celik Kubbe.v8i.yolov11.zip`) veri setidir, `.pt` ağırlık değil. Sınıflar GCS ile eşlendi:

| Dataset id | Roboflow adı | GCS etiketi |
|------------|--------------|-------------|
| 0 | f16 | F16 |
| 1 | fuze | BalistikFuze |
| 2 | helikopter | Helikopter |
| 3 | mini_iha | MiniIHA |

```bash
python scripts/train_celikkubbe.py
```

Eğitim `models/aeroshield.pt` üretir. `config.yaml` bu dosyayı kullanır; henüz yoksa `yolov8n.pt` + COCO stub map ile açılır.

### Klavye

| Tuş | İşlev |
|-----|--------|
| W/S | Tilt |
| A/D | Pan |
| Space | Ateş (Aşama 1) |
| Esc | E-Stop |

## Mimari

```
CameraProcess → InferenceProcess → ControlProcess → SerialProcess
                                         ↓
                              GuiBridge (QThread) → PyQt5 UI
```

## Yapılandırma

Tüm parametreler `config.yaml` içinde: kamera, model, PID, WEZ menzilleri, yasak bölge, bakım süresi.
