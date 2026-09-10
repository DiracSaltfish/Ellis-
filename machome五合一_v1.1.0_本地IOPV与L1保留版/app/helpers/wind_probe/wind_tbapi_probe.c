#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define JAVA_MODULE_SIZE 0x28
#define GLOBAL_MODULE_OFFSET 0xB0568ULL

typedef void *(*cj_init_fn)(const void *options);
typedef int64_t (*cj_create_sub_fn)(void *module, const char *sql, bool coord);
typedef int64_t (*cj_modify_sub_fn)(void *module, int64_t sub_id, const char *sql);
typedef void (*cj_register_cb_fn)(void *module, void *callback);
typedef int64_t (*cj_terminate_sub_fn)(void *module, int64_t sub_id);

static unsigned char g_module[JAVA_MODULE_SIZE];
static int64_t g_sub_id = -1;
static uint64_t g_callback_count = 0;
static char g_windcode[32] = "";
static char g_safe_code[32] = "";
static char g_result_dir[768] = "";
static cj_modify_sub_fn g_modify_sub = NULL;
static cj_terminate_sub_fn g_terminate_sub = NULL;

static void write_all(int fd, const void *data, size_t size) {
    const unsigned char *p = (const unsigned char *)data;
    while (size > 0) {
        ssize_t n = write(fd, p, size);
        if (n < 0) { if (errno == EINTR) continue; return; }
        p += (size_t)n;
        size -= (size_t)n;
    }
}

static void write_text(int fd, const char *text) { write_all(fd, text, strlen(text)); }

static void write_json_string(int fd, const char *text) {
    write_text(fd, "\"");
    if (text) {
        for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
            char buffer[8];
            switch (*p) {
                case '\\': write_text(fd, "\\\\"); break;
                case '"': write_text(fd, "\\\""); break;
                case '\n': write_text(fd, "\\n"); break;
                case '\r': write_text(fd, "\\r"); break;
                case '\t': write_text(fd, "\\t"); break;
                default:
                    if (*p < 0x20) {
                        int n = snprintf(buffer, sizeof(buffer), "\\u%04x", *p);
                        write_all(fd, buffer, (size_t)n);
                    } else write_all(fd, p, 1);
            }
        }
    }
    write_text(fd, "\"");
}

static void write_hex(int fd, const void *data, size_t size) {
    static const char digits[] = "0123456789abcdef";
    const unsigned char *p = (const unsigned char *)data;
    char buffer[4096];
    size_t used = 0;
    for (size_t i = 0; i < size; ++i) {
        buffer[used++] = digits[p[i] >> 4];
        buffer[used++] = digits[p[i] & 0x0f];
        if (used == sizeof(buffer)) { write_all(fd, buffer, used); used = 0; }
    }
    if (used) write_all(fd, buffer, used);
}

static uint32_t load_u32(const unsigned char *p) {
    uint32_t value;
    memcpy(&value, p, sizeof(value));
    return value;
}

static uintptr_t load_ptr(const unsigned char *p) {
    uintptr_t value;
    memcpy(&value, p, sizeof(value));
    return value;
}

static long long epoch_milliseconds(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_REALTIME, &value) != 0) return 0;
    return (long long)value.tv_sec * 1000LL + value.tv_nsec / 1000000LL;
}

static bool valid_windcode(const char *code) {
    if (!code || strlen(code) != 9) return false;
    for (int i = 0; i < 6; ++i) if (code[i] < '0' || code[i] > '9') return false;
    return code[6] == '.' && code[7] == 'S' && (code[8] == 'Z' || code[8] == 'H');
}

static void set_code(const char *code) {
    snprintf(g_windcode, sizeof(g_windcode), "%s", code);
    snprintf(g_safe_code, sizeof(g_safe_code), "%s", code);
    for (char *p = g_safe_code; *p; ++p) if (*p == '.') *p = '_';
}

static bool result_path(char *output, size_t size, const char *suffix) {
    if (!g_result_dir[0] || !g_safe_code[0]) return false;
    const int n = snprintf(output, size, "%s/wind_tbapi_live_%s%s.json",
                           g_result_dir, g_safe_code, suffix);
    return n > 0 && (size_t)n < size;
}

