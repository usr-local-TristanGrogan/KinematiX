from edge_utils import detect_uart


BAUD_RATE = 115200


def detect(edges):
    return detect_uart(edges, BAUD_RATE)