# L298N Motor Driver Upgrade Guide

## Overview

This guide covers the upgrade from the L293D motor driver (no speed control) to the L298N motor driver with PWM speed control. The L298N provides significant improvements:

- **Variable speed control**: 0-255 PWM levels (8-bit resolution)
- **Higher current capacity**: 2A continuous per channel (vs 600mA on L293D)
- **Better heat dissipation**: Larger heat sink for sustained operation
- **Built-in 5V regulator**: Can power logic from motor power supply

## Hardware Requirements

### Components
- **L298N Motor Driver Module** (red PCB)
- **ESP32-CAM** (AI-Thinker)
- **4x N20 DC Motors** (or similar)
- **4x AA Battery Pack** (6V nominal)
- **Jumper wires**: 8 wires total (6 control + 2 power)

### Recommended Wire Colors
- GPIO 12 → IN1: **Blue**
- GPIO 13 → IN2: **Green**
- GPIO 14 → IN3: **Purple**
- GPIO 15 → IN4: **Yellow**
- **GPIO 2 → ENABLE A: Orange** (NEW)
- **GPIO 4 → ENABLE B: Light Blue** (NEW)
- 5V → +5V: **Red**
- GND → GND: **Black**
- Battery+ → +12V: **Dark Red**
- Battery- → GND: **Black**

## Pin Mapping

### ESP32-CAM to L298N Connections

| ESP32-CAM Pin | L298N Pin | Function | Type |
|---------------|-----------|----------|------|
| GPIO 12 | IN1 | Left motor direction | Digital |
| GPIO 13 | IN2 | Left motor direction | Digital |
| GPIO 14 | IN3 | Right motor direction | Digital |
| GPIO 15 | IN4 | Right motor direction | Digital |
| **GPIO 2** | **ENABLE A** | Left motor speed | **PWM** |
| **GPIO 4** | **ENABLE B** | Right motor speed | **PWM** |
| 5V | +5V | Logic power | Power |
| GND | GND | Common ground | Ground |

### Power Connections

| Source | L298N Pin | Purpose |
|--------|-----------|---------|
| Battery + (6V) | +12V | Motor power input |
| Battery - | GND | Common ground |

**Note:** The L298N's "+12V" input accepts 5V-35V. Despite the label, your 6V battery pack is perfectly suitable.

## Critical Setup Steps

### 1. Remove ENABLE Jumpers

**This is the most important step!**

The L298N module ships with two small jumpers installed on the ENABLE A and ENABLE B pins. These jumpers connect the ENABLE pins directly to 5V, which means the motors always run at full speed.

**Before connecting GPIO 2 and GPIO 4:**
1. Locate the two jumpers near the screw terminals labeled "ENABLE A" and "ENABLE B"
2. Use needle-nose pliers or tweezers to remove both jumpers
3. Store the jumpers somewhere safe (you may need them for testing)
4. Verify both jumpers are removed before proceeding

**If you forget this step:** The motors will run at full speed regardless of the PWM value sent from the ESP32. The speed parameter in your commands will be ignored.

### 2. Wiring Sequence

Follow this order to prevent shorts:

1. **Power off everything** - Disconnect all power sources
2. **Connect grounds first**:
   - ESP32-CAM GND → L298N GND
   - Battery - → L298N GND
3. **Connect direction control pins** (IN1-IN4):
   - GPIO 12 → IN1 (Blue)
   - GPIO 13 → IN2 (Green)
   - GPIO 14 → IN3 (Purple)
   - GPIO 15 → IN4 (Yellow)
4. **Connect PWM ENABLE pins** (with jumpers removed):
   - GPIO 2 → ENABLE A (Orange)
   - GPIO 4 → ENABLE B (Light Blue)
5. **Connect logic power**:
   - ESP32-CAM 5V → L298N +5V (Red)
6. **Connect motor power**:
   - Battery + → L298N +12V (Dark Red)
7. **Connect motors**:
   - Left motors → OUT1 & OUT2 (Motor A)
   - Right motors → OUT3 & OUT4 (Motor B)

### 3. Verify Connections

Before applying power, verify:
- [ ] Both ENABLE jumpers removed from L298N
- [ ] All grounds connected together (ESP32 GND, L298N GND, Battery -)
- [ ] GPIO 2 and GPIO 4 connected to ENABLE A and ENABLE B (not IN1-IN4)
- [ ] Motor polarity consistent (left motors both forward, right motors both forward)
- [ ] Battery voltage is within 5V-35V range
- [ ] No loose wires or shorts

## Firmware Upload

### 1. Upload Modified Firmware

The firmware has been updated to support PWM speed control. Upload the modified `esp32cam_dogbot.ino` file to your ESP32-CAM using the Arduino IDE.

**Key firmware changes:**
- Added PWM channels for GPIO 2 (ENABLE A) and GPIO 4 (ENABLE B)
- 1 kHz PWM frequency, 8-bit resolution (0-255)
- Speed parameter now functional in HTTP and MQTT commands
- Motor control functions updated to accept speed values

