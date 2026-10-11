#include "controller.h"

#include <assert.h>
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static void near(float actual, float expected, float tolerance)
{
    assert(fabsf(actual - expected) < tolerance);
}

static ArmGeometry geometry(void)
{
    const ArmGeometry arm = {200.0f, 150.0f, {-170.0f, -170.0f}, {170.0f, 170.0f}};
    return arm;
}

static void test_kinematics(void)
{
    ArmGeometry arm = geometry();
    const float pose[2] = {0.0f, 90.0f};
    float xy[2], result[2];
    arm_forward(&arm, pose, xy);
    near(xy[0], 200.0f, 0.001f); near(xy[1], 150.0f, 0.001f);
    assert(arm_inverse(&arm, xy, 1, pose, result) == IK_OK);
    near(result[0], 0.0f, 0.001f); near(result[1], 90.0f, 0.001f);
    for (int q1 = -160; q1 <= 160; q1 += 8) {
        for (int q2 = 10; q2 <= 160; q2 += 10) {
            const float angles[2] = {(float)q1, (float)q2};
            arm_forward(&arm, angles, xy);
            assert(arm_inverse(&arm, xy, 1, angles, result) == IK_OK);
            near(result[0], angles[0], 0.003f); near(result[1], angles[1], 0.003f);
            const float negative[2] = {(float)q1, -(float)q2};
            arm_forward(&arm, negative, xy);
            assert(arm_inverse(&arm, xy, -1, negative, result) == IK_OK);
            near(result[0], negative[0], 0.003f); near(result[1], negative[1], 0.003f);
        }
    }
    const float outer[2] = {350.0f, 0.0f};
    assert(arm_inverse(&arm, outer, 1, pose, result) == IK_OK);
    near(result[1], 0.0f, 0.001f);
    const float impossible[2] = {351.0f, 0.0f};
    assert(arm_inverse(&arm, impossible, 1, pose, result) == IK_UNREACHABLE);
    const float origin[2] = {0.0f, 0.0f};
    assert(arm_inverse(&arm, origin, 1, pose, result) == IK_UNREACHABLE);
    arm.l2_mm = arm.l1_mm;
    assert(arm_inverse(&arm, origin, 1, pose, result) == IK_SINGULAR);
    const float invalid[2] = {NAN, 0.0f};
    assert(arm_inverse(&arm, invalid, 1, pose, result) == IK_INVALID);
    arm = geometry(); arm.max_deg[1] = 80.0f;
    const float right_angle[2] = {200.0f, 150.0f};
    assert(arm_inverse(&arm, right_angle, 1, pose, result) == IK_LIMIT);
    arm.l1_mm = 0.0f;
    assert(arm_inverse(&arm, right_angle, 1, pose, result) == IK_INVALID);

    /* Crossing atan2's +/-180 wrap must use the nearby shoulder orientation. */
    arm = geometry(); arm.min_deg[0] = -360.0f; arm.max_deg[0] = 360.0f;
    const float wrap[2] = {190.0f, 30.0f};
    arm_forward(&arm, wrap, xy);
    assert(arm_inverse(&arm, xy, 1, wrap, result) == IK_OK);
    near(result[0], 190.0f, 0.001f);
}

static void finish_motion(Motion *motion)
{
    unsigned ticks = 0;
    while (motion->active) {
        (void)motion_tick(motion);
        assert(++ticks < 10000000U);
    }
}

static void test_one_motion(int32_t first, int32_t second, float rate, float acceleration)
{
    Motion motion; motion_init(&motion);
    const int32_t target[2] = {first, second};
    assert(motion_start(&motion, target, rate, acceleration));
    uint32_t rising_edges[2] = {0, 0};
    uint8_t previous = 0;
    unsigned ticks = 0;
    while (motion.active) {
        const uint8_t level = motion_tick(&motion);
        assert((level & previous) == 0); /* At most 50 us high; at least 50 us low. */
        if (ticks < 2) { assert(level == 0); }
        for (unsigned i = 0; i < 2; ++i) {
            if ((level & (1U << i)) != 0) { ++rising_edges[i]; }
            const float ideal = (float)motion.completed * (float)motion.delta[i] /
                                 (float)(motion.total == 0 ? 1U : motion.total);
            const int32_t signed_position = motion.position[i] * motion.direction[i];
            assert(fabsf((float)signed_position - ideal) < 1.001f);
        }
        assert(motion.rate <= rate + 0.001f);
        previous = level;
        assert(++ticks < 10000000U);
    }
    assert(previous == 0); /* Last pulse must finish before DONE. */
    assert(motion.position[0] == first && motion.position[1] == second);
    assert(rising_edges[0] == motion.delta[0] && rising_edges[1] == motion.delta[1]);
}

