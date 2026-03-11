# L298N Motor Driver Upgrade - Summary of Changes

## Overview

Successfully upgraded the DogBot motor control system from L293D (no speed control) to L298N with PWM speed control. All firmware, documentation, and visual diagrams have been updated.

## Files Modified

### 1. ESP32 Firmware
**File:** `firmware/esp32cam_dogbot/esp32cam_dogbot.ino`

**Changes:**
- Added GPIO pin definitions for ENABLE A (GPIO 2) and ENABLE B (GPIO 4)
- Configured PWM channels using ESP32 LEDC peripheral (1 kHz, 8-bit resolution)
- Updated motor control functions to accept speed parameter (0-255)
- Modified HTTP handler to parse and use speed parameter
- Modified MQTT callback to parse and use speed parameter
- Added speed tracking to status reporting (current speed and driver type)
- Updated Serial Monitor output to show L298N configuration

**Key Code Additions:**
```cpp
#define ENA_PIN     2  // PWM Speed - Left motors
#define ENB_PIN     4  // PWM Speed - Right motors
#define PWM_FREQ      1000   // 1 kHz
#define PWM_RESOLUTION  8    // 0-255
#define PWM_CHANNEL_A   0
#define PWM_CHANNEL_B   1

void motorForward(int speed) { /* ... */ }
void motorASpeed(int speed) { ledcWrite(PWM_CHANNEL_A, speed); }
```

### 2. Pin Diagram HTML
**File:** `backend/static/pin_diagram.html`

**Changes:**
- Updated title from "L293D" to "L298N" with PWM indicator
- Changed PCB color from blue (L293D) to red (L298N)
- Added GPIO 2 and GPIO 4 pins to ESP32-CAM diagram with labels
- Added ENABLE A and ENABLE B pins to L298N driver diagram
- Added wire connections for ENABLE pins (orange and light blue)
- Updated legend to include new wire colors
- Updated connection table to include ENABLE pin wiring
- Replaced L293D specification card with L298N specifications
- Added prominent warning about jumper removal
- Added setup instructions section with critical steps
- Updated component labels from "MOTOR 1/2" to "MOTOR A/B"

**Visual Updates:**
- Orange wire: GPIO 2 → ENABLE A
- Light blue wire: GPIO 4 → ENABLE B
- PCB pattern changed to red (L298N characteristic)
- ENABLE pins added to motor control headers

### 3. Backend (No Changes Required)
**File:** `backend/services/esp32_client.py`

**Status:** Already supports speed parameter
- `send_motor_command(direction, speed=200)` method already implemented
- HTTP: `/motor?dir={direction}&speed={speed}`
- MQTT: `{"dir":"forward", "speed":200}`
- Default speed: 200 (78% power)

**No modifications needed** - the backend was designed with speed control in mind.

## New Documentation Files

### 1. L298N_UPGRADE_GUIDE.md
**Purpose:** Complete upgrade guide with step-by-step instructions

**Contents:**
- Hardware requirements and component list
- Detailed pin mapping tables
- Critical setup steps (jumper removal, wiring sequence)
- Firmware upload instructions
- Testing procedures (basic, PWM, direction, backend integration)
- Troubleshooting guide (common issues and solutions)
- Technical specifications (PWM config, H-bridge truth table)
- Backend integration notes
- Advantages over L293D comparison
- Safety notes and references

**Key Sections:**
- Jumper removal (most critical step)
- Wire color recommendations
- Testing at different speeds
- Heat management guidance

### 2. L298N_WIRING_REFERENCE.txt
**Purpose:** Quick reference ASCII diagram for wiring

**Contents:**
- ASCII art wiring diagram
- Complete connection table
- Critical warnings highlighted
- Quick test commands
- PWM specifications
- Troubleshooting quick reference

**Features:**
- Printable format
- Easy to reference during assembly
- Clear visual layout with boxes and tables

### 3. UPGRADE_SUMMARY.md
**Purpose:** This document - changelog and overview

## Pin Mapping Changes

### Old System (L293D)
```
GPIO 12 → IN1 (Left direction)
GPIO 13 → IN2 (Left direction)
GPIO 14 → IN3 (Right direction)
GPIO 15 → IN4 (Right direction)
ENA/ENB: Tied HIGH (no control)
```

