# 🔭 AstroVision (AV) — Autonomous Celestial Tracking System

A lightweight, resource-efficient computer vision and 2-axis servo tracking application engineered for the **Raspberry Pi** (Model B, 2, 3, 4, 5, and Zero 2W). AstroVision detects celestial bodies (stars, planets, satellites, meteors) using OpenCV and drives a pan/tilt servo bracket to lock targets directly into the center of the camera frame.

Includes a modern, dark-mode telemetry dashboard inspired by Linear and Vercel design systems, featuring live video streaming, manual 4-axis tactile D-pad jog controls, and REST API endpoints.

---

## ⚡ Highlights

- **Multi-Modal Detection Pipeline**:
  - **Bright Static Objects** (Stars & Planets): Gaussian blur, grayscale conversion, and adaptive thresholding with contour filtering.
  - **Moving Celestial Bodies** (Satellites, ISS passes, Meteors): High-speed frame differencing with morphological noise rejection.
- **Proportional (P) Closed-Loop Servo Controller**:
  - Computes the pixel delta ($\Delta X, \Delta Y$) between the detected target centroid and camera frame center.
  - Automatically nudges Pan (X) and Tilt (Y) servos with clamped step rates and an anti-jitter deadband.
- **Ultra-Low Resource Footprint**:
  - Optimized for low-RAM boards (Raspberry Pi Model B / Zero). Runs smoothly at 320×240 @ 10 FPS with < 50 MB RAM and low CPU usage.
- **Futuristic Dark-Mode Web Hub**:
  - Real-time live camera stream (`/video_feed`).
  - Tactile 5-button D-pad for manual telescope / bracket orientation with selectable step sizes (1°, 5°, 10°) and keyboard arrow keybindings.
  - Auto-tracking toggle (`/api/toggle_autotrack`).
  - Single-click high-resolution snapshot capture (`/api/capture`).
  - Remote telemetry forwarding via HTTP POST.
- **Desktop Simulation Mode**: Run and test vision, tracking logic, and web controls on your Mac, Windows, or Linux laptop without needing hardware connected (`--no-servo`).

---

## 📐 System Architecture

```text
       ┌────────────────────────┐
       │ USB Cam / CSI Pi Camera│
       └───────────┬────────────┘
                   │ Video Frames (320x240 @ 10 FPS)
                   ▼
       ┌────────────────────────┐
       │   OpenCV CV Pipeline   │◀──── config.py (Thresholds & Gains)
       │  (Adaptive & Diff CV)  │
       └───────────┬────────────┘
                   │ Target Centroid (X, Y)
                   ▼
       ┌────────────────────────┐         GPIO PWM (pigpio)
       │  Closed-Loop P-Control ├──────────────────────────┐
       │ (Error to Step Mapping)│                          │
       └───────────┬────────────┘                          ▼
                   │ Telemetry & Stream             ┌──────────────┐
                   ▼                                │ 2-Axis Servos│
       ┌────────────────────────┐                   │ (Pan / Tilt) │
       │ Flask Web Server :8080 │                   └──────────────┘
       │  (REST API & Stream)   │
       └───────────┬────────────┘
                   │ HTTP / JSON
                   ▼
      ┌──────────────────────────┐
      │ Browser Telemetry Web UI │
      │  (Desktop, Tablet, Phone)│
      └──────────────────────────┘
```

---

## 🧰 Hardware Requirements & Bill of Materials

| Component | Recommended Model | Notes |
|---|---|---|
| **SBC** | Raspberry Pi (Model B, 2B, 3B, 4B, 5, or Zero 2W) | Running Raspberry Pi OS (Lite or Desktop) |
| **Camera** | Raspberry Pi Camera Module (v2, v3, or HQ) OR standard USB Webcam | USB plug-and-play works out of the box |
| **Servos** | 2× SG90 (Micro 9g) or MG90S (Metal Gear) Servos | MG90S recommended for heavier camera lenses |
| **Bracket** | Mini 2-Axis Pan/Tilt Camera Gimbal Bracket | Readily available on Amazon / Adafruit / AliExpress |
| **Power Supply** | External 5V DC (2A–3A) Power Supply for Servos | **Required**: Do NOT power servos from the Pi 5V rail |
| **Wires** | Jumper Wires (Female-to-Male, Male-to-Male) | Breadboard or prototyping shield |

---

## 🔌 Hardware Wiring & Pinout Guide