static void test_motion(void)
{
    test_one_motion(0, 0, 200.0f, 400.0f);
    test_one_motion(1, 0, 4000.0f, 100000.0f);
    test_one_motion(1, 1, 1.0f, 1.0f);
    test_one_motion(200, 73, 1000.0f, 1000.0f);
    test_one_motion(-81, 377, 4000.0f, 100000.0f);
    test_one_motion(991, -17, 200.0f, 400.0f);
    test_one_motion(0, -150, 4000.0f, 400.0f);
    for (int i = 1; i <= 20; ++i) {
        test_one_motion(i * 7 - 83, i * 13 - 150, (float)i * 83.0f, (float)i * 99.0f);
    }
    Motion motion; motion_init(&motion);
    const int32_t destination[2] = {500, -300};
    assert(!motion_start(&motion, destination, NAN, 400.0f));
    assert(!motion_start(&motion, destination, 5000.0f, 400.0f));
    assert(!motion_start(&motion, destination, 200.0f, 0.0f));
    assert(motion_start(&motion, destination, 200.0f, 400.0f));
    assert(!motion_start(&motion, destination, 200.0f, 400.0f));
    for (unsigned i = 0; i < 2000; ++i) { (void)motion_tick(&motion); }
    const int32_t stopped[2] = {motion.position[0], motion.position[1]};
    motion_abort(&motion);
    for (unsigned i = 0; i < 10000; ++i) { assert(motion_tick(&motion) == 0); }
    assert(motion.position[0] == stopped[0] && motion.position[1] == stopped[1]);
    assert(motion_start(&motion, destination, 200.0f, 400.0f));
    finish_motion(&motion);
    assert(motion.position[0] == 500 && motion.position[1] == -300);
    motion.position[0] = INT32_MIN;
    const int32_t huge[2] = {INT32_MAX, 0};
    assert(!motion_start(&motion, huge, 200.0f, 400.0f));
}

static CommandEffect command(Controller *controller, const char *line, const char *expected)
{
    char reply[REPLY_CAPACITY];
    const CommandEffect result = controller_command(controller, line, reply, sizeof(reply));
    if (strncmp(reply, expected, strlen(expected)) != 0) {
        fprintf(stderr, "Command '%s': got '%s', expected '%s'\n", line, reply, expected);
        assert(0);
    }
    return result;
}

