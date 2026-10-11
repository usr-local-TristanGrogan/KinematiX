import importlib.util
import os
from pathlib import Path
import pty
import sys
import threading
import unittest


spec = importlib.util.spec_from_file_location(
    "robot_serial", Path(__file__).resolve().parents[1] / "tools" / "robot_serial.py"
)
robot_serial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(robot_serial)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from arrow_control import ArrowKeys, JogController, acceleration_for


class SerialTests(unittest.TestCase):
    def test_arrow_keys(self):
        keys = ArrowKeys()
        self.assertEqual(keys.feed(b"\x1b"), [])
        self.assertEqual(keys.feed(b"[D"), ["left"])
        self.assertEqual(keys.feed(b"\x1b[C\x1bOD"), ["right", "left"])
        self.assertEqual(keys.feed(b"a\x1b[A\x1bOB"), ["faster", "slower"])
        self.assertEqual(keys.feed(b" !q\x03"), ["stop", "stop", "quit", "quit"])
        self.assertEqual(keys.feed(b"123"), ["motor1", "motor2", "motor3"])

    def test_speed_changes_wait_for_jog_completion(self):
        class FakeRobot:
            def __init__(self):
                self.commands = []

            def request(self, command):
                self.commands.append(command)
                return True

            def send(self, command):
                self.commands.append(command)

        robot = FakeRobot()
        controller = JogController(robot, 100.0, 10, report=lambda line: None)
        controller.apply_speed()
        controller.jog(-1, 1.0)
        controller.change_speed(1)
        controller.change_speed(1)
        controller.select_motor(2)
        controller.apply_speed()
        controller.jog(1, 1.1)
        self.assertEqual(robot.commands, ["SPEED 100 400", "JOG 1 -10"])
        controller.receive("EVENT DONE")
        controller.apply_speed()
        self.assertEqual(robot.commands[-1], "SPEED 150 900")
        controller.jog(1, 2.0)
        self.assertEqual(robot.commands[-1], "JOG 2 10")
        self.assertEqual(controller.active_speed, 150.0)
        controller.select_motor(3)
        controller.jog(-1, 2.1)
        self.assertEqual(robot.commands[-1], "JOG 2 10")
        controller.receive("EVENT DONE")
        controller.jog(-1, 3.0)
        self.assertEqual(robot.commands[-1], "JOG 3 -10")
        self.assertFalse(controller.timed_out(2.1))
        self.assertTrue(controller.timed_out(10.0))
        with self.assertRaises(ValueError):
            controller.receive("EVENT STOPPED set_pose_again")

    def test_speed_bounds(self):
        controller = JogController(None, 3995.0, 10, report=lambda line: None)
        controller.change_speed(1)
        self.assertEqual(controller.speed, 4000.0)
        controller.speed = 10.0
        controller.change_speed(-1)
        self.assertEqual(controller.speed, 1.0)
        self.assertEqual(acceleration_for(1.0), 1.0)
        self.assertEqual(acceleration_for(100.0), 400.0)
        self.assertEqual(acceleration_for(4000.0), 100000.0)

    def test_commands(self):
        self.assertEqual(robot_serial.command_bytes(" move 12.5 -30 "), b"MOVE 12.5 -30\n")
        self.assertEqual(robot_serial.command_bytes("!"), b"!")
        for malformed in ("", "ARM\nMOVE 1 2", "MOVE ! 3", "a" * 128, "MOVE \x00 2"):
            with self.assertRaises(ValueError):
                robot_serial.command_bytes(malformed)

    def test_fragmented_serial_and_stop(self):
        master, slave = pty.openpty()
        port = robot_serial.RobotSerial(os.ttyname(slave))
        try:
            port.send("status")
            self.assertEqual(os.read(master, 1024), b"STATUS\n")
            os.write(master, b"OK STA")
            self.assertEqual(port.receive(1), [])
            os.write(master, b"TE=READY\r\nEVENT DONE\n")
            self.assertEqual(port.receive(1), ["OK STATE=READY", "EVENT DONE"])
            port.send("!")
            self.assertEqual(os.read(master, 1024), b"!")
        finally:
            port.close()
            os.close(master)
            os.close(slave)

    def test_wait_for_move(self):
        master, slave = pty.openpty()
        port = robot_serial.RobotSerial(os.ttyname(slave))
        seen = []

        def board():
            seen.append(os.read(master, 1024))
            os.write(master, b"OK MOVING Q1=1 Q2=90\r\nEVENT DONE\r\n")

        thread = threading.Thread(target=board, daemon=True)
        thread.start()
        try:
            self.assertTrue(port.request("MOVE 200 150", timeout=2, wait_motion=True))
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(seen, [b"MOVE 200 150\n"])
        finally:
            port.close()
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