static void write_status(const char *status, int64_t code, const char *message) {
    char final_path[1024], temp_path[1080];
    if (!result_path(final_path, sizeof(final_path), "_status")) return;
    snprintf(temp_path, sizeof(temp_path), "%s.%d.tmp", final_path, getpid());
    int fd = open(temp_path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0600);
    if (fd < 0) return;
    char head[512];
    int n = snprintf(head, sizeof(head),
        "{\"status\":\"%s\",\"code\":%lld,\"windcode\":\"%s\","
        "\"pid\":%d,\"epoch_ms\":%lld,\"message\":",
        status, (long long)code, g_windcode, getpid(), epoch_milliseconds());
    write_all(fd, head, (size_t)n);
    write_json_string(fd, message ? message : "");
    write_text(fd, "}\n");
    close(fd);
    (void)rename(temp_path, final_path);
}

static void subscription_callback(uint32_t sub_id, int32_t error_code,
                                  const char *error_message, const void *frame_ptr) {
    g_callback_count++;
    char final_path[1024], temp_path[1080];
    if (!result_path(final_path, sizeof(final_path), "")) return;
    snprintf(temp_path, sizeof(temp_path), "%s.%d.tmp", final_path, getpid());
    int fd = open(temp_path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0600);
    if (fd < 0) return;
    char head[768];
    int n = snprintf(head, sizeof(head),
        "{\"status\":\"subscription_push\",\"callback_seq\":%llu,"
        "\"sub_id\":%u,\"source_pid\":%d,\"callback_epoch_ms\":%lld,"
        "\"requested_windcode\":\"%s\",\"error_code\":%d,\"error_message\":",
        (unsigned long long)g_callback_count, sub_id, getpid(),
        epoch_milliseconds(), g_windcode, error_code);
    write_all(fd, head, (size_t)n);
    write_json_string(fd, error_message ? error_message : "");
    if (!frame_ptr) { write_text(fd, ",\"frame\":null}\n"); close(fd); (void)rename(temp_path, final_path); return; }

    const unsigned char *frame = (const unsigned char *)frame_ptr;
    const uint32_t field_size = load_u32(frame + 0x2c);
    const uint32_t data_size = load_u32(frame + 0x48);
    const uintptr_t field_ptr = load_ptr(frame + 0x40);
    const uintptr_t data_ptr = load_ptr(frame + 0x58);
    if (!field_ptr || !data_ptr || field_size > 16u * 1024u * 1024u
        || data_size > 16u * 1024u * 1024u) {
        write_text(fd, ",\"frame_error\":\"invalid buffer bounds\"}\n");
        close(fd); (void)rename(temp_path, final_path); return;
    }
    write_text(fd, ",\"field_info\":{\"size\":");
    char number[32];
    n = snprintf(number, sizeof(number), "%u", field_size); write_all(fd, number, (size_t)n);
    write_text(fd, ",\"hex\":\""); write_hex(fd, (const void *)field_ptr, field_size);
    write_text(fd, "\"},\"buffer_58\":{\"size\":");
    n = snprintf(number, sizeof(number), "%u", data_size); write_all(fd, number, (size_t)n);
    write_text(fd, ",\"hex\":\""); write_hex(fd, (const void *)data_ptr, data_size);
    write_text(fd, "\"}}\n");
    close(fd);
    (void)rename(temp_path, final_path);
}