static void test_controller(void)
{
    Controller controller; controller_init(&controller);
    command(&controller, "STATUS", "OK STATE=UNCONFIGURED");
    command(&controller, "ARM", "ERR configure_first");
    command(&controller, "MOVE 200 150", "ERR configure_first");
    command(&controller, "POSE 0 90", "ERR configure_first");
    command(&controller, "HOME", "ERR configure_first");
    command(&controller, "CONFIG 200 150 300 300", "OK CONFIGURED");
    command(&controller, "ARM", "ERR set_pose_first");
    command(&controller, "HOME", "ERR pose_and_arm_first");
    command(&controller, "CHECK 200 150", "OK CHECK Q1=0.000 Q2=90.000");
    command(&controller, "POSE 0 90", "OK POSE");
    command(&controller, "STATUS", "OK STATE=DISARMED Q1=0.000 Q2=90.000 X=200.000 Y=150.000");
    command(&controller, "MOVE 200 150", "ERR pose_and_arm_first");
    command(&controller, "ARM", "OK ARMED");
    command(&controller, "CONFIG 1 1 300 300", "ERR disarm_first");
    command(&controller, "MOVE 200 150", "OK DONE");
    command(&controller, "JOINT 6 96", "OK MOVING");
    assert(controller.motion.target[0] == 5 && controller.motion.target[1] == 5);
    command(&controller, "MOVE 210 150", "ERR busy");
    command(&controller, "STATUS", "OK STATE=MOVING");
    finish_motion(&controller.motion);
    float angles[2]; controller_angles(&controller, angles);
    near(angles[0], 6.0f, 0.001f); near(angles[1], 96.0f, 0.001f);
    command(&controller, "MOVE 200 150", "OK MOVING");
    finish_motion(&controller.motion);
    assert(controller.motion.position[0] == 0 && controller.motion.position[1] == 0);
    command(&controller, "JOINT -6 84", "OK MOVING");
    finish_motion(&controller.motion);
    command(&controller, "HOME", "OK MOVING Q1=0.000 Q2=90.000");
    assert(controller.motion.target[0] == 0 && controller.motion.target[1] == 0);
    finish_motion(&controller.motion);
    command(&controller, "HOME", "OK DONE");
    command(&controller, "MOVE 999 999", "ERR unreachable");
    assert(!controller.motion.active);
    command(&controller, "JOINT 100 100", "OK MOVING");
    for (unsigned i = 0; i < 10000; ++i) { (void)motion_tick(&controller.motion); }
    command(&controller, "STOP", "OK STOPPED");
    assert(!controller.armed && !controller.referenced && !controller.motion.active);
    command(&controller, "ARM", "ERR set_pose_first");
    command(&controller, "POSE 0 90", "OK POSE");
    command(&controller, "INVERT 1 0", "OK INVERT");
    assert(!controller.referenced && controller.inverted[0]);
    command(&controller, "POSE 0 90", "OK POSE");
    command(&controller, "LIMITS -10 10 80 100", "OK LIMITS");
    command(&controller, "LIMITS -10 10 -20 20", "ERR pose_outside_limits");
    command(&controller, "CHECK 350 0", "ERR joint_limits");
    command(&controller, "ARM", "OK ARMED");
    command(&controller, "POSE 0 90", "ERR disarm_first");
    command(&controller, "JOINT 0 110", "ERR joint_limits");
    command(&controller, "DISARM", "OK DISARMED");
    command(&controller, "CONFIG 200 150 400 400", "OK CONFIGURED");
    command(&controller, "POSE 0 90", "OK POSE");
    command(&controller, "LIMITS -10 10 80 90.8", "OK LIMITS");
    command(&controller, "ARM", "OK ARMED");
    command(&controller, "JOINT 0 90.8", "ERR quantized_joint_limits");

    const char *invalid[] = {
        "", "MOVE", "MOVE 1", "MOVE 1 2 3", "ARM 1", "POSE nan 90",
        "CONFIG 0 150 300 300", "CONFIG 200 150 0 300", "SPEED 5000 200",
        "SPEED 100 -1", "MOVE 1x 2", "MOVE inf 2", "MOVE 1e100 2",
        "INVERT 2 0", "UNKNOWN", "HELP 1 2 3 4 5 6 7 8"
    };
    for (unsigned i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
        command(&controller, invalid[i], "ERR");
        assert(!controller.motion.active);
    }
    char long_line[256]; memset(long_line, 'A', sizeof(long_line)); long_line[255] = '\0';
    command(&controller, long_line, "ERR line_too_long");
    command(&controller, "HELP", "OK HELP");
    char tiny[1]; controller_command(&controller, "STATUS", tiny, sizeof(tiny));
    assert(tiny[0] == '\0');
}

