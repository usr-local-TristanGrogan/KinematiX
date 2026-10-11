#ifndef KINEMATICS_H
#define KINEMATICS_H

#include <stdbool.h>

typedef struct {
    float l1_mm;
    float l2_mm;
    float min_deg[2];
    float max_deg[2];
} ArmGeometry;

typedef enum {
    IK_OK,
    IK_INVALID,
    IK_UNREACHABLE,
    IK_SINGULAR,
    IK_LIMIT
} IkResult;

/* Shoulder is relative to +X. Elbow is relative to arm 1. Positive is CCW. */
bool arm_angles_allowed(const ArmGeometry *arm, const float deg[2]);
void arm_forward(const ArmGeometry *arm, const float deg[2], float xy_mm[2]);
/* Fix the elbow branch (+1 or -1), keeping shoulder rotation near its pose. */
IkResult arm_inverse(const ArmGeometry *arm, const float xy_mm[2], int branch,
                     const float near_deg[2], float deg[2]);

#endif
