#include "kinematics.h"

#include <math.h>

#define RAD_PER_DEG 0.017453292519943295f
#define DEG_PER_RAD 57.29577951308232f

static bool geometry_valid(const ArmGeometry *arm)
{
    return isfinite(arm->l1_mm) && arm->l1_mm > 0.0f && arm->l1_mm <= 10000.0f &&
           isfinite(arm->l2_mm) && arm->l2_mm > 0.0f && arm->l2_mm <= 10000.0f;
}

bool arm_angles_allowed(const ArmGeometry *arm, const float deg[2])
{
    for (unsigned i = 0; i < 2; ++i) {
        if (!isfinite(deg[i]) || !isfinite(arm->min_deg[i]) ||
            !isfinite(arm->max_deg[i]) || arm->min_deg[i] > arm->max_deg[i] ||
            deg[i] < arm->min_deg[i] || deg[i] > arm->max_deg[i]) {
            return false;
        }
    }
    return true;
}

void arm_forward(const ArmGeometry *arm, const float deg[2], float xy_mm[2])
{
    const float shoulder = deg[0] * RAD_PER_DEG;
    const float tool = (deg[0] + deg[1]) * RAD_PER_DEG;
    xy_mm[0] = arm->l1_mm * cosf(shoulder) + arm->l2_mm * cosf(tool);
    xy_mm[1] = arm->l1_mm * sinf(shoulder) + arm->l2_mm * sinf(tool);
}

IkResult arm_inverse(const ArmGeometry *arm, const float xy_mm[2], int branch,
                     const float near_deg[2], float deg[2])
{
    if (!geometry_valid(arm) || !isfinite(xy_mm[0]) || !isfinite(xy_mm[1]) ||
        !isfinite(near_deg[0]) || !isfinite(near_deg[1]) ||
        (branch != 1 && branch != -1)) {
        return IK_INVALID;
    }
    const float radius = hypotf(xy_mm[0], xy_mm[1]);
    const float outer = arm->l1_mm + arm->l2_mm;
    const float inner = fabsf(arm->l1_mm - arm->l2_mm);
    const float tolerance = 0.00001f * outer;
    if (radius > outer + tolerance || radius < inner - tolerance) {
        return IK_UNREACHABLE;
    }
    if (radius < 0.00001f && inner < 0.00001f) {
        return IK_SINGULAR; /* Folded equal arms at the origin have no unique shoulder. */
    }
    float c2 = (radius * radius - arm->l1_mm * arm->l1_mm -
                arm->l2_mm * arm->l2_mm) / (2.0f * arm->l1_mm * arm->l2_mm);
    c2 = fmaxf(-1.0f, fminf(1.0f, c2));
    const float elbow = (float)branch * acosf(c2);
    const float shoulder = atan2f(xy_mm[1], xy_mm[0]) -
        atan2f(arm->l2_mm * sinf(elbow), arm->l1_mm + arm->l2_mm * cosf(elbow));
    const float base = shoulder * DEG_PER_RAD;
    const float turns = roundf((near_deg[0] - base) / 360.0f);
    float best_distance = INFINITY;
    bool found = false;
    for (int offset = -1; offset <= 1; ++offset) {
        const float candidate[2] = {
            base + 360.0f * (turns + (float)offset), elbow * DEG_PER_RAD
        };
        const float distance = fabsf(candidate[0] - near_deg[0]);
        if (arm_angles_allowed(arm, candidate) && distance < best_distance) {
            deg[0] = candidate[0];
            deg[1] = candidate[1];
            best_distance = distance;
            found = true;
        }
    }
    return found ? IK_OK : IK_LIMIT;
}
