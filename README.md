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

Komut paketi (PC → ESP32):

`[0xAA][MOD][PAN_H][PAN_L][TILT_H][TILT_L][FIRE][CHECKSUM][0xFF]`

Durum paketi (ESP32 → PC):

`[0xAA][STATUS][LIMIT_PAN][LIMIT_TILT][CHECKSUM][0xFF]`

### Model

- Varsayılan: `yolov8n.pt` (ilk çalıştırmada indirilir)
- KTR ağırlığı: `model.path: models/yolov11m.pt` veya `models/aeroshield.pt`
- Özel sınıf isimleri: `model.class_names`

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
