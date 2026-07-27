#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define OUTPUT_FILENAME "mock_output.txt"
#define HOME_POINT_FILENAME "home_point.txt"

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

static const char *prefixed_value(const char *argument, const char *prefix) {
    size_t prefix_length = strlen(prefix);
    if (strncmp(argument, prefix, prefix_length) != 0 ||
        argument[prefix_length] == '\0') {
        return NULL;
    }
    return argument + prefix_length;
}

int main(int argc, char **argv) {
    static const char *roles[] = {
        "imu_path",
        "image_path",
        "image_timestamps_path",
        "calibration_path",
    };
    const int role_count = (int)(sizeof(roles) / sizeof(roles[0]));
    const char *dataset_root = NULL;
    const char *start_text = NULL;
    const char *end_text = NULL;
    const char *inputs[4] = {NULL, NULL, NULL, NULL};
    double start = 0.0;
    double end = 0.0;

    if (argc != 4 + role_count) {
        fprintf(stderr, "algorithm2 expects seven run arguments\n");
        return 2;
    }
    if (strncmp(argv[1], "--", 2) == 0) {
        dataset_root = prefixed_value(argv[1], "--log=");
        start_text = prefixed_value(argv[2], "--start_time=");
        end_text = prefixed_value(argv[3], "--end_time=");
        inputs[0] = prefixed_value(argv[4], "--imu=");
        inputs[1] = prefixed_value(argv[5], "--image=");
        inputs[2] = prefixed_value(argv[6], "--timestamps=");
        inputs[3] = prefixed_value(argv[7], "--calibration=");
        if (dataset_root == NULL || start_text == NULL || end_text == NULL ||
            inputs[0] == NULL || inputs[1] == NULL || inputs[2] == NULL ||
            inputs[3] == NULL) {
            fprintf(stderr, "algorithm2 received invalid prefixed arguments\n");
            return 2;
        }
    } else {
        dataset_root = argv[1];
        start_text = argv[2];
        end_text = argv[3];
        for (int index = 0; index < role_count; ++index) {
            inputs[index] = argv[4 + index];
        }
    }
    if (!parse_number(start_text, &start) || !parse_number(end_text, &end) ||
        end < start) {
        fprintf(stderr, "invalid Segment timestamp range\n");
        return 3;
    }

    FILE *output = fopen(OUTPUT_FILENAME, "w");
    if (output == NULL) {
        perror(OUTPUT_FILENAME);
        return 4;
    }

    int ok = emit(output, "algorithm", "algorithm2") &&
             emit(output, "dataset_type", "rk3399") &&
             emit(output, "dataset_root", dataset_root) &&
             emit(output, "segment_start", start_text) &&
             emit(output, "segment_end", end_text);
    for (int index = 0; ok && index < role_count; ++index) {
        char key[128];
        if (snprintf(key, sizeof(key), "input.%s", roles[index]) < 0) {
            ok = 0;
            break;
        }
        ok = emit(output, key, inputs[index]);
    }
    if (fclose(output) != 0) {
        ok = 0;
    }
    if (ok) {
        FILE *home_point = fopen(HOME_POINT_FILENAME, "w");
        if (home_point == NULL) {
            perror(HOME_POINT_FILENAME);
            ok = 0;
        } else {
            int home_point_ok =
                fputs("121.2 31.1 51.0\n", home_point) >= 0;
            if (fclose(home_point) != 0) {
                home_point_ok = 0;
            }
            if (!home_point_ok) {
                ok = 0;
            }
        }
    }
    return ok ? 0 : 5;
}
