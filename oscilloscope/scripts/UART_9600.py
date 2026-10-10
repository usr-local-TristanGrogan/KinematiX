from edge_utils import detect_uart


BAUD_RATE = 9600


def detect(edges):
	return detect_uart(edges, BAUD_RATE)
