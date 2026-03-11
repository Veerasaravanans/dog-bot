"""
L298N Motor Speed Control Test Script

This script tests the PWM speed control capabilities of the L298N motor driver.
It sends HTTP commands to the ESP32-CAM at different speed levels to verify
that variable speed control is working correctly.

Usage:
    python test_l298n_speed.py [ESP32_IP]

Example:
    python test_l298n_speed.py 192.168.1.100

If no IP is provided, it will prompt for the ESP32's IP address.
"""

import sys
import time
import requests
from typing import Optional


class L298NTester:
    def __init__(self, esp32_ip: str):
        self.base_url = f"http://{esp32_ip}"
        self.control_url = f"{self.base_url}/motor"
        self.status_url = f"{self.base_url}/status"

    def send_command(self, direction: str, speed: int = 200) -> bool:
        """Send motor command and return success status."""
        try:
            params = {"dir": direction, "speed": speed}
            response = requests.get(self.control_url, params=params, timeout=2)

            if response.status_code == 200:
                data = response.json()
                print(f"✓ Command sent: {direction} @ speed={speed}")
                if 'speed' in data:
                    print(f"  Response confirms speed: {data['speed']}")
                return True
            else:
                print(f"✗ Command failed: HTTP {response.status_code}")
                return False

        except requests.exceptions.Timeout:
            print(f"✗ Timeout connecting to {self.control_url}")
            return False
        except requests.exceptions.ConnectionError:
            print(f"✗ Connection error - is ESP32 at {self.base_url}?")
            return False
        except Exception as e:
            print(f"✗ Error: {e}")
            return False

    def get_status(self) -> Optional[dict]:
        """Get ESP32 status."""
        try:
            response = requests.get(self.status_url, timeout=2)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None

    def test_connection(self) -> bool:
        """Test if ESP32 is reachable."""
        print(f"\n{'='*60}")
        print(f"Testing connection to ESP32-CAM at {self.base_url}")
        print(f"{'='*60}")

        status = self.get_status()
        if status:
            print("✓ ESP32 is online!")
            print(f"  IP: {status.get('ip', 'unknown')}")
            print(f"  Uptime: {status.get('uptime', 0)} seconds")
            print(f"  RSSI: {status.get('rssi', 0)} dBm")
            print(f"  Motor State: {status.get('motor', 'unknown')}")

            if 'driver' in status:
                driver = status['driver']
                print(f"  Motor Driver: {driver}")
                if driver == 'L298N':
                    print("  ✓ L298N firmware detected!")
                else:
                    print(f"  ⚠️  Warning: Expected L298N, got {driver}")
            else:
                print("  ⚠️  Warning: Driver type not in status (old firmware?)")

            if 'speed' in status:
                print(f"  Current Speed: {status['speed']}")

            return True
        else:
            print("✗ Cannot connect to ESP32")
            return False

    def test_speed_range(self):
        """Test motors at different speed levels."""
        print(f"\n{'='*60}")
        print("Testing PWM Speed Control Range")
        print(f"{'='*60}")
        print("\nThis test will run motors at different speeds.")
        print("You should hear/see speed differences between each level.\n")

        speed_levels = [
            (50, "Very Slow (20%)"),
            (100, "Slow (40%)"),
            (150, "Medium (60%)"),
            (200, "Fast (80%)"),
            (255, "Full Speed (100%)"),
        ]

        for speed, description in speed_levels:
            print(f"\n--- {description} ---")
            input(f"Press Enter to test speed={speed}...")

            if self.send_command("forward", speed):
                print(f"Motors running at speed {speed} for 2 seconds...")
                time.sleep(2)
                self.send_command("stop")
                time.sleep(1)
            else:
                print("Failed to send command. Aborting test.")
                self.send_command("stop")
                return

        print("\n✓ Speed range test complete!")
        print("\nDid you notice speed differences between each level?")
        response = input("(y/n): ").lower()

        if response == 'y':
            print("✓ PWM speed control is working correctly!")
        else:
            print("⚠️  Issue detected:")
            print("   - If motors ran at full speed every time:")
            print("     → ENABLE jumpers are still on the L298N module")
            print("     → Power off and remove both jumpers")
            print("   - If motors didn't move at all:")
            print("     → Check ENABLE A/B connections (GPIO 2 & 4)")

    def test_directions(self):
        """Test all four directions at medium speed."""
        print(f"\n{'='*60}")
        print("Testing Directional Control (Medium Speed)")
        print(f"{'='*60}")

        directions = [
            ("forward", "Forward"),
            ("back", "Backward"),
            ("left", "Turn Left (Tank)"),
            ("right", "Turn Right (Tank)"),
        ]

        test_speed = 150

        for direction, description in directions:
            print(f"\n--- {description} ---")
            input(f"Press Enter to test {direction}...")

            if self.send_command(direction, test_speed):
                print(f"Motors running {direction} at speed {test_speed} for 2 seconds...")
                time.sleep(2)
                self.send_command("stop")
                time.sleep(1)
            else:
                print("Failed to send command. Aborting test.")
                self.send_command("stop")
                return

        print("\n✓ Direction test complete!")

    def test_jumper_check(self):
        """Test if ENABLE jumpers are removed (full speed vs partial speed)."""
        print(f"\n{'='*60}")
        print("Testing ENABLE Jumper Removal")
        print(f"{'='*60}")
        print("\nThis test checks if ENABLE jumpers are properly removed.")
        print("We'll run motors at 50% speed, then 100% speed.\n")

        # Test at 50% (should be noticeably slower if jumpers removed)
        print("--- Testing 50% Speed (speed=128) ---")
        input("Press Enter to start...")

        if not self.send_command("forward", 128):
            print("Failed to send command.")
            return

        print("Motors running at 50% for 3 seconds...")
        time.sleep(3)
        self.send_command("stop")
        time.sleep(1)

        # Test at 100%
        print("\n--- Testing 100% Speed (speed=255) ---")
        input("Press Enter to start...")

        if not self.send_command("forward", 255):
            print("Failed to send command.")
            return

        print("Motors running at 100% for 3 seconds...")
        time.sleep(3)
        self.send_command("stop")

        print("\n" + "="*60)
        print("Was there a noticeable speed difference?")
        response = input("(y/n): ").lower()

        if response == 'y':
            print("✓ ENABLE jumpers are properly removed!")
            print("  PWM speed control is functional.")
        else:
            print("✗ ENABLE jumpers may still be installed!")
            print("\n  CRITICAL: The L298N has two jumpers on ENABLE A and ENABLE B.")
            print("  These jumpers connect ENABLE pins directly to 5V (full speed).")
            print("\n  TO FIX:")
            print("    1. Power off the ESP32 and L298N")
            print("    2. Locate the two jumpers near the screw terminals")
            print("    3. Remove both jumpers with needle-nose pliers")
            print("    4. Verify both are removed")
            print("    5. Power on and run this test again")

    def run_all_tests(self):
        """Run complete test suite."""
        print("\n" + "="*60)
        print(" L298N Motor Speed Control Test Suite")
        print("="*60)

        # Test 1: Connection
        if not self.test_connection():
            print("\n✗ Cannot proceed: ESP32 not reachable")
            return

        input("\nPress Enter to continue with motor tests...")

        # Test 2: Jumper check
        self.test_jumper_check()

        input("\nPress Enter to continue with speed range test...")

        # Test 3: Speed range
        self.test_speed_range()

        input("\nPress Enter to continue with direction test...")

        # Test 4: Directions
        self.test_directions()

        # Final summary
        print("\n" + "="*60)
        print(" Test Suite Complete!")
        print("="*60)
        print("\nIf all tests passed, your L298N upgrade is successful!")
        print("The DogBot is now ready for autonomous navigation with PWM speed control.")
        print("\nNext steps:")
        print("  1. Test with the dashboard at http://localhost:8000")
        print("  2. Verify AI decision engine uses variable speed")
        print("  3. Test autonomous navigation in a safe environment")


def main():
    print("L298N Motor Speed Control Test Script")
    print("="*60)

    # Get ESP32 IP
    if len(sys.argv) > 1:
        esp32_ip = sys.argv[1]
    else:
        esp32_ip = input("Enter ESP32-CAM IP address (e.g., 192.168.1.100): ").strip()

    if not esp32_ip:
        print("Error: IP address required")
        sys.exit(1)

    # Create tester and run
    tester = L298NTester(esp32_ip)

    try:
        tester.run_all_tests()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        print("Sending stop command...")
        tester.send_command("stop")
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        print("Sending stop command...")
        tester.send_command("stop")


if __name__ == "__main__":
    main()
