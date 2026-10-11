#!/usr/bin/env python3
"""Jog with left/right arrow keys; change motor speed with up/down."""

import argparse
import math
import os
import select
import sys
import termios
import time
import tty

try:
    from .robot_serial import RobotSerial, available_ports
except ImportError:
    from robot_serial import RobotSerial, available_ports


class ArrowKeys:
    def __init__(self):
        self.pending = bytearray()

    def feed(self, data):
        self.pending.extend(data)
        result = []
        while self.pending:
            first = self.pending[0]
            if first == 27:
                if len(self.pending) < 2:
                    break
                if self.pending[1] not in (ord("["), ord("O")):
                    del self.pending[0]
                    continue
                if len(self.pending) < 3:
                    break
                final = self.pending[2]
                del self.pending[:3]
                if final == ord("D"):
                    result.append("left")
                elif final == ord("C"):
                    result.append("right")
                elif final == ord("A"):
                    result.append("faster")
                elif final == ord("B"):
                    result.append("slower")
            else:
                del self.pending[0]
                if first in (ord(" "), ord("!")):
                    result.append("stop")
                elif first in (ord("1"), ord("2"), ord("3")):
                    result.append(f"motor{chr(first)}")
                elif first in (ord("q"), ord("Q"), 3, 4):
                    result.append("quit")
        return result


def acceleration_for(speed):
    # Scale acceleration with speed so short jogs respond to speed changes too.
    return min(100000.0, max(1.0, 400.0 * (speed / 100.0) ** 2))


class JogController:
    def __init__(self, robot, speed, steps, speed_step=25.0, report=print, motor=1):
        self.robot = robot
        self.speed = speed
        self.applied_speed = None
        self.speed_step = speed_step
        self.steps = steps
        self.report = report
        self.motor = motor
        self.busy = False
        self.sent_at = 0.0
        self.active_speed = speed

    def change_speed(self, direction):
        previous = self.speed
        self.speed = min(4000.0, max(1.0, self.speed + direction * self.speed_step))
        if previous != self.speed:
            suffix = " (after this jog)" if self.busy else ""
            self.report(f"Speed: {self.speed:g} pulses/s{suffix}")

    def apply_speed(self):
        if self.busy or self.speed == self.applied_speed:
            return
        if not self.robot.request(f"SPEED {self.speed:g} {acceleration_for(self.speed):g}"):
            raise ValueError("speed update was rejected; motor stopped")
        self.applied_speed = self.speed

    def select_motor(self, motor):
        self.motor = motor
        suffix = " (after this jog)" if self.busy else ""
        selection = "Both motors" if motor == 3 else f"Motor {motor}"
        self.report(f"{selection} selected{suffix}")

    def jog(self, direction, now):
        if self.busy:
            return
        self.apply_speed()
        self.robot.send(f"JOG {self.motor} {direction * self.steps}")
        self.sent_at = now
        self.active_speed = self.applied_speed
        self.busy = True

    def receive(self, line):
        self.report(line)
        if line == "EVENT DONE" or line.startswith("OK DONE"):
            self.busy = False
        elif line.startswith("ERR") or line.startswith("EVENT STOPPED"):
            raise ValueError("motor stopped; restart the terminal to resume")

    def timed_out(self, now):
        return self.busy and now - self.sent_at > 2.0 * self.steps / self.active_speed + 5.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Nucleo serial device; auto-detected if unique")
    parser.add_argument("--speed", type=float, default=100.0, help="pulse rate; default 100, half the former 200")
    parser.add_argument("--steps", type=int, default=10, help="pulses per arrow press, default 10")
    parser.add_argument("--motor", type=int, choices=(1, 2, 3), default=1, help="1 or 2 selects a motor; 3 selects both")
    parser.add_argument("--speed-step", type=float, default=25.0, help="up/down adjustment in pulses/s, default 25")
    args = parser.parse_args()
    if not math.isfinite(args.speed) or not 1 <= args.speed <= 4000:
        parser.error("--speed must be between 1 and 4000 pulses/second")
    if not 1 <= args.steps <= 2000:
        parser.error("--steps must be between 1 and 2000")
    if not math.isfinite(args.speed_step) or not 1 <= args.speed_step <= 4000:
        parser.error("--speed-step must be between 1 and 4000 pulses/second")
    if not sys.stdin.isatty():
        parser.error("run in an interactive terminal to receive arrow keys")
    if args.port is None:
        ports = available_ports()
        if len(ports) != 1:
            parser.error("specify --port; use robot_serial.py --list to find it")
        args.port = ports[0]

    robot = RobotSerial(args.port)
    original = termios.tcgetattr(sys.stdin.fileno())
    keys = ArrowKeys()
    controller = JogController(robot, args.speed, args.steps, args.speed_step, motor=args.motor)
    exit_code = 0
    try:
        if not robot.request("DISARM"):
            return 1
        controller.apply_speed()
        if not robot.request("MOTORARM"):
            return 1
        print(f"Left/right: -/+{args.steps} pulses at up to {args.speed:g} pulses/s.")
        print(f"Up/down: faster/slower by {args.speed_step:g} pulses/s (range 1–4000).")
        print("1: motor 1. 2: motor 2. 3: both motors together.")
        controller.select_motor(args.motor)
        print("Hold an arrow to repeat. Space/! stops. Q exits. No motion is queued.", flush=True)
        tty.setcbreak(sys.stdin.fileno())
        while True:
            ready, _, _ = select.select([sys.stdin, robot.fd], [], [], 0.1)
            if robot.fd in ready:
                for line in robot.receive(0):
                    controller.receive(line)
            if controller.timed_out(time.monotonic()):
                raise TimeoutError("motor command did not complete")
            if sys.stdin in ready:
                actions = keys.feed(os.read(sys.stdin.fileno(), 64))
                if "quit" in actions or "stop" in actions:
                    return 0
                for action in actions:
                    if action == "faster":
                        controller.change_speed(1)
                    elif action == "slower":
                        controller.change_speed(-1)
                    elif action in ("motor1", "motor2", "motor3"):
                        controller.select_motor(int(action[-1]))
                    elif action in ("left", "right"):
                        controller.jog(-1 if action == "left" else 1, time.monotonic())
            controller.apply_speed()
    except KeyboardInterrupt:
        exit_code = 130
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)
        try:
            robot.send("!")
        finally:
            robot.close()
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TimeoutError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
