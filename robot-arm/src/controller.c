#include "controller.h"

#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void controller_init(Controller *controller)
{
    memset(controller, 0, sizeof(*controller));
    controller->arm.min_deg[0] = -170.0f;
    controller->arm.max_deg[0] = 170.0f;
    controller->arm.min_deg[1] = -170.0f;
    controller->arm.max_deg[1] = 170.0f;
    controller->reference_deg[0] = 0.0f;
    controller->reference_deg[1] = 90.0f;
    controller->branch = 1;
    controller->rate = 100.0f;
    controller->acceleration = 400.0f;
}

void controller_angles(const Controller *controller, float deg[2])
{
    for (unsigned i = 0; i < 2; ++i) {
        deg[i] = controller->reference_deg[i];
        if (controller->configured) {
            deg[i] += (float)controller->motion.position[i] * 360.0f / controller->pulses_per_rev[i];
        }
    }
}

void controller_stop(Controller *controller)
{
    motion_abort(&controller->motion);
    controller->armed = false;
    /* Abrupt stops can lose steps; require a fresh physical pose declaration. */
    controller->referenced = false;
}

static CommandEffect respond(char *reply, size_t capacity, const char *text)
{
    (void)snprintf(reply, capacity, "%s", text);
    return COMMAND_READONLY;
}

static bool parse_numbers(char *const tokens[], unsigned count, float values[])
{
    for (unsigned i = 0; i < count; ++i) {
        char *end;
        errno = 0;
        values[i] = strtof(tokens[i], &end);
        if (end == tokens[i] || *end != '\0' || errno == ERANGE || !isfinite(values[i])) {
            return false;
        }
    }
    return true;
}

static const char *ik_error(IkResult result)
{
    switch (result) {
    case IK_UNREACHABLE: return "ERR unreachable";
    case IK_SINGULAR: return "ERR singular_pose";
    case IK_LIMIT: return "ERR joint_limits";
    default: return "ERR invalid_geometry";
    }
}

static CommandEffect start_angles(Controller *controller, const float deg[2],
                                  char *reply, size_t capacity)
{
    if (!arm_angles_allowed(&controller->arm, deg)) {
        return respond(reply, capacity, "ERR joint_limits");
    }
    int32_t target[2];
    float quantized[2];
    for (unsigned i = 0; i < 2; ++i) {
        const float count = roundf((deg[i] - controller->reference_deg[i]) *
                                   controller->pulses_per_rev[i] / 360.0f);
        if (!isfinite(count) || fabsf(count) > 20000000.0f) {
            return respond(reply, capacity, "ERR step_range");
        }
        target[i] = (int32_t)count;
        quantized[i] = controller->reference_deg[i] + (float)target[i] * 360.0f /
                       controller->pulses_per_rev[i];
    }
    /* Rounding must not take the actual step target beyond a joint limit. */
    if (!arm_angles_allowed(&controller->arm, quantized)) {
        return respond(reply, capacity, "ERR quantized_joint_limits");
    }
    if (!motion_start(&controller->motion, target, controller->rate, controller->acceleration)) {
        return respond(reply, capacity, "ERR motion_range");
    }
    (void)snprintf(reply, capacity, "OK %s Q1=%.3f Q2=%.3f",
                   controller->motion.active ? "MOVING" : "DONE",
                   (double)quantized[0], (double)quantized[1]);
    return COMMAND_STARTED;
}

