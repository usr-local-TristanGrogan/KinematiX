#include "motion.h"

#include <math.h>
#include <stddef.h>
#include <string.h>

void motion_init(Motion *motion)
{
    memset(motion, 0, sizeof(*motion));
}

bool motion_start(Motion *motion, const int32_t target[2], float max_rate,
                  float acceleration)
{
    if (motion->active || !isfinite(max_rate) || max_rate < 1.0f ||
        max_rate > MOTION_MAX_RATE || !isfinite(acceleration) ||
        acceleration < 1.0f || acceleration > 100000.0f) {
        return false;
    }
    uint32_t delta[2];
    for (unsigned i = 0; i < 2; ++i) {
        const int64_t difference = (int64_t)target[i] - (int64_t)motion->position[i];
        const uint64_t magnitude = (uint64_t)(difference < 0 ? -difference : difference);
        if (magnitude > MOTION_MAX_STEPS) {
            return false;
        }
        delta[i] = (uint32_t)magnitude;
    }
    motion->total = delta[0] > delta[1] ? delta[0] : delta[1];
    motion->completed = 0;
    motion->phase = 0.0f;
    motion->rate = 0.0f;
    motion->max_rate = max_rate;
    motion->acceleration = acceleration;
    motion->setup_ticks = 2; /* At least 100 us DIR setup before the first pulse. */
    motion->pulse_mask = 0;
    for (unsigned i = 0; i < 2; ++i) {
        motion->target[i] = target[i];
        motion->delta[i] = delta[i];
        motion->error[i] = 0;
        motion->direction[i] = target[i] >= motion->position[i] ? 1 : -1;
    }
    motion->active = motion->total != 0;
    return true;
}

uint8_t motion_tick(Motion *motion)
{
    /* Each pulse lasts one tick (50 us); always end it on the next tick. */
    const bool was_high = motion->pulse_mask != 0;
    motion->pulse_mask = 0;
    if (!motion->active) {
        return 0;
    }
    if (motion->completed == motion->total) {
        motion->active = false;
        return 0;
    }
    if (motion->setup_ticks != 0) {
        --motion->setup_ticks;
        return 0;
    }
    const float dt = 1.0f / (float)MOTION_TICK_HZ;
    const float remaining = (float)(motion->total - motion->completed) - motion->phase;
    const float stop_distance = motion->rate * motion->rate / (2.0f * motion->acceleration);
    if (stop_distance >= remaining) {
        /* A one-step stopping floor guarantees completion despite discrete ticks. */
        const float floor_rate = fminf(motion->max_rate, sqrtf(2.0f * motion->acceleration));
        motion->rate = fmaxf(floor_rate, motion->rate - motion->acceleration * dt);
    } else {
        motion->rate = fminf(motion->max_rate, motion->rate + motion->acceleration * dt);
    }
    motion->phase += motion->rate * dt;
    if (was_high || motion->phase < 1.0f) {
        return 0;
    }
    motion->phase -= 1.0f;
    for (unsigned i = 0; i < 2; ++i) {
        motion->error[i] += motion->delta[i];
        if (motion->error[i] >= motion->total) {
            motion->error[i] -= motion->total;
            motion->position[i] += motion->direction[i];
            motion->pulse_mask |= (uint8_t)(1U << i);
        }
    }
    ++motion->completed;
    /* Keep active until the trailing edge has completed, including the last step. */
    return motion->pulse_mask;
}

void motion_abort(Motion *motion)
{
    motion->active = false;
    motion->pulse_mask = 0;
    motion->rate = 0.0f;
    motion->phase = 0.0f;
    for (unsigned i = 0; i < 2; ++i) {
        motion->target[i] = motion->position[i];
    }
}
