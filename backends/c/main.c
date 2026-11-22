#define _POSIX_C_SOURCE 200809L
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int compare_i64(const void *left, const void *right) {
    int64_t a = *(const int64_t *)left;
    int64_t b = *(const int64_t *)right;
    return (a > b) - (a < b);
}

static void sift_down(int64_t *values, size_t root, size_t end) {
    while (root * 2 + 1 < end) {
        size_t child = root * 2 + 1;
        if (child + 1 < end && values[child] < values[child + 1]) {
            child++;
        }
        if (values[root] >= values[child]) {
            return;
        }
        int64_t temporary = values[root];
        values[root] = values[child];
        values[child] = temporary;
        root = child;
    }
}

static void heap_sort(int64_t *values, size_t length) {
    for (size_t root = length / 2; root > 0; root--) {
        sift_down(values, root - 1, length);
    }
    for (size_t end = length; end > 1; end--) {
        int64_t temporary = values[0];
        values[0] = values[end - 1];
        values[end - 1] = temporary;
        sift_down(values, 0, end - 1);
    }
}

static void shell_sort(int64_t *values, size_t length) {
    for (size_t gap = length / 2; gap > 0; gap /= 2) {
        for (size_t index = gap; index < length; index++) {
            int64_t value = values[index];
            size_t cursor = index;
            while (cursor >= gap && values[cursor - gap] > value) {
                values[cursor] = values[cursor - gap];
                cursor -= gap;
            }
            values[cursor] = value;
        }
    }
}

static uint64_t elapsed_ns(struct timespec start, struct timespec end) {
    return (uint64_t)(end.tv_sec - start.tv_sec) * UINT64_C(1000000000)
        + (uint64_t)(end.tv_nsec - start.tv_nsec);
}

int main(void) {
    char strategy[32];
    size_t count = 0;
    if (scanf("%31s %zu", strategy, &count) != 2 || count > 100000) {
        fputs("invalid request header\n", stderr);
        return 2;
    }

    int64_t *values = malloc((count == 0 ? 1 : count) * sizeof(*values));
    if (values == NULL) {
        fputs("allocation failed\n", stderr);
        return 3;
    }
    for (size_t index = 0; index < count; index++) {
        if (scanf("%" SCNd64, &values[index]) != 1) {
            free(values);
            fputs("invalid integer input\n", stderr);
            return 2;
        }
    }

    struct timespec start;
    struct timespec end;
    if (clock_gettime(CLOCK_MONOTONIC, &start) != 0) {
        free(values);
        return 4;
    }
    if (strcmp(strategy, "builtin") == 0) {
        qsort(values, count, sizeof(*values), compare_i64);
    } else if (strcmp(strategy, "heapsort") == 0) {
        heap_sort(values, count);
    } else if (strcmp(strategy, "shellsort") == 0) {
        shell_sort(values, count);
    } else {
        free(values);
        fputs("unsupported strategy\n", stderr);
        return 2;
    }
    if (clock_gettime(CLOCK_MONOTONIC, &end) != 0) {
        free(values);
        return 4;
    }

    printf("%" PRIu64 "\n", elapsed_ns(start, end));
    for (size_t index = 0; index < count; index++) {
        if (index > 0) {
            putchar(' ');
        }
        printf("%" PRId64, values[index]);
    }
    putchar('\n');
    free(values);
    return 0;
}