```text
SERVO SIGNAL WIRING (Raspberry Pi BCM Pinout):
────────────────────────────────────────────────────────────────────────────
Servo Wire        Connected To                      Pi Physical Pin (BCM)
────────────────────────────────────────────────────────────────────────────
Pan PWM (Orange)  ──▶ Raspberry Pi GPIO 17           Pin 11 (BCM 17)
Tilt PWM (Orange) ──▶ Raspberry Pi GPIO 18           Pin 12 (BCM 18)
Servos VCC (Red)  ──▶ External 5V Power Supply (+)   DO NOT CONNECT TO PI
Servos GND (Brown)──▶ External 5V Power Supply (-)  ──┐ (COMMON GROUND)
                      AND Raspberry Pi GND Pin 6     ◀─┘
────────────────────────────────────────────────────────────────────────────
```

> [!CAUTION]
> **COMMON GROUND & POWER WARNING:**
> 1. **Do NOT power the servos directly from the Raspberry Pi's 5V pin!** Servo motors draw sudden current spikes up to 800mA+ when starting movement. Powering them from the Pi's rail will cause brownouts, immediate reboots, and potential SD card filesystem corruption.
> 2. **Always connect the External 5V Power Supply GND to a Raspberry Pi GND pin.** Without a common ground reference, the PWM signal voltage floats, causing severe servo jitter and uncontrolled twitching.

---

## 🚀 Quick Start Guide

### Step 1: Clone the Repository to Your Raspberry Pi

Log in to your Raspberry Pi via terminal or SSH:

```bash
# Clone the repository
git clone https://github.com/swisdev/AstroVision.git
cd AstroVision
```

### Step 2: Automated Installation (Recommended)

Run the included automated installer script. It configures system packages, compiles native OpenCV dependencies, creates a Python virtual environment, and enables the `pigpiod` hardware PWM service:

```bash
chmod +x install.sh
sudo ./install.sh
```

### Step 3: Activate Environment & Launch

```bash
# Activate virtual environment
source venv/bin/activate

# Launch AstroVision with hardware servo control
sudo python3 tracker.py
```

### Step 4: Open the Web Dashboard

Open any web browser on your computer, tablet, or phone connected to the same local network:

```text
http://<YOUR_RASPBERRY_PI_IP>:8080
```
*(Example: `http://192.168.1.145:8080` or `http://localhost:8080` if browsing on the Pi itself)*

---

## 💻 Desktop Simulation Mode (No Hardware Required)

You can run and test AstroVision on your desktop or laptop (Windows, macOS, Linux) without a Raspberry Pi or physical servos:

```bash
# Install dependencies
pip install -r requirements.txt

# Run in vision & web simulation mode
python3 tracker.py --no-servo
```

In `--no-servo` mode:
- The servo driver switches to a safe software stub.
- If no physical webcam is detected, the video feed displays a high-tech synthetic dark-sky simulation with moving satellites and celestial stars.
- All web controls, D-pad directional moves, and REST endpoints remain fully functional.

---

## 📂 Project Structure

```text
AstroVision/
├── config.py             # Central configuration: GPIO pins, tuning gains, thresholds
├── tracker.py            # Main execution loop: frame capture, detection, P-control, telemetry
├── vision.py             # OpenCV vision pipeline: bright star detection & frame differencing
├── servo_controller.py   # pigpio hardware PWM driver with closed-loop P-controller
├── web_server.py         # Flask HTTP server: live MJPEG video stream & REST API
├── templates/
│   └── index.html        # Clean, modern Vercel/Linear dark-mode dashboard
├── static/
│   └── style.css         # Matte slate stylesheet & tactile D-pad styles
├── install.sh            # Automated one-shot setup script for Raspberry Pi OS
├── requirements.txt      # Python dependencies (OpenCV, Flask, Requests, pigpio, NumPy)
└── README.md             # System documentation & troubleshooting guide
```

---

## ⚙️ Configuration & Tuning Reference

All operational constants live in [`config.py`](config.py). You can adjust them directly in the file or override them via system environment variables:

| Variable | Default | Environment Override | Description |
|---|---|---|---|
| `SERVO_PAN_PIN` | `17` | `CT_PAN_PIN=17` | BCM GPIO pin driving the Pan (horizontal) servo PWM |
| `SERVO_TILT_PIN` | `18` | `CT_TILT_PIN=18` | BCM GPIO pin driving the Tilt (vertical) servo PWM |
| `KP_PAN` | `0.05` | — | Proportional gain for Pan (degrees adjustment per pixel offset) |
| `KP_TILT` | `0.05` | — | Proportional gain for Tilt (degrees adjustment per pixel offset) |
| `SERVO_MAX_STEP` | `2.0` | — | Maximum degrees servos can move in a single control cycle |
| `DEADZONE_PX` | `5` | — | Pixel error deadband around frame center (prevents servo jitter) |
| `FRAME_WIDTH` | `320` | — | Optical capture width (downsampled for low CPU usage) |
| `FRAME_HEIGHT` | `240` | — | Optical capture height |
| `FPS_LIMIT` | `10` | — | Frame processing tick frequency (Hz) |
| `ADAPTIVE_BLOCK_SIZE`| `11`| — | Kernel size for adaptive star thresholding |
| `DIFF_THRESHOLD` | `30` | — | Sensitivity threshold for satellite/meteor motion differencing |
| `WEB_PORT` | `8080` | `CT_WEB_PORT=8080` | Local port for the web dashboard |
| `REMOTE_POST_URL` | `""` | `CT_REMOTE_URL=...`| Optional URL to forward detection JSON payloads via HTTP POST |

