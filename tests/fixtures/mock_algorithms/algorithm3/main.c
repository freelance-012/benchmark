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
    static const char *roles[] = {
        "image_timestamps_path",
        "calibration_path",
        "left_image_dir",
        "right_image_dir",
    };
    const int role_count = (int)(sizeof(roles) / sizeof(roles[0]));
    double start = 0.0;
    double end = 0.0;

    if (argc != 4 + role_count && argc != 5 + role_count) {
        fprintf(stderr, "algorithm3 expects dataset root, start, end, four inputs and optional ground truth\n");
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

    int ok = emit(output, "algorithm", "algorithm3") &&
             emit(output, "dataset_type", "kitti") &&
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
    if (ok) {
        const char *ground_truth = argc == 5 + role_count ? argv[4 + role_count] : "<none>";
        ok = emit(output, "input.ground_truth_path", ground_truth);
    }
    if (fclose(output) != 0) {
        ok = 0;
    }
    return ok ? 0 : 5;
}
