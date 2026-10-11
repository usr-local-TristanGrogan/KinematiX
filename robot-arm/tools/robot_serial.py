#!/usr/bin/env python3
"""Linux/macOS serial terminal for the robot, using only the Python standard library."""

import argparse
import fcntl
import glob
import os
import select
import sys
import termios
import time


def command_bytes(command):
    command = command.strip()
    if not command:
        raise ValueError("command is empty")
    if command == "!":
        return b"!"
    if "\n" in command or "\r" in command or "!" in command:
        raise ValueError("send one command per line; ! is a separate stop command")
    data = command.upper().encode("ascii")
    if len(data) > 127 or any(ch < 32 and ch != 9 for ch in data):
        raise ValueError("command must contain at most 127 printable ASCII characters")
    return data + b"\n"


class RobotSerial:
    def __init__(self, port):
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.pending = bytearray()
        try:
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            attrs = termios.tcgetattr(self.fd)
            attrs[0] = 0
            attrs[1] = 0
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attrs[3] = 0
            attrs[4] = termios.B115200
            attrs[5] = termios.B115200
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)

    def send(self, command):
        data = command_bytes(command)
        offset = 0
        deadline = time.monotonic() + 3
        while offset < len(data):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [self.fd], [], remaining)[1]:
                raise TimeoutError("serial write timed out")
            try:
                offset += os.write(self.fd, data[offset:])
            except BlockingIOError:
                continue

    def receive(self, timeout):
        if not select.select([self.fd], [], [], timeout)[0]:
            return []
        data = os.read(self.fd, 4096)
        if not data:
            raise OSError("serial device disconnected")
        self.pending.extend(data)
        lines = []
        while b"\n" in self.pending:
            line, _, tail = self.pending.partition(b"\n")
            self.pending = bytearray(tail)
            lines.append(line.rstrip(b"\r").decode("ascii", errors="replace"))
        if len(self.pending) > 8192:
            raise ValueError("serial response is too long")
        return lines

    def request(self, command, timeout=3.0, wait_motion=False):
        # Drain responses from before this request; the protocol has one outstanding command.
        for line in self.receive(0):
            print(line, flush=True)
        self.send(command)
        deadline = time.monotonic() + timeout
        awaiting_done = False
        for_stop = command.strip() == "!"
        while time.monotonic() < deadline:
            for line in self.receive(max(0.0, deadline - time.monotonic())):
                print(line, flush=True)
                if line.startswith("ERR") or line.startswith("EVENT STOPPED"):
                    return for_stop and line.startswith("EVENT STOPPED")
                if line.startswith("OK MOVING") and wait_motion:
                    awaiting_done = True
                elif line.startswith("OK") and not awaiting_done:
                    return True
                elif line == "EVENT DONE" and awaiting_done:
                    return True
        raise TimeoutError("no complete response; use STATUS to check the board")


def available_ports():
    return sorted(glob.glob("/dev/serial/by-id/*") or glob.glob("/dev/ttyACM*"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="serial device; auto-detected when exactly one exists")
    parser.add_argument("--list", action="store_true", help="list detected serial devices")
    parser.add_argument("--diagnose", action="store_true", help="show port and serial settings")
    parser.add_argument("--command", action="append", help="send a command; repeat for a sequence")
    parser.add_argument("--wait", action="store_true", help="wait for MOVE/JOINT completion")
    parser.add_argument("--timeout", type=float, default=5.0, help="response timeout in seconds")
    args = parser.parse_args()
    ports = available_ports()
    if args.list:
        print("\n".join(ports) if ports else "No serial devices found")
        return 0
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.port is None:
        if len(ports) != 1:
            parser.error("specify --port; use --list to see detected devices")
        args.port = ports[0]
    serial = RobotSerial(args.port)
    try:
        if args.diagnose:
            attrs = termios.tcgetattr(serial.fd)
            print(f"Port: {args.port}")
            print(f"Input/output speed: {attrs[4]}/{attrs[5]} (115200 constant: {termios.B115200})")
            print(f"Flags: input={attrs[0]} output={attrs[1]} control={attrs[2]} local={attrs[3]}")
            if not args.command:
                return 0
        if args.command:
            for command in args.command:
                if not serial.request(command, args.timeout, args.wait):
                    return 1
        else:
            print("Connected at 115200 8N1. Enter HELP. ! stops immediately; Ctrl-D exits.", flush=True)
            while True:
                readable, _, _ = select.select([serial.fd, sys.stdin], [], [])
                if serial.fd in readable:
                    for line in serial.receive(0):
                        print(line, flush=True)
                if sys.stdin in readable:
                    line = sys.stdin.readline()
                    if not line:
                        break
                    if line.strip():
                        try:
                            serial.send(line)
                        except ValueError as error:
                            print(error, file=sys.stderr)
    except KeyboardInterrupt:
        serial.send("!")
        print("\nStop sent; set the known pose again before resuming.", file=sys.stderr)
        return 130
    finally:
        serial.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TimeoutError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