---

## 📡 REST API & Network Endpoints

AstroVision exposes a lightweight REST API for automation, remote monitoring, and integration with home observatories:

| Method | Endpoint | Description | Sample Response / Payload |
|---|---|---|---|
| `GET` | `/` | Web Telemetry & Control Dashboard | HTML Web Page |
| `GET` | `/video_feed` | Live MJPEG Camera Video Stream | Multipart JPEG Stream |
| `GET` | `/api/status` | Current tracking state, angles, target, and FPS | `{"tracking": true, "pan_angle": 92.5, "tilt_angle": 88.0, "fps": 10.0}` |
| `GET` | `/api/detections`| List of recent detection events (up to 200) | `[{"object_type": "bright_star", "confidence_score": 0.88, ...}]` |
| `POST`| `/api/move` | 4-Axis D-Pad control (`up`, `down`, `left`, `right`, `center`) | Query: `?direction=up&step=5` or JSON: `{"direction": "up", "step": 5}` |
| `POST`| `/api/servo/center`| Re-center scope to default Home (90°, 90°) | `{"status": "centered", "pan": 90.0, "tilt": 90.0}` |
| `POST`| `/api/toggle_autotrack`| Enable or pause autonomous servo tracking | `{"status": "ok", "autotrack": true}` |
| `POST`| `/api/capture` | Take and log an instant optical snapshot | `{"status": "success", "snapshot_id": "snap_1729000", ...}` |
| `POST`| `/api/forward` | Manually forward the latest detection event | `{"status": "forwarded", "payload": {...}}` |

---

## 🛠️ Auto-Start on Boot (Systemd Service)

To run AstroVision as a headless background daemon whenever the Raspberry Pi powers on:

1. Create a systemd service file:
   ```bash
   sudo nano /etc/systemd/system/astrovision.service
   ```

2. Paste the following configuration (replace `pi` with your actual username if different):
   ```ini
   [Unit]
   Description=AstroVision Autonomous Celestial Tracker
   After=network.target pigpiod.service
   Wants=pigpiod.service

   [Service]
   Type=simple
   User=root
   WorkingDirectory=/home/pi/AstroVision
   ExecStart=/home/pi/AstroVision/venv/bin/python3 /home/pi/AstroVision/tracker.py
   Restart=always
   RestartSec=5
   Environment=PYTHONUNBUFFERED=1

   [Install]
   WantedBy=multi-user.target
   ```

