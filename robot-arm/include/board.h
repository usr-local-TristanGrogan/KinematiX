#ifndef BOARD_H
#define BOARD_H

#include <stdbool.h>
#include <stdint.h>

void board_init(void);
void board_timer_ack(void);
uint32_t board_irq_save(void);
void board_irq_restore(uint32_t saved);
uint32_t board_millis(void);
void board_step_levels(uint8_t mask);
void board_directions(const int direction[2], const bool inverted[2]);
void board_led(bool on);
bool board_stop_pressed(void);
void board_serial_write(const char *text);
int board_serial_read(void);
bool board_serial_fault(void);
bool board_take_stop_request(void);
void board_serial_discard(void);

#endif
