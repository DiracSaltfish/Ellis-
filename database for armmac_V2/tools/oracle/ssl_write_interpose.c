#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef int (*ssl_write_fn)(void *, const void *, int);
typedef int (*ssl_read_fn)(void *, void *, int);

static ssl_write_fn real_ssl_write;
static ssl_read_fn real_ssl_read;
static pthread_once_t symbols_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t capture_lock = PTHREAD_MUTEX_INITIALIZER;
static int capture_fd = -1;

static void load_symbols(void) {
    /*
     * The Python process also loads the OS OpenSSL. RTLD_NEXT can therefore
     * resolve the wrong ABI. Prefer the vendor's already-loaded libssl.so.10,
     * matching the official SDK's own dependency.
     */
    void *ssl_handle = NULL;
    const char *vendor_libssl = getenv("TGW_VENDOR_LIBSSL");
    if (vendor_libssl && *vendor_libssl) {
        ssl_handle = dlopen(vendor_libssl, RTLD_LAZY | RTLD_NOLOAD);
        if (!ssl_handle) ssl_handle = dlopen(vendor_libssl, RTLD_LAZY);
    }
    real_ssl_write = (ssl_write_fn)dlsym(ssl_handle ? ssl_handle : RTLD_NEXT, "SSL_write");
    real_ssl_read = (ssl_read_fn)dlsym(ssl_handle ? ssl_handle : RTLD_NEXT, "SSL_read");
    const char *path = getenv("TGW_SSL_CAPTURE");
    if (!path || !*path) return;
    capture_fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (capture_fd >= 0 && lseek(capture_fd, 0, SEEK_END) == 0) {
        static const char magic[] = "TGWSSL3\n";
        (void)write(capture_fd, magic, sizeof(magic) - 1);
    }
}

static void write_all(int fd, const void *buffer, size_t length) {
    const unsigned char *cursor = (const unsigned char *)buffer;
    while (length) {
        ssize_t result = write(fd, cursor, length);
        if (result > 0) {
            cursor += (size_t)result;
            length -= (size_t)result;
        } else if (result < 0 && errno == EINTR) {
            continue;
        } else {
            break;
        }
    }
}

int SSL_write(void *ssl, const void *buffer, int length) {
    pthread_once(&symbols_once, load_symbols);
    if (!real_ssl_write) {
        errno = ENOSYS;
        return -1;
    }
    int result = real_ssl_write(ssl, buffer, length);
    if (result <= 0 || capture_fd < 0) return result;

    uint64_t stream_id = (uint64_t)(uintptr_t)ssl;
    uint32_t captured_length = (uint32_t)result;
    const unsigned char direction = 'W';
    pthread_mutex_lock(&capture_lock);
    write_all(capture_fd, &direction, sizeof(direction));
    write_all(capture_fd, &stream_id, sizeof(stream_id));
    write_all(capture_fd, &captured_length, sizeof(captured_length));
    write_all(capture_fd, buffer, (size_t)result);
    pthread_mutex_unlock(&capture_lock);
    return result;
}

int SSL_read(void *ssl, void *buffer, int length) {
    pthread_once(&symbols_once, load_symbols);
    if (!real_ssl_read) {
        errno = ENOSYS;
        return -1;
    }
    int result = real_ssl_read(ssl, buffer, length);
    if (result <= 0 || capture_fd < 0) return result;

    uint64_t stream_id = (uint64_t)(uintptr_t)ssl;
    uint32_t captured_length = (uint32_t)result;
    const unsigned char direction = 'R';
    pthread_mutex_lock(&capture_lock);
    write_all(capture_fd, &direction, sizeof(direction));
    write_all(capture_fd, &stream_id, sizeof(stream_id));
    write_all(capture_fd, &captured_length, sizeof(captured_length));
    write_all(capture_fd, buffer, (size_t)result);
    pthread_mutex_unlock(&capture_lock);
    return result;
}
