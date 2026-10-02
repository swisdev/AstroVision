# ★ Celestial Tracker

A lightweight, resource-efficient application for **Raspberry Pi Model B** that
detects and tracks celestial bodies (stars, planets, satellites, meteors) using
computer vision and drives a pan/tilt servo bracket to keep the camera locked on
target.

```text
┌─────────────┐   USB/CSI   ┌───────────────┐   GPIO PWM   ┌──────────────┐
│  Pi Camera  │────────────▶│  Raspberry Pi │─────────────▶│ Pan/Tilt     │
│  or USB cam │             │  Model B      │              │ Servo Bracket│
└─────────────┘             └──────┬────────┘              └──────────────┘
                                   │ :8080
                                   ▼
                            ┌──────────────┐
                            │  Browser /   │
                            │  Remote API  │
                            └──────────────┘
```

## Features

| Feature | Detail |
|---|---|
| **Bright-object detection** | Adaptive thresholding finds stars and planets |
| **Moving-object detection** | Frame differencing spots satellites and meteors |
| **P-controller servo tracking** | Smooth proportional control locks target to frame centre |
| **Low resource usage** | 320×240 @ 10 FPS, < 50 MB RAM |
| **Live web dashboard** | Auto-refreshing HTML page on port 8080 |
| **JSON API** | `GET /api/status`, `GET /api/detections` |
| **Remote forwarding** | Optional `POST` to any remote URL on every detection |

## Hardware Requirements

- Raspberry Pi Model B (any revision — also works on Pi 2/3/4/Zero)
- USB webcam or Pi Camera Module
- Two SG90 (or similar) servo motors
- Pan/tilt servo bracket
- Jumper wires, 5 V power supply for servos

## Wiring

```text
Servo       Pi GPIO (BCM)     Wire Colour (typical)
──────      ─────────────     ─────────────────────
Pan  PWM    GPIO 17           Orange / White
Tilt PWM    GPIO 18           Orange / White
Servo VCC   External 5 V      Red
Servo GND   Pi GND + Ext GND  Brown / Black
```

> **Important:** Power the servos from an **external 5 V supply** — the Pi's
> 5 V rail cannot source enough current for two servos under load.  Connect
> the grounds together.

To change GPIO pins, edit `config.py` or set environment variables:

```bash
export CT_PAN_PIN=12
export CT_TILT_PIN=13
```

## Quick Start

```bash
# 1. Clone / copy the project to your Pi
scp -r celestial-tracker/ pi@raspberrypi:~/

# 2. Run the installer (requires sudo)
ssh pi@raspberrypi
cd ~/celestial-tracker
chmod +x install.sh
sudo ./install.sh

# 3. Activate the virtual environment
source venv/bin/activate

# 4. Start the tracker
sudo python3 tracker.py

# 5. Open the dashboard
# In a browser: http://<PI_IP>:8080
```

### Desktop Development (no servos)

```bash
pip install opencv-python-headless flask requests numpy
python3 tracker.py --no-servo
```

## Project Structure

```
celestial-tracker/
├── tracker.py            # Main entry point & control loop
├── vision.py             # CV pipeline (bright + moving detection)
├── servo_controller.py   # pigpio PWM driver + P-controller
├── web_server.py         # Flask dashboard & JSON API
├── config.py             # All tuneable parameters
├── requirements.txt      # Python dependencies
├── install.sh            # One-shot Pi installer
└── README.md             # This file
```

## Configuration Reference

All parameters live in `config.py`.  Key values to tune:

| Parameter | Default | Purpose |
|---|---|---|
| `SERVO_PAN_PIN` | 17 | BCM GPIO pin for the pan servo |
| `SERVO_TILT_PIN` | 18 | BCM GPIO pin for the tilt servo |
| `KP_PAN` / `KP_TILT` | 0.05 | P-controller gain (degrees per pixel error) |
| `SERVO_MAX_STEP` | 2.0 | Max degrees per tick (smoothness limiter) |
| `DEADZONE_PX` | 5 | Pixel error ignored to prevent jitter |
| `FRAME_WIDTH` / `HEIGHT` | 320 / 240 | Capture resolution |
| `FPS_LIMIT` | 10 | Processing ticks per second |
| `ADAPTIVE_BLOCK_SIZE` | 11 | Neighbourhood for adaptive threshold |
| `DIFF_THRESHOLD` | 30 | Sensitivity for motion detection |
| `REMOTE_POST_URL` | *(empty)* | URL to POST detection payloads to |

## API

### `GET /api/status`

```json
{
  "tracking": true,
  "pan_angle": 94.5,
  "tilt_angle": 87.3,
  "target": {
    "x": 172,
    "y": 110,
    "object_type": "bright_star",
    "confidence": 0.85
  },
  "fps": 9.8,
  "uptime_s": 342
}
```

### `GET /api/detections`

Returns an array of the most recent detection events (up to 200):

```json
[
  {
    "timestamp": "2026-10-01T06:45:12+0000",
    "object_type": "moving_celestial",
    "current_pan_angle": 102.4,
    "current_tilt_angle": 78.1,
    "confidence_score": 0.72
  }
]
```

### `POST /api/forward`

Manually trigger a POST of the latest detection to `REMOTE_POST_URL`.

```bash
curl -X POST http://raspberrypi:8080/api/forward
```

## Tuning Tips

1. **Too jittery?** Increase `DEADZONE_PX` or decrease `KP_PAN`/`KP_TILT`.
2. **Too slow to track?** Increase `KP_*` or `SERVO_MAX_STEP`.
3. **False positives on stars?** Raise `MIN_BRIGHT_AREA` or lower `ADAPTIVE_C`.
4. **Missing satellites?** Lower `DIFF_THRESHOLD` or `MIN_MOVING_AREA`.
5. **CPU too high?** Reduce `FPS_LIMIT` or `FRAME_WIDTH`/`FRAME_HEIGHT`.

## License

MIT
