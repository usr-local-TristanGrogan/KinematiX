import unittest

from backend.robot_serial import (
    SerialCommunicationError,
    SerialProtocolError,
    SerialResponseTimeout,
    SerialSettings,
    STM32SerialClient,
)


class FakeSerial:
    def __init__(self, response=b"READY\n", **kwargs):
        self.response = bytearray(response)
        self.commands = []
        self.timeout = kwargs.get("timeout")
        self.closed = False

    def reset_input_buffer(self):
        pass

    def write(self, command):
        self.commands.append(command)
        return len(command)

    def flush(self):
        pass

    def read(self, size):
        if not self.response:
            return b""
        result = self.response[:size]
        del self.response[:size]
        return bytes(result)

    def close(self):
        self.closed = True


class RobotSerialTests(unittest.TestCase):
    def make_client(self, response=b"READY\n", timeout=0.02):
        fake = FakeSerial(response=response)
        client = STM32SerialClient(
            SerialSettings("COM4", 115200, timeout),
            serial_factory=lambda *args, **kwargs: fake,
        )
        return client, fake

    def test_move_sends_newline_delimited_coordinates_and_accepts_ready(self):
        client, fake = self.make_client()
        with client:
            self.assertEqual(client.move(12.5, -3), "READY")
        self.assertEqual(fake.commands, [b"MOVE 12.5 -3\n"])
        self.assertTrue(fake.closed)

    def test_error_response_fails_the_move(self):
        client, _ = self.make_client(b"ERROR motor stalled\n")
        with client:
            with self.assertRaisesRegex(SerialCommunicationError, "motor stalled"):
                client.move(1, 2)

    def test_move_waits_for_firmware_completion_after_acceptance(self):
        client, fake = self.make_client(b"OK MOVING Q1=12 Q2=80\r\nEVENT DONE\r\n")
        with client:
            self.assertEqual(client.move(1, 2), "EVENT DONE")
        self.assertEqual(fake.response, b"")

    def test_startup_banner_and_blank_lines_do_not_complete_a_move(self):
        client, _ = self.make_client(
            b"READY ROBOT F446RE 115200 8N1\r\n\r\nOK MOVING Q1=1 Q2=90\nEVENT DONE\n"
        )
        with client:
            self.assertEqual(client.move(1, 2), "EVENT DONE")

    def test_already_at_target_completes_immediately(self):
        client, _ = self.make_client(b"OK DONE Q1=0 Q2=90\n")
        with client:
            self.assertEqual(client.move(1, 2), "OK DONE Q1=0 Q2=90")

    def test_accepted_move_without_done_times_out(self):
        client, _ = self.make_client(b"OK MOVING Q1=1 Q2=90\n")
        with client:
            with self.assertRaises(SerialResponseTimeout):
                client.move(1, 2)

    def test_firmware_error_and_stop_fail_the_move(self):
        for response in (b"ERR unreachable\n", b"OK MOVING\nEVENT STOPPED set_pose_again\n"):
            with self.subTest(response=response):
                client, _ = self.make_client(response)
                with client:
                    with self.assertRaises(SerialCommunicationError):
                        client.move(1, 2)

    def test_done_event_without_move_acceptance_is_rejected(self):
        client, _ = self.make_client(b"EVENT DONE\n")
        with client:
            with self.assertRaises(SerialProtocolError):
                client.move(1, 2)

    def test_home_uses_declared_pose_command(self):
        client, fake = self.make_client(b"OK MOVING Q1=0 Q2=90\nEVENT DONE\n")
        with client:
            self.assertEqual(client.home(), "EVENT DONE")
        self.assertEqual(fake.commands, [b"HOME\n"])

    def test_unexpected_response_is_a_protocol_error(self):
        client, _ = self.make_client(b"BUSY\n")
        with client:
            with self.assertRaises(SerialProtocolError):
                client.move(1, 2)

    def test_missing_response_times_out(self):
        client, _ = self.make_client(response=b"")
        with client:
            with self.assertRaises(SerialResponseTimeout):
                client.move(1, 2)

    def test_non_finite_coordinates_are_rejected(self):
        client, fake = self.make_client()
        with client:
            with self.assertRaises(ValueError):
                client.move(float("nan"), 2)
        self.assertEqual(fake.commands, [])


if __name__ == "__main__":
    unittest.main()