static void test_motor_jog(void)
{
    Controller controller; controller_init(&controller);
    near(controller.rate, 100.0f, 0.001f);
    command(&controller, "JOG 10", "ERR motorarm_first");
    command(&controller, "MOTORARM", "OK MOTOR_ARMED");
    assert(controller.motor_mode && controller.armed && !controller.referenced);
    command(&controller, "STATUS", "OK STATE=MOTOR_READY STEP1=0 STEP2=0 RATE=100.0");
    command(&controller, "MOTORARM", "ERR disarm_first");
    command(&controller, "JOG 1.5", "ERR jog_integer_range");
    command(&controller, "JOG 2001", "ERR jog_integer_range");
    command(&controller, "JOG 10", "OK MOVING STEP1=10");
    assert(controller.motion.target[1] == 0 && controller.motion.max_rate == 100.0f);
    command(&controller, "JOG -10", "ERR busy");
    finish_motion(&controller.motion);
    assert(controller.motion.position[0] == 10 && controller.motion.position[1] == 0);
    command(&controller, "JOG -20", "OK MOVING STEP1=-10");
    finish_motion(&controller.motion);
    assert(controller.motion.position[0] == -10 && controller.motion.position[1] == 0);
    command(&controller, "JOG 2 12", "OK MOVING STEP1=-10 STEP2=12");
    assert(controller.motion.delta[0] == 0 && controller.motion.delta[1] == 12);
    finish_motion(&controller.motion);
    assert(controller.motion.position[0] == -10 && controller.motion.position[1] == 12);
    command(&controller, "JOG 2 -24", "OK MOVING STEP1=-10 STEP2=-12");
    finish_motion(&controller.motion);
    assert(controller.motion.position[0] == -10 && controller.motion.position[1] == -12);
    command(&controller, "JOG 4 10", "ERR motor_use_1_2_or_3");
    command(&controller, "JOG 1.5 10", "ERR motor_use_1_2_or_3");
    command(&controller, "JOG 2 1.5", "ERR jog_integer_range");
    command(&controller, "JOG 2 2001", "ERR jog_integer_range");
    command(&controller, "JOG 0", "OK DONE STEP1=-10");
    command(&controller, "STOP", "OK STOPPED");
    command(&controller, "JOG 10", "ERR motorarm_first");
    command(&controller, "STATUS", "OK STATE=MOTOR_DISARMED");
    command(&controller, "MOTORARM", "OK MOTOR_ARMED");
    command(&controller, "DISARM", "OK DISARMED");
    command(&controller, "CONFIG 200 150 300 300", "OK CONFIGURED");
    command(&controller, "POSE 0 90", "OK POSE");
    assert(!controller.motor_mode);
    command(&controller, "MOTORARM", "OK MOTOR_ARMED");
    assert(!controller.referenced);
    command(&controller, "MOVE 200 150", "ERR pose_and_arm_first");
    command(&controller, "HOME", "ERR pose_and_arm_first");
    controller.motion.position[0] = INT32_MAX;
    command(&controller, "JOG 1", "ERR step_range");
    controller.motion.position[1] = INT32_MIN;
    command(&controller, "JOG 2 -1", "ERR step_range");

    /* Both motors share a single planner: all emitted pulses coincide. */
    motion_init(&controller.motion);
    controller.motion.position[0] = 5;
    controller.motion.position[1] = -8;
    command(&controller, "JOG 3 10", "OK MOVING STEP1=15 STEP2=2");
    unsigned pulse_count = 0;
    while (controller.motion.active) {
        const uint8_t levels = motion_tick(&controller.motion);
        assert(levels == 0 || levels == 3);
        if (levels == 3) { ++pulse_count; }
    }
    assert(pulse_count == 10);
    assert(controller.motion.position[0] == 15 && controller.motion.position[1] == 2);
    command(&controller, "JOG 3 -10", "OK MOVING STEP1=5 STEP2=-8");
    assert(controller.motion.direction[0] == -1 && controller.motion.direction[1] == -1);
    finish_motion(&controller.motion);
    assert(controller.motion.position[0] == 5 && controller.motion.position[1] == -8);
    command(&controller, "JOG 3 0", "OK DONE STEP1=5 STEP2=-8");
    controller.motion.position[1] = INT32_MAX;
    command(&controller, "JOG 3 1", "ERR step_range");
    assert(!controller.motion.active && controller.motion.position[0] == 5);
    assert(controller.motion.target[0] == 5); /* Overflow cannot partially start a move. */
}

int main(void)
{
    test_kinematics();
    test_motion();
    test_controller();
    test_motor_jog();
    puts("PASS: IK round trips, elbow branches, reach/limits, synchronized pulses, stop and serial commands");
    return 0;
}