static int initialise_module(void) {
    const char *path = "/Applications/WindPersonFree.app/Contents/Frameworks/libWind.Cosmos.TBAPI2.dylib";
    void *tbapi = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!tbapi) return 101;
    cj_init_fn init = (cj_init_fn)dlsym(tbapi, "CJAVAInit");
    cj_register_cb_fn register_cb = (cj_register_cb_fn)dlsym(tbapi, "CJAVARegisterQueryCallBack");
    g_modify_sub = (cj_modify_sub_fn)dlsym(tbapi, "CJAVAModifySubscription");
    g_terminate_sub = (cj_terminate_sub_fn)dlsym(tbapi, "CJAVATerminateSubscription");
    if (!init || !register_cb || !g_modify_sub || !g_terminate_sub) return 102;
    void *wind_module = NULL;
    Dl_info info;
    if (dladdr((const void *)init, &info) && info.dli_fbase) {
        uintptr_t *global_ptr = (uintptr_t *)((uintptr_t)info.dli_fbase + GLOBAL_MODULE_OFFSET);
        wind_module = (void *)*global_ptr;
    }
    if (!wind_module) {
        unsigned char options[0x40] = {0};
        wind_module = init(options);
    }
    if (!wind_module) return 103;
    memcpy(g_module, wind_module, JAVA_MODULE_SIZE);
    register_cb(g_module, (void *)subscription_callback);
    *(void **)(g_module + 0x00) = (void *)subscription_callback;
    return 0;
}

extern "C" __attribute__((visibility("default")))
int64_t wind_tbapi_set_output_dir(const char *path) {
    if (!path || path[0] != '/' || strlen(path) >= sizeof(g_result_dir)) return -21001;
    snprintf(g_result_dir, sizeof(g_result_dir), "%s", path);
    while (strlen(g_result_dir) > 1 && g_result_dir[strlen(g_result_dir) - 1] == '/')
        g_result_dir[strlen(g_result_dir) - 1] = '\0';
    return 0;
}

extern "C" __attribute__((visibility("default")))
int64_t wind_tbapi_subscribe(const char *windcode, int latency_ms) {
    if (!valid_windcode(windcode)) return -20001;
    if (latency_ms < 100 || latency_ms > 60000) return -20002;
    if (!g_result_dir[0]) return -21002;
    set_code(windcode);
    int init_result = initialise_module();
    if (init_result != 0) { write_status("initialise_failed", init_result, "TBAPI2 module unavailable"); return -init_result; }
    if (g_sub_id >= 0 && g_terminate_sub) { (void)g_terminate_sub(g_module, g_sub_id); g_sub_id = -1; }
    void *tbapi = dlopen("/Applications/WindPersonFree.app/Contents/Frameworks/libWind.Cosmos.TBAPI2.dylib", RTLD_NOW | RTLD_LOCAL);
    cj_create_sub_fn create_sub = (cj_create_sub_fn)dlsym(tbapi, "CJAVACreateSubscription");
    if (!create_sub) return -102;
    const char *create_sql =
        "SELECT etfbuynumber, etfbuyamount, etfbuymoney, etfsellnumber, etfsellamount, etfsellmoney "
        "FROM ETFComprehensive.WholeETFData WHERE windcode = '' LATENCY(500 MS)";
    g_callback_count = 0;
    g_sub_id = create_sub(g_module, create_sql, false);
    write_status(g_sub_id >= 0 ? "create_empty" : "create_failed", g_sub_id, create_sql);
    if (g_sub_id < 0) return g_sub_id;
    char sql[512];
    int n = snprintf(sql, sizeof(sql),
        "SELECT etfbuynumber, etfbuyamount, etfbuymoney, etfsellnumber, etfsellamount, etfsellmoney "
        "FROM ETFComprehensive.WholeETFData WHERE windcode = '%s' LATENCY(500 MS)", windcode);
    if (n <= 0 || (size_t)n >= sizeof(sql)) return -20003;
    int64_t result = g_modify_sub(g_module, g_sub_id, sql);
    write_status(result >= 0 ? "modify_target" : "modify_failed", result, sql);
    return result;
}

extern "C" __attribute__((visibility("default")))
int64_t wind_tbapi_stop(void) {
    if (g_sub_id < 0) return 0;
    if (!g_terminate_sub) return -102;
    int64_t result = g_terminate_sub(g_module, g_sub_id);
    if (result >= 0) g_sub_id = -1;
    write_status("stopped", result, "subscription terminated");
    return result;
}

extern "C" __attribute__((visibility("default")))
int64_t wind_tbapi_subscription_id(void) { return g_sub_id; }