### 2. Arduino IDE Settings

**Board:** AI-Thinker ESP32-CAM
**Upload Speed:** 115200
**Flash Frequency:** 80MHz
**Flash Mode:** QIO
**Partition Scheme:** Default

### 3. Monitor Serial Output

After upload, open Serial Monitor (115200 baud). You should see:
```
=== DogBot Recon System ===
Camera: OK
Motor driver initialized: L298N with PWM speed control
Motors: OK
WiFi: OK
IP: 192.168.x.x
Motor Driver: L298N with PWM speed control
Control server: port 80
Stream server: port 81
MQTT: configured
=== DogBot Ready ===
```

If you see "Motor driver initialized: L298N with PWM speed control", the firmware is correctly loaded.

## Testing

### 1. Basic Motor Test

Test motors at different speeds using HTTP commands:

```bash
# Slow forward (40% speed)
http://192.168.x.x/motor?dir=forward&speed=100

# Medium forward (60% speed)
http://192.168.x.x/motor?dir=forward&speed=150

# Full forward (100% speed)
http://192.168.x.x/motor?dir=forward&speed=255

# Stop
http://192.168.x.x/motor?dir=stop
```

Replace `192.168.x.x` with your ESP32-CAM's IP address (shown in Serial Monitor).

### 2. Verify PWM Control

**Expected behavior:**
- speed=100: Motors should turn slowly (you can hear the difference)
- speed=150: Motors at medium speed
- speed=255: Motors at full speed

**If motors only run at full speed:**
- The ENABLE jumpers are still installed on the L298N
- Turn off power, remove jumpers, reconnect, and test again

**If motors don't move at all:**
- Check that ENABLE pins are connected to GPIO 2 and GPIO 4 (not disconnected)
- Verify direction control pins (IN1-IN4) are properly connected
- Check motor power connections (battery to +12V input)

### 3. Direction Test

Test all four directions at medium speed (speed=150):

```bash
# Forward
http://192.168.x.x/motor?dir=forward&speed=150

# Backward
http://192.168.x.x/motor?dir=back&speed=150

# Turn left (tank turn)
http://192.168.x.x/motor?dir=left&speed=150

# Turn right (tank turn)
http://192.168.x.x/motor?dir=right&speed=150

# Stop
http://192.168.x.x/motor?dir=stop
```

Verify that:
- Forward moves forward
- Back moves backward
- Left rotates counterclockwise (left motors back, right motors forward)
- Right rotates clockwise (left motors forward, right motors back)

If directions are reversed, swap the motor wires on the affected output terminals (OUT1-OUT4).

### 4. Backend Integration Test

The backend already supports speed control. Test through the dashboard:

1. Open the dashboard: `http://localhost:8000`
2. Click Setup Guide and Pin Diagram to verify the new wiring
3. Use manual controls to test different speeds
4. The AI decision engine will automatically use variable speed based on path planning

## Troubleshooting

### Motors Run at Full Speed Regardless of Speed Parameter

**Cause:** ENABLE jumpers still installed on L298N module

**Solution:**
1. Power off everything
2. Locate the ENABLE A and ENABLE B jumpers on the L298N
3. Remove both jumpers using needle-nose pliers
4. Reconnect and test again

### Motors Don't Move at All

**Possible causes:**

1. **ENABLE pins disconnected**
   - Verify GPIO 2 → ENABLE A (Orange wire)
   - Verify GPIO 4 → ENABLE B (Light Blue wire)

2. **No motor power**
   - Check battery voltage (should be ~6V for 4xAA)
   - Verify Battery + → L298N +12V connection
   - Check Battery - → L298N GND connection

3. **Direction pins not connected**
   - Verify all four IN pins (GPIO 12, 13, 14, 15) are connected

4. **Motor wiring**
   - Check motor connections to OUT1-OUT4 screw terminals
   - Ensure wires are tight in terminals

### Motors Spin in Wrong Direction

**Solution:** Swap the two motor wires for the affected motor pair at the L298N screw terminals (OUT1-OUT4).

### L298N Gets Hot

**Expected:** The L298N will warm up during operation due to H-bridge inefficiency (≈2V voltage drop).

**Acceptable:** Warm to the touch (50-70°C with heat sink)

**Too hot to touch (>80°C):**
- Reduce motor load (check for mechanical binding)
- Add additional heat sinking or cooling fan
- Reduce PWM duty cycle (lower speed values)
- Check for short circuits

### MQTT Control Not Working

MQTT is configured for remote operation. If MQTT commands aren't controlling the motors:

1. **Check MQTT broker connection:**
   - Serial Monitor should show "MQTT connected"
   - If not, verify broker credentials in firmware (lines 18-21)

2. **Verify topic subscription:**
   - ESP32 should subscribe to `dogbot/cmd/motor` on connection

3. **Test with HTTP first:**
   - If HTTP works but MQTT doesn't, the issue is MQTT-specific (not hardware)

