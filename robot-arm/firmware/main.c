#include "board.h"
#include "controller.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

static Controller controller;
static volatile uint32_t generation;
static volatile bool finished_event, stopped_event;
static unsigned button_ticks;

void TIM2_IRQHandler(void)
{
    board_timer_ack();
    const bool pressed = board_stop_pressed();
    if (!pressed) { button_ticks = 0; }
    else if (button_ticks < 101U) { ++button_ticks; }
    if (board_take_stop_request() || button_ticks == 100U) {
        controller_stop(&controller);
        board_step_levels(0);
        finished_event = false;
        stopped_event = true;
        ++generation;
        return;
    }
    const bool was_active = controller.motion.active;
    const uint8_t pulses = controller.armed ? motion_tick(&controller.motion) : 0U;
    board_step_levels(pulses);
    if (was_active && !controller.motion.active) {
        finished_event = true;
        ++generation;
    }
}

static void execute_line(const char *line)
{
    Controller snapshot;
    uint32_t version;
    uint32_t saved = board_irq_save();
    snapshot = controller;
    version = generation;
    board_irq_restore(saved);

    char reply[REPLY_CAPACITY];
    /* Parse, solve IK and format replies with interrupts enabled. */
    const CommandEffect effect = controller_command(&snapshot, line, reply, sizeof(reply));
    saved = board_irq_save();
    if (effect == COMMAND_STOPPED) {
        controller_stop(&controller);
        board_step_levels(0);
        finished_event = false;
        ++generation;
    } else if (effect != COMMAND_READONLY) {
        if (version != generation || stopped_event || board_stop_pressed()) {
            (void)snprintf(reply, sizeof(reply), "ERR interrupted set_pose_again");
        } else {
            controller = snapshot;
            if (effect == COMMAND_STARTED) {
                board_directions(controller.motion.direction, controller.inverted);
            }
            ++generation;
        }
    }
    board_irq_restore(saved);
    board_serial_write(reply);
    board_serial_write("\r\n");
}

int main(void)
{
    controller_init(&controller);
    board_init();
    board_serial_write("READY ROBOT F446RE 115200 8N1; HELP; XY: CONFIG + POSE 0 90; motor: MOTORARM\r\n");
    char line[COMMAND_CAPACITY];
    unsigned length = 0;
    bool discard_line = false;
    for (;;) {
        const uint32_t now = board_millis();
        board_led(controller.armed ? true : (now % 1000U) < 100U);

        const uint32_t saved = board_irq_save();
        const bool stopped = stopped_event;
        const bool finished = finished_event;
        stopped_event = false;
        finished_event = false;
        board_irq_restore(saved);
        if (stopped) {
            length = 0;
            discard_line = false;
            board_serial_discard();
            board_serial_write("EVENT STOPPED set_pose_again\r\n");
        } else if (finished) {
            board_serial_write("EVENT DONE\r\n");
        }
        if (board_serial_fault()) {
            length = 0;
            discard_line = true;
            board_serial_discard();
            board_serial_write("ERR serial_overrun_or_framing resend_complete_line\r\n");
        }

        /* One character per loop: events and stop handling always get service. */
        const int ch = board_serial_read();
        if (ch < 0) { continue; }
        if (ch == '\r' || ch == '\n') {
            if (discard_line) {
                board_serial_write("ERR malformed_or_long_line\r\n");
            } else if (length != 0) {
                line[length] = '\0';
                execute_line(line);
            }
            length = 0;
            discard_line = false;
        } else if ((ch == '\b' || ch == 127) && !discard_line) {
            if (length > 0) { --length; }
        } else if (!discard_line) {
            if ((ch < 32 && ch != '\t') || ch > 126 || length + 1U >= sizeof(line)) {
                discard_line = true;
            } else {
                line[length++] = (char)ch;
            }
        }
    }
}