CommandEffect controller_command(Controller *controller, const char *line,
                                 char *reply, size_t capacity)
{
    char copy[COMMAND_CAPACITY];
    char *tokens[8];
    unsigned count = 0;
    if (strlen(line) >= sizeof(copy)) {
        return respond(reply, capacity, "ERR line_too_long");
    }
    (void)strcpy(copy, line);
    char *cursor = copy;
    while (*cursor != '\0') {
        while (isspace((unsigned char)*cursor)) { ++cursor; }
        if (*cursor == '\0') { break; }
        if (count == 8) {
            return respond(reply, capacity, "ERR too_many_arguments");
        }
        tokens[count++] = cursor;
        while (*cursor != '\0' && !isspace((unsigned char)*cursor)) { ++cursor; }
        if (*cursor != '\0') { *cursor++ = '\0'; }
    }
    if (count == 0) { return respond(reply, capacity, "ERR empty_command"); }
    const char *command = tokens[0];
    const unsigned nargs = count - 1;
    float args[7];
    if (!parse_numbers(&tokens[1], nargs, args)) {
        return respond(reply, capacity, "ERR invalid_number");
    }

    if (strcmp(command, "HELP") == 0 && nargs == 0) {
        return respond(reply, capacity,
            "OK HELP STATUS CONFIG L1 L2 PPR1 PPR2; LIMITS MIN1 MAX1 MIN2 MAX2; "
            "INVERT 0|1 0|1; SPEED PULSES_PER_SEC ACCEL; POSE Q1 Q2; "
            "ARM DISARM; CHECK X Y; MOVE X Y; HOME; JOINT Q1 Q2; MOTORARM; JOG [MOTOR] PULSES; STOP; !");
    }
    if (strcmp(command, "STATUS") == 0 && nargs == 0) {
        if (controller->motor_mode) {
            (void)snprintf(reply, capacity,
                "OK STATE=MOTOR_%s STEP1=%ld STEP2=%ld RATE=%.1f ACCEL=%.1f INV1=%u",
                !controller->armed ? "DISARMED" : controller->motion.active ? "MOVING" : "READY",
                (long)controller->motion.position[0], (long)controller->motion.position[1],
                (double)controller->rate, (double)controller->acceleration,
                (unsigned)controller->inverted[0]);
            return COMMAND_READONLY;
        }
        float deg[2], xy[2] = {0.0f, 0.0f};
        controller_angles(controller, deg);
        if (controller->configured) { arm_forward(&controller->arm, deg, xy); }
        const char *state = !controller->configured ? "UNCONFIGURED" :
            !controller->referenced ? "UNREFERENCED" :
            !controller->armed ? "DISARMED" :
            controller->motion.active ? "MOVING" : "READY";
        (void)snprintf(reply, capacity,
            "OK STATE=%s Q1=%.3f Q2=%.3f X=%.3f Y=%.3f L1=%.3f L2=%.3f "
            "PPR1=%.3f PPR2=%.3f RATE=%.1f ACCEL=%.1f INV1=%u INV2=%u",
            state, (double)deg[0], (double)deg[1], (double)xy[0], (double)xy[1],
            (double)controller->arm.l1_mm, (double)controller->arm.l2_mm,
            (double)controller->pulses_per_rev[0], (double)controller->pulses_per_rev[1],
            (double)controller->rate, (double)controller->acceleration,
            (unsigned)controller->inverted[0], (unsigned)controller->inverted[1]);
        return COMMAND_READONLY;
    }
    if (strcmp(command, "STOP") == 0 && nargs == 0) {
        controller_stop(controller);
        (void)respond(reply, capacity, "OK STOPPED set_pose_again");
        return COMMAND_STOPPED;
    }
    if (strcmp(command, "DISARM") == 0 && nargs == 0) {
        if (controller->motion.active) {
            controller_stop(controller);
            (void)respond(reply, capacity, "OK STOPPED set_pose_again");
            return COMMAND_STOPPED;
        }
        controller->armed = false;
        (void)respond(reply, capacity, "OK DISARMED");
        return COMMAND_UPDATED;
    }
    if (controller->motion.active) { return respond(reply, capacity, "ERR busy"); }

    if (strcmp(command, "MOTORARM") == 0 && nargs == 0) {
        if (controller->armed) { return respond(reply, capacity, "ERR disarm_first"); }
        /* Bench control uses relative pulses without assuming geometry. */
        motion_init(&controller->motion);
        controller->motor_mode = true;
        controller->referenced = false;
        controller->armed = true;
        (void)respond(reply, capacity, "OK MOTOR_ARMED");
        return COMMAND_UPDATED;
    }
    if (strcmp(command, "JOG") == 0 && (nargs == 1 || nargs == 2)) {
        if (!controller->motor_mode || !controller->armed) {
            return respond(reply, capacity, "ERR motorarm_first");
        }
        if (nargs == 2 && args[0] != 1.0f && args[0] != 2.0f && args[0] != 3.0f) {
            return respond(reply, capacity, "ERR motor_use_1_2_or_3");
        }
        const unsigned axis = nargs == 2 && args[0] == 2.0f ? 1U : 0U;
        const bool both = nargs == 2 && args[0] == 3.0f;
        const float pulses = nargs == 2 ? args[1] : args[0];
        if (roundf(pulses) != pulses || fabsf(pulses) > 2000.0f) {
            return respond(reply, capacity, "ERR jog_integer_range_2000");
        }
        int32_t target[2] = {controller->motion.position[0], controller->motion.position[1]};
        for (unsigned i = 0; i < 2; ++i) {
            if (both || i == axis) {
                const int64_t destination = (int64_t)controller->motion.position[i] + (int64_t)pulses;
                if (destination < INT32_MIN || destination > INT32_MAX) {
                    return respond(reply, capacity, "ERR step_range");
                }
                target[i] = (int32_t)destination;
            }
        }
        if (!motion_start(&controller->motion, target, controller->rate, controller->acceleration)) {
            return respond(reply, capacity, "ERR motion_range");
        }
        (void)snprintf(reply, capacity, "OK %s STEP1=%ld STEP2=%ld",
                       controller->motion.active ? "MOVING" : "DONE",
                       (long)target[0], (long)target[1]);
        return COMMAND_STARTED;
    }

    if (strcmp(command, "CONFIG") == 0 && nargs == 4) {
        if (controller->armed) { return respond(reply, capacity, "ERR disarm_first"); }
        if (args[0] <= 0.0f || args[0] > 10000.0f || args[1] <= 0.0f || args[1] > 10000.0f ||
            args[2] < 1.0f || args[2] > 10000000.0f || args[3] < 1.0f || args[3] > 10000000.0f) {
            return respond(reply, capacity, "ERR configuration_range");
        }
        controller->arm.l1_mm = args[0];
        controller->arm.l2_mm = args[1];
        controller->pulses_per_rev[0] = args[2];
        controller->pulses_per_rev[1] = args[3];
        motion_init(&controller->motion);
        controller->configured = true;
        controller->motor_mode = false;
        controller->referenced = false;
        (void)respond(reply, capacity, "OK CONFIGURED set_pose");
        return COMMAND_UPDATED;
    }
    if (strcmp(command, "LIMITS") == 0 && nargs == 4) {
        if (controller->armed) { return respond(reply, capacity, "ERR disarm_first"); }
        if (args[0] < -360.0f || args[1] > 360.0f || args[0] >= args[1] ||
            args[2] < -180.0f || args[3] > 180.0f || args[2] >= args[3]) {
            return respond(reply, capacity, "ERR limits_range");
        }
        ArmGeometry proposed = controller->arm;
        proposed.min_deg[0] = args[0]; proposed.max_deg[0] = args[1];
        proposed.min_deg[1] = args[2]; proposed.max_deg[1] = args[3];
        float deg[2]; controller_angles(controller, deg);
        if (controller->referenced && !arm_angles_allowed(&proposed, deg)) {
            return respond(reply, capacity, "ERR pose_outside_limits");
        }
        controller->arm = proposed;
        (void)respond(reply, capacity, "OK LIMITS");
        return COMMAND_UPDATED;
    }
    if (strcmp(command, "INVERT") == 0 && nargs == 2) {
        if (controller->armed) { return respond(reply, capacity, "ERR disarm_first"); }
        if ((args[0] != 0.0f && args[0] != 1.0f) || (args[1] != 0.0f && args[1] != 1.0f)) {
            return respond(reply, capacity, "ERR invert_use_0_or_1");
        }
        controller->inverted[0] = args[0] != 0.0f;
        controller->inverted[1] = args[1] != 0.0f;
        controller->referenced = false;
        (void)respond(reply, capacity, "OK INVERT set_pose");
        return COMMAND_UPDATED;
    }
    if (strcmp(command, "SPEED") == 0 && nargs == 2) {
        if (args[0] < 1.0f || args[0] > MOTION_MAX_RATE || args[1] < 1.0f || args[1] > 100000.0f) {
            return respond(reply, capacity, "ERR speed_range");
        }
        controller->rate = args[0]; controller->acceleration = args[1];
        (void)respond(reply, capacity, "OK SPEED");
        return COMMAND_UPDATED;
    }
    if (strcmp(command, "POSE") == 0 && nargs == 2) {
        if (controller->armed) { return respond(reply, capacity, "ERR disarm_first"); }
        if (!controller->configured) { return respond(reply, capacity, "ERR configure_first"); }
        if (!arm_angles_allowed(&controller->arm, args)) {
            return respond(reply, capacity, "ERR joint_limits");
        }
        controller->reference_deg[0] = args[0]; controller->reference_deg[1] = args[1];
        controller->branch = args[1] < 0.0f ? -1 : 1;
        motion_init(&controller->motion);
        controller->referenced = true;
        controller->motor_mode = false;
        (void)respond(reply, capacity, "OK POSE");
        return COMMAND_UPDATED;
    }
    if (strcmp(command, "ARM") == 0 && nargs == 0) {
        if (!controller->configured) { return respond(reply, capacity, "ERR configure_first"); }
        if (!controller->referenced) { return respond(reply, capacity, "ERR set_pose_first"); }
        controller->armed = true;
        (void)respond(reply, capacity, "OK ARMED");
        return COMMAND_UPDATED;
    }
    if (strcmp(command, "HOME") == 0 && nargs == 0) {
        if (!controller->configured) { return respond(reply, capacity, "ERR configure_first"); }
        if (!controller->referenced || !controller->armed) {
            return respond(reply, capacity, "ERR pose_and_arm_first");
        }
        return start_angles(controller, controller->reference_deg, reply, capacity);
    }
    const bool check = strcmp(command, "CHECK") == 0;
    const bool move = strcmp(command, "MOVE") == 0;
    const bool joint = strcmp(command, "JOINT") == 0;
    if ((check || move || joint) && nargs == 2) {
        if (!controller->configured) { return respond(reply, capacity, "ERR configure_first"); }
        if (!check && (!controller->referenced || !controller->armed)) {
            return respond(reply, capacity, "ERR pose_and_arm_first");
        }
        float deg[2] = {args[0], args[1]};
        if (!joint) {
            float near_deg[2]; controller_angles(controller, near_deg);
            const IkResult result = arm_inverse(&controller->arm, args, controller->branch, near_deg, deg);
            if (result != IK_OK) { return respond(reply, capacity, ik_error(result)); }
        }
        if (check) {
            (void)snprintf(reply, capacity, "OK CHECK Q1=%.3f Q2=%.3f", (double)deg[0], (double)deg[1]);
            return COMMAND_READONLY;
        }
        const CommandEffect effect = start_angles(controller, deg, reply, capacity);
        if (joint && effect == COMMAND_STARTED) { controller->branch = deg[1] < 0.0f ? -1 : 1; }
        return effect;
    }
    return respond(reply, capacity, "ERR command_or_arguments see_HELP");
}
