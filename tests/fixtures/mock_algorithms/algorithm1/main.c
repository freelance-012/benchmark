#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#define OUTPUT_FILENAME "mock_output.txt"

static int parse_number(const char *text, double *value) {
    char *end = NULL;
    errno = 0;
    *value = strtod(text, &end);
    return errno == 0 && end != text && *end == '\0' && isfinite(*value);
}

static int emit(FILE *output, const char *key, const char *value) {
    if (fprintf(stdout, "%s=%s\n", key, value) < 0) {
        return 0;
    }
    return fprintf(output, "%s=%s\n", key, value) >= 0;
}

int main(int argc, char **argv) {
    static const char *rk3588_roles[] = {
        "imu_path",
        "bottom_video_0_path",
        "bottom_video_1_path",
        "front_video_0_path",
        "front_video_1_path",
        "bottom_image_timestamps_path",
        "front_image_timestamps_path",
        "bottom_calibration_path",
        "front_calibration_path",
    };
    static const char *rk3399_roles[] = {
        "imu_path",
        "image_path",
        "image_timestamps_path",
        "calibration_path",
    };
    const int rk3588_role_count = (int)(sizeof(rk3588_roles) / sizeof(rk3588_roles[0]));
    const int rk3399_role_count = (int)(sizeof(rk3399_roles) / sizeof(rk3399_roles[0]));
    const char **roles = NULL;
    const char *dataset_type = NULL;
    int role_count = 0;
    double start = 0.0;
    double end = 0.0;

    if (argc == 4 + rk3588_role_count) {
        roles = rk3588_roles;
        dataset_type = "rk3588";
        role_count = rk3588_role_count;
    } else if (argc == 4 + rk3399_role_count) {
        roles = rk3399_roles;
        dataset_type = "rk3399";
        role_count = rk3399_role_count;
    } else {
        fprintf(stderr, "algorithm1 expects 4 RK3399 inputs or 9 RK3588 inputs\n");
        return 2;
    }
    if (!parse_number(argv[2], &start) || !parse_number(argv[3], &end) || end < start) {
        fprintf(stderr, "invalid Segment timestamp range\n");
        return 3;
    }

    FILE *output = fopen(OUTPUT_FILENAME, "w");
    if (output == NULL) {
        perror(OUTPUT_FILENAME);
        return 4;
    }

    int ok = emit(output, "algorithm", "algorithm1") &&
             emit(output, "dataset_type", dataset_type) &&
             emit(output, "dataset_root", argv[1]) &&
             emit(output, "segment_start", argv[2]) &&
             emit(output, "segment_end", argv[3]);
    for (int index = 0; ok && index < role_count; ++index) {
        char key[128];
        if (snprintf(key, sizeof(key), "input.%s", roles[index]) < 0) {
            ok = 0;
            break;
        }
        ok = emit(output, key, argv[4 + index]);
    }
    if (fclose(output) != 0) {
        ok = 0;
    }
    return ok ? 0 : 5;
}