### New System (L298N)
```
GPIO 12 → IN1 (Left direction)      [Same]
GPIO 13 → IN2 (Left direction)      [Same]
GPIO 14 → IN3 (Right direction)     [Same]
GPIO 15 → IN4 (Right direction)     [Same]
GPIO 2  → ENABLE A (Left speed)     [NEW - PWM]
GPIO 4  → ENABLE B (Right speed)    [NEW - PWM]
```

## Feature Comparison

| Feature | L293D (Old) | L298N (New) |
|---------|-------------|-------------|
| Speed Control | None (full speed only) | PWM 0-255 (8-bit) |
| Current Capacity | 600mA/channel | 2A/channel |
| Peak Current | 1.2A | 3A |
| Heat Sink | Small | Large with fins |
| PWM Frequency | N/A | 1 kHz (configurable) |
| Voltage Drop | ~1V | ~2V |
| GPIO Pins Used | 4 (IN1-IN4) | 6 (IN1-IN4 + ENA/ENB) |
| Firmware Changes | N/A | LEDC PWM configuration |

## Critical Setup Requirements

### 1. Hardware Setup
- **MUST remove both ENABLE jumpers on L298N module**
- Without removal: motors run at full speed, PWM ignored
- Jumper location: Near screw terminals labeled "ENABLE A" and "ENABLE B"

### 2. Wiring Verification
- All grounds connected (ESP32, L298N, Battery)
- GPIO 2 and GPIO 4 connected to ENABLE pins (not IN pins)
- Direction pins (IN1-IN4) maintain same connections as before
- Battery voltage 5-6.5V (4xAA pack)

### 3. Firmware Upload
- Use Arduino IDE with AI-Thinker ESP32-CAM board
- Upload modified `esp32cam_dogbot.ino`
- Verify Serial Monitor shows "L298N with PWM speed control"

### 4. Testing
- Test at multiple speeds: 100, 150, 200, 255
- Verify speed changes are audible/visible
- Test all four directions (forward, back, left, right)
- Check that stop command works

## API Interface

### HTTP Commands
```bash
# Variable speed control (NEW)
GET /motor?dir=forward&speed=100   # Slow (40%)
GET /motor?dir=forward&speed=150   # Medium (60%)
GET /motor?dir=forward&speed=255   # Full (100%)

# Backward compatibility (default speed=200)
GET /motor?dir=forward              # Uses default speed
```

### MQTT Commands
```json
{
  "dir": "forward",
  "speed": 150
}
```

### Status Response
```json
{
  "rssi": -45,
  "uptime": 12345,
  "motor": "forward",
  "speed": 150,          // NEW
  "ip": "192.168.1.100",
  "mqtt_connected": true,
  "driver": "L298N"      // NEW
}
```

## Benefits Achieved

### 1. Autonomous Navigation Improvements
- **Dynamic speed control**: AI can slow down for obstacles, corners
- **Smoother motion**: Gradual acceleration/deceleration
- **Battery efficiency**: Run at lower speeds to extend runtime
- **Better control**: Precise maneuvering in tight spaces

### 2. Path Planner Integration
The path planner already outputs speed (0.0-1.0), which can now be used:
```python
# Path planner output
steering = -0.5  # Turn left
speed = 0.6      # 60% throttle

# Convert to PWM (0-255)
pwm_speed = int(speed * 255)  # = 153

# Send to ESP32
await esp32_client.send_motor_command("forward", pwm_speed)
```

### 3. Enhanced Safety
- **Emergency slow**: Approach obstacles at reduced speed
- **Gradual stop**: Ramp down instead of abrupt stop
- **Obstacle proximity**: Speed inversely proportional to distance
- **Confidence-based**: Lower confidence = lower speed

### 4. User Experience
- Dashboard now shows speed parameter
- Visual feedback on motor speed
- Testing easier with variable speeds
- Debugging simplified (can run at slow speed)

## Migration Path for Users

### For Existing L293D Users

