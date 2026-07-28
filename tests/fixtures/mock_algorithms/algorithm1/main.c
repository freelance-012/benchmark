#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

#define OUTPUT_FILENAME "mock_output.txt"
#define OUTPUT_ROOT "../algorithm1_output"
#define COUNTER_FILENAME "counter.yaml"
#define COUNTER_TEMP_FILENAME "counter.yaml.tmp"
#define PATH_BUFFER_SIZE 4096

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

static unsigned int progress_delay_seconds(void) {
    const char *text = getenv("BENCHMARK_MOCK_PROGRESS_DELAY_SECONDS");
    char *end = NULL;
    unsigned long value = 0;
    if (text == NULL || *text == '\0') {
        return 0;
    }
    errno = 0;
    value = strtoul(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value > 60) {
        return 0;
    }
    return (unsigned int)value;
}

static int emit_progress(double start, double end) {
    const int step_count = 4;
    const unsigned int delay_seconds = progress_delay_seconds();
    for (int step = 0; step <= step_count; ++step) {
        const double fraction = (double)step / (double)step_count;
        const double timestamp = start + (end - start) * fraction;
        if (fprintf(
                stdout,
                "BENCHMARK_PROGRESS "
                "{\"timestamp\":%.6f,\"percent\":%.1f,\"fps\":30.0}\n",
                timestamp,
                fraction * 100.0
            ) < 0 ||
            fflush(stdout) != 0) {
            return 0;
        }
        if (delay_seconds > 0 && step < step_count) {
            sleep(delay_seconds);
        }
    }
    return 1;
}

static int ensure_directory(const char *path) {
    struct stat status;

    if (mkdir(path, 0775) == 0) {
        return 1;
    }
    if (errno != EEXIST) {
        perror(path);
        return 0;
    }
    if (stat(path, &status) != 0) {
        perror(path);
        return 0;
    }
    if (!S_ISDIR(status.st_mode)) {
        fprintf(stderr, "output root is not a directory: %s\n", path);
        return 0;
    }
    return 1;
}

static int read_last_completed(const char *counter_path, long *value) {
    FILE *counter = fopen(counter_path, "r");
    if (counter == NULL) {
        if (errno == ENOENT) {
            *value = -1;
            return 1;
        }
        perror(counter_path);
        return 0;
    }

    char line[128];
    char extra = '\0';
    int valid = fgets(line, sizeof(line), counter) != NULL &&
                sscanf(line, "last_completed: %ld %c", value, &extra) == 1 &&
                *value >= -1;
    if (valid) {
        int character = 0;
        while ((character = fgetc(counter)) != EOF) {
            if (!isspace((unsigned char)character)) {
                valid = 0;
                break;
            }
        }
    }
    if (fclose(counter) != 0) {
        valid = 0;
    }
    if (!valid) {
        fprintf(stderr, "invalid counter file: %s\n", counter_path);
    }
    return valid;
}

static int write_counter(const char *counter_path, long value) {
    char temporary_path[PATH_BUFFER_SIZE];
    int temporary_length = snprintf(
        temporary_path,
        sizeof(temporary_path),
        "%s/%s",
        OUTPUT_ROOT,
        COUNTER_TEMP_FILENAME
    );
    if (temporary_length < 0 ||
        (size_t)temporary_length >= sizeof(temporary_path)) {
        fprintf(stderr, "counter temporary path is too long\n");
        return 0;
    }

    FILE *counter = fopen(temporary_path, "w");
    if (counter == NULL) {
        perror(temporary_path);
        return 0;
    }
    int ok = fprintf(counter, "last_completed: %ld\n", value) >= 0;
    if (fclose(counter) != 0) {
        ok = 0;
    }
    if (!ok) {
        unlink(temporary_path);
        return 0;
    }
    if (rename(temporary_path, counter_path) != 0) {
        perror(counter_path);
        unlink(temporary_path);
        return 0;
    }
    return 1;
}

static void remove_incomplete_output(
    const char *output_path,
    const char *directory_path
) {
    unlink(output_path);
    rmdir(directory_path);
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
    if (!emit_progress(start, end)) {
        fprintf(stderr, "cannot emit algorithm progress\n");
        return 5;
    }

    if (!ensure_directory(OUTPUT_ROOT)) {
        return 4;
    }

    char counter_path[PATH_BUFFER_SIZE];
    int counter_length = snprintf(
        counter_path,
        sizeof(counter_path),
        "%s/%s",
        OUTPUT_ROOT,
        COUNTER_FILENAME
    );
    if (counter_length < 0 || (size_t)counter_length >= sizeof(counter_path)) {
        fprintf(stderr, "counter path is too long\n");
        return 4;
    }

    long last_completed = -1;
    if (!read_last_completed(counter_path, &last_completed) ||
        last_completed == LONG_MAX) {
        return 4;
    }
    long output_index = last_completed + 1;

    char temporary_directory[PATH_BUFFER_SIZE];
    char output_directory[PATH_BUFFER_SIZE];
    char output_path[PATH_BUFFER_SIZE];
    int temporary_length = snprintf(
        temporary_directory,
        sizeof(temporary_directory),
        "%s/.%ld.tmp",
        OUTPUT_ROOT,
        output_index
    );
    int directory_length = snprintf(
        output_directory,
        sizeof(output_directory),
        "%s/%ld",
        OUTPUT_ROOT,
        output_index
    );
    int output_length = snprintf(
        output_path,
        sizeof(output_path),
        "%s/%s",
        temporary_directory,
        OUTPUT_FILENAME
    );
    if (temporary_length < 0 ||
        (size_t)temporary_length >= sizeof(temporary_directory) ||
        directory_length < 0 ||
        (size_t)directory_length >= sizeof(output_directory) ||
        output_length < 0 ||
        (size_t)output_length >= sizeof(output_path)) {
        fprintf(stderr, "numbered output path is too long\n");
        return 4;
    }

    struct stat existing_output;
    if (stat(output_directory, &existing_output) == 0) {
        fprintf(stderr, "numbered output already exists: %s\n", output_directory);
        return 4;
    }
    if (errno != ENOENT) {
        perror(output_directory);
        return 4;
    }
    if (mkdir(temporary_directory, 0775) != 0) {
        perror(temporary_directory);
        return 4;
    }

    FILE *output = fopen(output_path, "w");
    if (output == NULL) {
        perror(output_path);
        remove_incomplete_output(output_path, temporary_directory);
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
    if (!ok) {
        remove_incomplete_output(output_path, temporary_directory);
        return 5;
    }
    if (rename(temporary_directory, output_directory) != 0) {
        perror(output_directory);
        remove_incomplete_output(output_path, temporary_directory);
        return 5;
    }
    if (!write_counter(counter_path, output_index)) {
        char completed_output_path[PATH_BUFFER_SIZE];
        int completed_length = snprintf(
            completed_output_path,
            sizeof(completed_output_path),
            "%s/%s",
            output_directory,
            OUTPUT_FILENAME
        );
        if (completed_length >= 0 &&
            (size_t)completed_length < sizeof(completed_output_path)) {
            remove_incomplete_output(completed_output_path, output_directory);
        }
        return 5;
    }

    fprintf(stdout, "output_index=%ld\n", output_index);
    fprintf(stdout, "output_directory=%s\n", output_directory);
    fprintf(stdout, "counter_path=%s\n", counter_path);
    return 0;
}