4. **Windows users:**
   - Use `python run.py` to start the backend (not `uvicorn` directly)
   - This ensures proper event loop for MQTT compatibility

## Technical Specifications

### ESP32 PWM Configuration

```cpp
PWM_FREQ = 1000 Hz       // 1 kHz PWM frequency
PWM_RESOLUTION = 8-bit   // 0-255 range
PWM_CHANNEL_A = 0        // LEDC channel for Motor A
PWM_CHANNEL_B = 1        // LEDC channel for Motor B
```

**Why 1 kHz?**
- Optimal for DC motors (inaudible, smooth operation)
- Above the audible range for humans
- Low enough for efficient H-bridge switching

**Speed range:**
- 0 = Full stop (0% duty cycle)
- 128 = Half speed (50% duty cycle)
- 255 = Full speed (100% duty cycle)

### L298N H-Bridge Operation

**Truth Table for Motor A (IN1, IN2, ENABLE A):**

| IN1 | IN2 | ENA (PWM) | Motor A State |
|-----|-----|-----------|---------------|
| LOW | LOW | X | Brake/Coast |
| HIGH | LOW | 0-255 | Forward (PWM speed) |
| LOW | HIGH | 0-255 | Reverse (PWM speed) |
| HIGH | HIGH | X | Brake |

Same logic applies to Motor B (IN3, IN4, ENABLE B).

**Voltage Drop:**
- The L298N has approximately 2V drop across the H-bridge
- With 6V input, motors receive ~4V at full PWM (255)
- This is acceptable for N20 motors rated at 3-6V

## Backend Integration

The backend (FastAPI) already supports the speed parameter:

### ESP32Client

```python
async def send_motor_command(self, direction: str, speed: int = 200) -> bool:
    # Sends: /motor?dir={direction}&speed={speed}
```

Default speed is 200 (≈78% power), which balances speed and battery life.

### Path Planner Integration

The path planner (`services/path_planner/engine.py`) outputs:
- `steering`: -1.0 to +1.0 (turn angle)
- `speed`: 0.0 to 1.0 (throttle)

The speed output can be mapped to PWM values (0-255):
```python
pwm_speed = int(planner_speed * 255)
```

This allows the AI to dynamically adjust speed based on obstacles, path curvature, and confidence levels.

### Recommended Speed Ranges

| Scenario | Speed Value | Description |
|----------|-------------|-------------|
| Obstacle avoidance | 100-150 | Slow approach for precision |
| Normal navigation | 180-200 | Balanced speed and control |
| Straight path, clear | 220-255 | Maximum speed |
| Emergency stop | 0 | Immediate stop |

## Advantages Over L293D

| Feature | L293D | L298N |
|---------|-------|-------|
| Speed Control | None (always full) | PWM 0-255 |
| Current Capacity | 600mA per channel | 2A per channel |
| Peak Current | 1.2A | 3A |
| Heat Management | Minimal heat sink | Large heat sink |
| 5V Regulator | No | Yes (when Vin ≥7V) |
| Voltage Drop | ~1V | ~2V |
| Cost | Low | Low |

## Next Steps

1. **Upload the firmware** to your ESP32-CAM
2. **Wire the L298N** following the pin mapping above
3. **Remove ENABLE jumpers** (don't forget!)
4. **Test with HTTP commands** at different speeds
5. **Integrate with the dashboard** for full autonomous operation
6. **Tune speed parameters** in the path planner for optimal performance

## References

### File Locations
- **Firmware:** `firmware/esp32cam_dogbot/esp32cam_dogbot.ino`
- **Pin Diagram:** `backend/static/pin_diagram.html`
- **ESP32 Client:** `backend/services/esp32_client.py`
- **Path Planner:** `backend/services/path_planner/engine.py`

### Configuration
- **ESP32 WiFi/MQTT:** Lines 14-26 in firmware
- **Backend MQTT:** `.env` file (mqtt_broker_host, mqtt_username, mqtt_password)
- **ESP32 IP/Stream URL:** `.env` file (esp32_control_url, esp32_stream_url)

### Commands
- **Start backend:** `python run.py` (recommended for Windows)
- **Dashboard:** `http://localhost:8000`
- **Pin diagram:** `http://localhost:8000/pin-diagram`
- **ESP32 control:** `http://{ESP32_IP}/motor?dir={direction}&speed={0-255}`

## Safety Notes

1. **Always disconnect power before wiring changes**
2. **Verify ground connections first** (prevents voltage spikes)
3. **Check motor polarity** to avoid reverse direction
4. **Monitor heat sink temperature** during extended operation
5. **Use appropriate wire gauge** for motor current (20-22 AWG recommended)
6. **Keep ENABLE jumpers** for future testing/debugging

## Support

If you encounter issues:
1. Check Serial Monitor output for error messages
2. Verify all connections match the pin diagram
3. Test with HTTP commands before MQTT
4. Ensure ENABLE jumpers are removed
5. Measure battery voltage (should be 5-6.5V for 4xAA)

The system is now ready for autonomous navigation with dynamic speed control!