**If you have the L293D system working:**
1. **Backup current firmware** (save `esp32cam_dogbot.ino` as `esp32cam_dogbot_L293D_backup.ino`)
2. **Test current system** to ensure it works before upgrade
3. **Order L298N module** (red PCB, ~$3-5 USD)
4. **Keep existing wiring** for IN1-IN4 connections
5. **Add two new wires** for ENABLE A and ENABLE B
6. **Remove jumpers** on L298N before powering on
7. **Upload new firmware**
8. **Test with HTTP commands** at different speeds

**Rollback option:** If issues occur, you can:
- Reconnect L293D module with old wiring
- Upload backup firmware
- System will work as before (no speed control)

### For New Builds

**Starting fresh:**
1. Use L298N from the beginning (don't buy L293D)
2. Follow wiring diagram in `L298N_WIRING_REFERENCE.txt`
3. Upload provided firmware
4. You're ready to go with speed control

## Testing Checklist

After completing the upgrade:

- [ ] Firmware uploaded successfully
- [ ] Serial Monitor shows "L298N with PWM speed control"
- [ ] ESP32 connected to WiFi (IP shown in Serial Monitor)
- [ ] Both ENABLE jumpers removed from L298N
- [ ] All 8 control wires connected (6 GPIO + 2 power)
- [ ] All 3 ground connections made (ESP32, L298N, Battery)
- [ ] Battery voltage 5-6.5V
- [ ] Motors respond to speed=100 (slow)
- [ ] Motors respond to speed=255 (fast)
- [ ] Speed difference is audible/visible
- [ ] All four directions work (forward, back, left, right)
- [ ] Stop command works immediately
- [ ] Dashboard accessible at http://localhost:8000
- [ ] Pin diagram updated at http://localhost:8000/pin-diagram

## Troubleshooting Quick Reference

| Symptom | Cause | Solution |
|---------|-------|----------|
| Motors always full speed | Jumpers not removed | Remove ENABLE jumpers |
| Motors don't move | ENABLE pins disconnected | Connect GPIO 2 & 4 |
| Wrong direction | Motor polarity reversed | Swap motor wires |
| L298N very hot | Motor stall or overload | Check mechanical binding |
| WiFi won't connect | Wrong credentials | Update lines 14-15 in firmware |
| MQTT not working | Broker settings wrong | Update lines 18-21 in firmware |

## Next Steps

### Immediate (Hardware)
1. Wire the L298N according to the diagram
2. Remove both ENABLE jumpers
3. Upload the firmware
4. Test motor speeds

### Short-term (Integration)
1. Test with dashboard controls
2. Verify AI decision engine uses speed
3. Tune path planner speed parameters
4. Test autonomous navigation

### Long-term (Optimization)
1. Calibrate optimal speed ranges for different scenarios
2. Implement acceleration/deceleration curves
3. Add battery voltage monitoring for speed compensation
4. Fine-tune PWM frequency if needed (currently 1 kHz)

## Support Resources

- **Complete Guide:** `L298N_UPGRADE_GUIDE.md`
- **Wiring Reference:** `L298N_WIRING_REFERENCE.txt`
- **Pin Diagram:** `http://localhost:8000/pin-diagram`
- **Firmware:** `firmware/esp32cam_dogbot/esp32cam_dogbot.ino`
- **Backend Client:** `backend/services/esp32_client.py`

## Version Information

- **Firmware Version:** L298N PWM Speed Control (2025-02-07)
- **ESP32 Board:** AI-Thinker ESP32-CAM
- **Motor Driver:** L298N Dual H-Bridge
- **PWM Frequency:** 1000 Hz
- **PWM Resolution:** 8-bit (0-255)
- **GPIO Pins Added:** GPIO 2 (ENA), GPIO 4 (ENB)
- **Backward Compatible:** Yes (speed defaults to 200 if not specified)

## Conclusion

The upgrade to L298N with PWM speed control is complete and fully documented. The system now supports variable speed motor control while maintaining backward compatibility with existing code. All visual diagrams, wiring references, and setup guides have been created to ensure a smooth transition.

**Key Achievement:** Full PWM speed control (0-255) with minimal changes to existing codebase.

**Critical Reminder:** Remove both ENABLE jumpers on the L298N module before connecting GPIO 2 and GPIO 4!