3. Enable and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable astrovision
   sudo systemctl start astrovision
   ```

4. Check live service logs:
   ```bash
   sudo journalctl -u astrovision -f
   ```

---

## 🔍 Troubleshooting & Frequently Asked Questions

### 1. APT Error: `Package 'pigpio'` or `'libatlas-base-dev' has no installation candidate`

- **Cause: You are running Raspberry Pi OS Bookworm (Debian 12) or Ubuntu.**
  - Debian 12 removed `libatlas-base-dev` and replaced it with `libopenblas-dev`.
  - Debian 12 also dropped `pigpio` from the standard APT package repository.
- **Solution (Automatic):**
  Pull the latest repository changes and re-run the updated installer, which automatically detects your OS and builds `pigpio` from source in ~20 seconds:
  ```bash
  git pull
  sudo ./install.sh
  ```
- **Solution (Manual):**
  If installing manually, install `libopenblas-dev` and compile `pigpio` from its official repository:
  ```bash
  sudo apt-get update
  sudo apt-get install -y build-essential git python3-pip python3-venv libopenblas-dev
  
  # Build and install pigpio
  git clone https://github.com/joan2937/pigpio.git /tmp/pigpio
  cd /tmp/pigpio
  make && sudo make install
  sudo ldconfig
  sudo systemctl restart pigpiod
  ```

---

### 2. Camera Fails to Open (`Cannot open camera 0. Exiting.`)

- **Cause A: Camera index mismatch.**
  Check available video devices:
  ```bash
  ls -l /dev/video*
  v4l2-ctl --list-devices
  ```
  If your camera is mapped to `/dev/video1` or `/dev/video2`, change `CAMERA_INDEX` in `config.py`:
  ```python
  CAMERA_INDEX = 1
  ```
- **Cause B: Raspberry Pi Camera Module (CSI Ribbon) not enabled.**
  On older Raspberry Pi OS (Buster/Bullseye), enable the camera interface:
  ```bash
  sudo raspi-config
  # Navigate to Interface Options -> Legacy Camera -> Enable -> Reboot
  ```
- **Cause C: Camera ribbon cable inverted.**
  Ensure the blue tape on the ribbon cable faces the Ethernet/USB ports (on standard Pi boards) and is firmly seated in the connector.
- **Cause D: V4L2 kernel driver not loaded.**
  Force load the Video4Linux2 driver:
  ```bash
  sudo modprobe bcm2835-v4l2
  echo "bcm2835-v4l2" | sudo tee -a /etc/modules
  ```

---

### 2. pigpio Daemon Error (`pigpio daemon not running — servos disabled`)

- **Cause: The `pigpiod` service is inactive or not installed.**
  Start and enable it manually:
  ```bash
  sudo systemctl restart pigpiod
  sudo systemctl status pigpiod
  ```
- **Cause: Port 8888 conflict or stale socket.**
  Kill conflicting instances and restart:
  ```bash
  sudo killall pigpiod
  sudo pigpiod
  ```
- **Cause: Running tracker without root permissions.**
  `pigpio` requires root privileges to access Direct Memory Access (DMA) hardware timers:
  ```bash
  sudo python3 tracker.py
  ```

---

### 3. Servos Are Jittering, Humming, or Twitching Continuously

- **Solution A: Verify Common Ground Connection.**
  The single most common cause of servo flutter is a missing common ground between the external 5V power supply and the Raspberry Pi. Connect an external PSU ground wire to any Pi GND pin (e.g., Pin 6 or 14).
- **Solution B: Increase the Anti-Jitter Deadband.**
  If the target pixel position fluctuates by 1–3 pixels due to atmospheric noise, the controller will continuously issue micro-adjustments. In `config.py`, increase:
  ```python
  DEADZONE_PX = 8   # Default is 5. Try 8 or 10.
  ```
- **Solution C: Reduce Controller Proportional Gain.**
  If the servos overshoot and oscillate back and forth around the target:
  ```python
  KP_PAN  = 0.03    # Lower from 0.05
  KP_TILT = 0.03    # Lower from 0.05
  ```

---

### 4. Servo Moves Away from Target (Inverted Direction)

- **Solution: Invert the Gain Coefficient.**
  Depending on how your pan/tilt bracket is physically assembled, moving clockwise may decrease rather than increase pixel coordinates. Simply invert the sign of the proportional gain in `config.py`:
  ```python
  KP_PAN = -0.05     # Invert Pan direction
  # and/or
  KP_TILT = -0.05    # Invert Tilt direction
  ```

---

### 5. Web Dashboard Is Inaccessible from Another Device

- **Solution A: Check IP Address and Port.**
  Run `hostname -I` on your Pi to confirm its local IP. Make sure you are visiting `http://<PI_IP>:8080` (include the `:8080` port).
- **Solution B: Confirm 0.0.0.0 Binding.**
  Verify `WEB_HOST = "0.0.0.0"` in `config.py`. If set to `127.0.0.1`, the server will only accept local connections originating from the Pi itself.
- **Solution C: Check Local Firewall.**
  If `ufw` or `iptables` is active, open port 8080:
  ```bash
  sudo ufw allow 8080/tcp
  ```

---

### 6. High CPU Usage or Slow Framerate on Low-RAM Pi Models

- **Solution A: Keep Capture Resolution at 320×240.**
  Higher resolutions (e.g., 1080p) will overwhelm the single-core CPU of a Pi Model B or Pi Zero. 320×240 provides the optimal balance of celestial centroid accuracy and low memory usage.
- **Solution B: Lower Process Tick Frequency.**
  In `config.py`, reduce:
  ```python
  FPS_LIMIT = 8    # Drops CPU processing load by 20%
  ```
- **Solution C: Expand Swap Memory (Prevent Out-Of-Memory Crashes).**
  On 256MB/512MB RAM boards, increase swap size:
  ```bash
  sudo dphys-swapfile swapoff
  sudo sed -i 's/CONF_SWAPSIZE=100/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
  sudo dphys-swapfile setup
  sudo dphys-swapfile swapon
  ```

---

### 7. False Positives on Cloudy or Noisy Nights

- **Solution: Fine-Tune Detection Filters in `config.py`:**
  - For static bright stars: Increase `MIN_BRIGHT_AREA` from `4` to `10` to filter out single-pixel camera sensor hot spots.
  - For moving objects: Increase `DIFF_THRESHOLD` from `30` to `45` and `MIN_MOVING_AREA` from `20` to `50` to reject clouds drifting in the wind.

---

## 📜 License

This project is open-source and released under the [MIT License](LICENSE). Contributions, issues, and feature requests are welcome!
