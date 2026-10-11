"""Decode classic CAN 2.0 standard frames from a transceiver RXD edge trace."""

from __future__ import annotations

from bisect import bisect_right


BIT_RATES = (500_000, 250_000, 125_000, 1_000_000)
CRC15_POLYNOMIAL = 0x4599


class InvalidCanFrame(ValueError):
    pass


class EdgeSampler:
    def __init__(self, edges):
        self.times = [edge["time_s"] for edge in edges]
        self.levels = [edge["to_level"] for edge in edges]
        self.initial_level = edges[0]["from_level"] if edges else 1

    def level_at(self, timestamp):
        index = bisect_right(self.times, timestamp) - 1
        return self.levels[index] if index >= 0 else self.initial_level


class CanBitReader:
    def __init__(self, sampler: EdgeSampler, start_time: float, bit_rate: int):
        self.sampler = sampler
        self.start_time = start_time
        self.bit_period = 1.0 / bit_rate
        self.bit_index = 0
        self.previous_bit = None
        self.same_bit_count = 0

    def _read_sampled_bit(self):
        timestamp = self.start_time + (self.bit_index + 0.5) * self.bit_period
        self.bit_index += 1
        return self.sampler.level_at(timestamp)

    def read_stuffed_bit(self):
        if self.same_bit_count == 5:
            stuff_bit = self._read_sampled_bit()
            if stuff_bit == self.previous_bit:
                raise InvalidCanFrame("Invalid CAN bit-stuff sequence.")
            self.previous_bit = stuff_bit
            self.same_bit_count = 1

        bit = self._read_sampled_bit()
        if bit == self.previous_bit:
            self.same_bit_count += 1
        else:
            self.previous_bit = bit
            self.same_bit_count = 1
        return bit

    def finish_stuffing(self):
        if self.same_bit_count == 5:
            stuff_bit = self._read_sampled_bit()
            if stuff_bit == self.previous_bit:
                raise InvalidCanFrame("Invalid CAN bit-stuff sequence before CRC delimiter.")
            self.previous_bit = stuff_bit
            self.same_bit_count = 1

    def read_unstuffed_bit(self):
        return self._read_sampled_bit()


def _bits_to_int(bits):
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return value


def _crc15(bits):
    remainder = 0
    for bit in bits:
        top_bit = (remainder >> 14) & 1
        remainder = (remainder << 1) & 0x7FFF
        if top_bit ^ bit:
            remainder ^= CRC15_POLYNOMIAL
    return remainder


def _read_bits(reader, count):
    return [reader.read_stuffed_bit() for _ in range(count)]


def _decode_frame(sampler: EdgeSampler, start_time: float, bit_rate: int):
    reader = CanBitReader(sampler, start_time, bit_rate)
    bits = _read_bits(reader, 14)
    if bits[0] != 0:
        raise InvalidCanFrame("CAN start-of-frame bit was recessive.")

    base_identifier = _bits_to_int(bits[1:12])
    identifier_extension = 0
    rtr_or_srr = bits[12]
    ide = bits[13]

    if ide == 0:
        rtr = rtr_or_srr
        reserved = _read_bits(reader, 1)
        if reserved[0] != 0:
            raise InvalidCanFrame("Reserved standard-frame bit was not dominant.")
        dlc_bits = _read_bits(reader, 4)
        identifier = base_identifier
        extended = False
        header_bits = bits + reserved + dlc_bits
    else:
        if rtr_or_srr != 1:
            raise InvalidCanFrame("Extended-frame SRR bit was not recessive.")
        extension_bits = _read_bits(reader, 18)
        identifier_extension = _bits_to_int(extension_bits)
        rtr_and_reserved = _read_bits(reader, 3)
        rtr = rtr_and_reserved[0]
        if rtr_and_reserved[1:] != [0, 0]:
            raise InvalidCanFrame("Reserved extended-frame bits were not dominant.")
        dlc_bits = _read_bits(reader, 4)
        identifier = (base_identifier << 18) | identifier_extension
        extended = True
        header_bits = bits + extension_bits + rtr_and_reserved + dlc_bits

    dlc = _bits_to_int(dlc_bits)
    if dlc > 8:
        raise InvalidCanFrame("Classic CAN data length exceeded eight bytes.")

    data_bits = [] if rtr else _read_bits(reader, dlc * 8)
    crc_prefix = header_bits + data_bits

    received_crc_bits = _read_bits(reader, 15)
    received_crc = _bits_to_int(received_crc_bits)
    calculated_crc = _crc15(crc_prefix)
    if received_crc != calculated_crc:
        raise InvalidCanFrame("CAN CRC-15 check failed.")

    reader.finish_stuffing()
    crc_delimiter = reader.read_unstuffed_bit()
    ack_slot = reader.read_unstuffed_bit()
    ack_delimiter = reader.read_unstuffed_bit()
    eof = [reader.read_unstuffed_bit() for _ in range(7)]
    if crc_delimiter != 1 or ack_delimiter != 1 or eof != [1] * 7:
        raise InvalidCanFrame("Invalid CAN delimiter or end-of-frame bits.")

    payload = [
        _bits_to_int(data_bits[index:index + 8])
        for index in range(0, len(data_bits), 8)
    ]
    return {
        "start_time_s": start_time,
        "identifier": identifier,
        "identifier_hex": f"0x{identifier:08X}" if extended else f"0x{identifier:03X}",
        "extended": extended,
        "remote_frame": bool(rtr),
        "dlc": dlc,
        "data_bytes": payload,
        "data_hex": " ".join(f"{byte:02X}" for byte in payload),
        "ascii_preview": bytes(payload).decode("ascii", errors="replace"),
        "crc15": f"0x{received_crc:04X}",
        "crc_valid": True,
        "acknowledged": ack_slot == 0,
        "sampled_bits": reader.bit_index,
    }


def _decode_at_rate(edges, bit_rate):
    sampler = EdgeSampler(edges)
    starts = [
        edge["time_s"]
        for edge in edges
        if edge["from_level"] == 1 and edge["to_level"] == 0
    ]
    frames = []
    ignored_starts = 0
    next_frame_time = float("-inf")

    for start_time in starts:
        if start_time < next_frame_time:
            continue
        try:
            frame = _decode_frame(sampler, start_time, bit_rate)
        except InvalidCanFrame:
            ignored_starts += 1
            continue
        frames.append(frame)
        next_frame_time = start_time + frame["sampled_bits"] / bit_rate

    score = min(1.0, len(frames) / 3)
    return {
        "protocol": f"CAN {bit_rate}",
        "score": round(score, 4),
        "evidence": {
            "bit_rate": bit_rate,
            "valid_frames": len(frames),
            "rejected_start_edges": ignored_starts,
            "frames": frames[:32],
        },
    }


def detect(edges):
    """Try common CAN bit rates and score candidates by CRC-valid frames."""
    candidates = [_decode_at_rate(edges, bit_rate) for bit_rate in BIT_RATES]
    return max(
        candidates,
        key=lambda candidate: (
            candidate["score"],
            candidate["evidence"]["valid_frames"],
        ),
    )