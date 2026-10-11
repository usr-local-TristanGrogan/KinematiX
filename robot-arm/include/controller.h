#ifndef CONTROLLER_H
#define CONTROLLER_H

#include "kinematics.h"
#include "motion.h"

#include <stddef.h>

#define COMMAND_CAPACITY 128U
#define REPLY_CAPACITY 384U

typedef struct {
    ArmGeometry arm;
    Motion motion;
    float pulses_per_rev[2];
    float reference_deg[2];
    float rate;
    float acceleration;
    bool inverted[2];
    bool configured;
    bool referenced;
    bool armed;
    bool motor_mode;
    int branch;
} Controller;

void controller_init(Controller *controller);
void controller_angles(const Controller *controller, float deg[2]);
void controller_stop(Controller *controller);
typedef enum {
    COMMAND_READONLY,
    COMMAND_UPDATED,
    COMMAND_STARTED,
    COMMAND_STOPPED
} CommandEffect;
/* Caller serializes command access with the timer ISR. No hardware dependencies. */
CommandEffect controller_command(Controller *controller, const char *line,
                                 char *reply, size_t capacity);

#endif
