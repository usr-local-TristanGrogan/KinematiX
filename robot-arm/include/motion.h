#ifndef MOTION_H
#define MOTION_H

#include <stdbool.h>
#include <stdint.h>

#define MOTION_TICK_HZ 20000U
#define MOTION_MAX_RATE 4000.0f
#define MOTION_MAX_STEPS 1000000U

typedef struct {
    int32_t position[2];
    int32_t target[2];
    int direction[2];
    uint32_t delta[2];
    uint32_t error[2];
    uint32_t total;
    uint32_t completed;
    float max_rate;
    float acceleration;
    float rate;
    float phase;
    unsigned setup_ticks;
    uint8_t pulse_mask;
    bool active;
} Motion;

void motion_init(Motion *motion);
bool motion_start(Motion *motion, const int32_t target[2], float max_rate,
                  float acceleration);
/* Called at 20 kHz. Returned bits are STEP1/STEP2 levels for this tick. */
uint8_t motion_tick(Motion *motion);
void motion_abort(Motion *motion);

#endif
