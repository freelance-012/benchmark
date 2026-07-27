#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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

typedef struct {
    const char *dataset_root;
    const char *start_text;
    const char *end_text;
    const char *inputs[4];
    const char *ground_truth;
} RunArguments;

static int assign_once(const char **target, const char *value) {
    if (*target != NULL || value[0] == '\0') {
        return 0;
    }
    *target = value;
    return 1;
}

static int parse_prefixed_arguments(
    int argc,
    char **argv,
    RunArguments *arguments
) {
    if ((argc - 1) % 2 != 0) {
        return 0;
    }
    for (int index = 1; index < argc; index += 2) {
        const char *flag = argv[index];
        const char *value = argv[index + 1];
        int accepted = 0;
        if (strcmp(flag, "--dataset") == 0) {
            accepted = assign_once(&arguments->dataset_root, value);
        } else if (strcmp(flag, "--start") == 0) {
            accepted = assign_once(&arguments->start_text, value);
        } else if (strcmp(flag, "--end") == 0) {
            accepted = assign_once(&arguments->end_text, value);
        } else if (strcmp(flag, "--timestamps") == 0) {
            accepted = assign_once(&arguments->inputs[0], value);
        } else if (strcmp(flag, "--calibration") == 0) {
            accepted = assign_once(&arguments->inputs[1], value);
        } else if (strcmp(flag, "--left-images") == 0) {
            accepted = assign_once(&arguments->inputs[2], value);
        } else if (strcmp(flag, "--right-images") == 0) {
            accepted = assign_once(&arguments->inputs[3], value);
        } else if (strcmp(flag, "--ground-truth") == 0) {
            accepted = assign_once(&arguments->ground_truth, value);
        }
        if (!accepted) {
            return 0;
        }
    }
    return arguments->dataset_root != NULL &&
           arguments->start_text != NULL &&
           arguments->end_text != NULL &&
           arguments->inputs[0] != NULL &&
           arguments->inputs[1] != NULL &&
           arguments->inputs[2] != NULL &&
           arguments->inputs[3] != NULL;
}

int main(int argc, char **argv) {
    static const char *roles[] = {
        "image_timestamps_path",
        "calibration_path",
        "left_image_dir",
        "right_image_dir",
    };
    const int role_count = (int)(sizeof(roles) / sizeof(roles[0]));
    RunArguments arguments = {0};
    double start = 0.0;
    double end = 0.0;

    if (argc > 1 && strncmp(argv[1], "--", 2) == 0) {
        if (!parse_prefixed_arguments(argc, argv, &arguments)) {
            fprintf(stderr, "algorithm3 received invalid prefixed arguments\n");
            return 2;
        }
    } else {
        if (argc != 4 + role_count && argc != 5 + role_count) {
            fprintf(stderr, "algorithm3 expects positional inputs or named pairs\n");
            return 2;
        }
        arguments.dataset_root = argv[1];
        arguments.start_text = argv[2];
        arguments.end_text = argv[3];
        for (int index = 0; index < role_count; ++index) {
            arguments.inputs[index] = argv[4 + index];
        }
        if (argc == 5 + role_count) {
            arguments.ground_truth = argv[4 + role_count];
        }
    }
    if (arguments.ground_truth == NULL) {
        arguments.ground_truth = "<none>";
    }
    if (!parse_number(arguments.start_text, &start) ||
        !parse_number(arguments.end_text, &end) || end < start) {
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
             emit(output, "dataset_root", arguments.dataset_root) &&
             emit(output, "segment_start", arguments.start_text) &&
             emit(output, "segment_end", arguments.end_text);
    for (int index = 0; ok && index < role_count; ++index) {
        char key[128];
        if (snprintf(key, sizeof(key), "input.%s", roles[index]) < 0) {
            ok = 0;
            break;
        }
        ok = emit(output, key, arguments.inputs[index]);
    }
    if (ok) {
        ok = emit(output, "input.ground_truth_path", arguments.ground_truth);
    }
    if (fclose(output) != 0) {
        ok = 0;
    }
    return ok ? 0 : 5;
}
