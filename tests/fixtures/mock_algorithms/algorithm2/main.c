#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

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

int main(int argc, char **argv) {
    static const char *roles[] = {
        "imu_path",
        "image_path",
        "image_timestamps_path",
        "calibration_path",
    };
    const int role_count = (int)(sizeof(roles) / sizeof(roles[0]));
    double start = 0.0;
    double end = 0.0;

    if (argc != 4 + role_count) {
        fprintf(stderr, "algorithm2 expects dataset root, start, end and %d inputs\n", role_count);
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

    int ok = emit(output, "algorithm", "algorithm2") &&
             emit(output, "dataset_type", "rk3399") &&
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
