#include "internal.hpp"

#include "tgw/protocol.hpp"

#include <algorithm>
#include <array>
#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cctype>
#include <cstring>
#include <fcntl.h>
#include <iomanip>
#include <limits>
#include <netdb.h>
#include <netinet/tcp.h>
#include <optional>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/sha.h>
#include <openssl/ssl.h>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <sys/socket.h>
#include <unistd.h>

namespace tgw::detail {
namespace {

std::string openssl_error(const std::string& prefix) {
    const auto code = ERR_get_error();
    if (code == 0) {
        return prefix;
    }
    char buffer[256]{};
    ERR_error_string_n(code, buffer, sizeof(buffer));
    return prefix + ": " + buffer;
}

int connect_tcp(const std::string& host, std::uint16_t port,
                std::chrono::milliseconds timeout) {
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_protocol = IPPROTO_TCP;

    addrinfo* raw_addresses = nullptr;
    const std::string service = std::to_string(port);
    const int lookup = ::getaddrinfo(host.c_str(), service.c_str(), &hints, &raw_addresses);
    if (lookup != 0) {
        throw TransportError("DNS lookup failed for TGW host: " +
                             std::string(gai_strerror(lookup)));
    }
    std::unique_ptr<addrinfo, decltype(&freeaddrinfo)> addresses(raw_addresses, freeaddrinfo);

    std::string last_error = "no address succeeded";
    for (auto* address = addresses.get(); address != nullptr; address = address->ai_next) {
        const int fd = ::socket(address->ai_family, address->ai_socktype, address->ai_protocol);
        if (fd < 0) {
            last_error = std::strerror(errno);
            continue;
        }
        const int original_flags = ::fcntl(fd, F_GETFL, 0);
        if (original_flags < 0 || ::fcntl(fd, F_SETFL, original_flags | O_NONBLOCK) < 0) {
            last_error = std::strerror(errno);
            ::close(fd);
            continue;
        }

        int result = ::connect(fd, address->ai_addr, address->ai_addrlen);
        if (result < 0 && errno == EINPROGRESS) {
            pollfd descriptor{fd, POLLOUT, 0};
            result = ::poll(&descriptor, 1, static_cast<int>(timeout.count()));
            if (result > 0) {
                int socket_error = 0;
                socklen_t error_size = sizeof(socket_error);
                if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, &socket_error, &error_size) != 0) {
                    socket_error = errno;
                }
                if (socket_error != 0) {
                    errno = socket_error;
                    result = -1;
                } else {
                    result = 0;
                }
            } else if (result == 0) {
                errno = ETIMEDOUT;
                result = -1;
            }
        }
        if (result == 0) {
            if (::fcntl(fd, F_SETFL, original_flags) < 0) {
                last_error = std::strerror(errno);
                ::close(fd);
                continue;
            }
            int enabled = 1;
            (void)::setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled));
#if defined(SO_NOSIGPIPE)
            (void)::setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled));
#endif
            const timeval socket_timeout{
                static_cast<time_t>(timeout.count() / 1000),
                static_cast<suseconds_t>((timeout.count() % 1000) * 1000)
            };
            (void)::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &socket_timeout,
                               sizeof(socket_timeout));
            (void)::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &socket_timeout,
                               sizeof(socket_timeout));
            return fd;
        }
        last_error = std::strerror(errno);
        ::close(fd);
    }
    throw TransportError("TCP connection to TGW failed: " + last_error);
}

std::string base64(std::span<const unsigned char> bytes) {
    std::string output(4U * ((bytes.size() + 2U) / 3U), '\0');
    const int written = EVP_EncodeBlock(
        reinterpret_cast<unsigned char*>(output.data()), bytes.data(),
        static_cast<int>(bytes.size())
    );
    if (written < 0) {
        throw TransportError("base64 encoding failed");
    }
    output.resize(static_cast<std::size_t>(written));
    return output;
}

std::string trim(std::string value) {
    const auto begin = value.find_first_not_of(" \t\r\n");
    if (begin == std::string::npos) {
        return {};
    }
    const auto end = value.find_last_not_of(" \t\r\n");
    return value.substr(begin, end - begin + 1U);
}

std::vector<std::uint8_t> encode_frame(std::span<const std::uint8_t> payload,
                                       std::uint8_t opcode) {
    if (opcode > 0x0F) {
        throw ProtocolError("invalid WebSocket opcode");
    }
    if (opcode >= 0x08 && payload.size() > 125U) {
        throw ProtocolError("invalid WebSocket control frame");
    }
    std::vector<std::uint8_t> frame;
    frame.reserve(payload.size() + 14U);
    frame.push_back(static_cast<std::uint8_t>(0x80U | opcode));
    if (payload.size() < 126U) {
        frame.push_back(static_cast<std::uint8_t>(0x80U | payload.size()));
    } else if (payload.size() <= 0xFFFFU) {
        frame.push_back(0x80U | 126U);
        frame.push_back(static_cast<std::uint8_t>((payload.size() >> 8U) & 0xFFU));
        frame.push_back(static_cast<std::uint8_t>(payload.size() & 0xFFU));
    } else {
        frame.push_back(0x80U | 127U);
        const auto length = static_cast<std::uint64_t>(payload.size());
        for (int shift = 56; shift >= 0; shift -= 8) {
            frame.push_back(static_cast<std::uint8_t>((length >> shift) & 0xFFU));
        }
    }
    std::array<std::uint8_t, 4> mask{};
    if (RAND_bytes(mask.data(), static_cast<int>(mask.size())) != 1) {
        throw TransportError(openssl_error("WebSocket mask generation failed"));
    }
    frame.insert(frame.end(), mask.begin(), mask.end());
    for (std::size_t index = 0; index < payload.size(); ++index) {
        frame.push_back(static_cast<std::uint8_t>(payload[index] ^ mask[index & 3U]));
    }
    return frame;
}

} // namespace

SecureWebSocket::~SecureWebSocket() {
    close();
}

void SecureWebSocket::connect(
    const std::string& host,
    std::uint16_t port,
    const std::string& endpoint,
    const std::string& ca_file,
    const std::string& server_name,
    std::chrono::milliseconds timeout,
    std::size_t max_payload
) {
    if (open()) {
        throw TransportError("TGW WebSocket is already connected");
    }
    OPENSSL_init_ssl(0, nullptr);
    max_payload_ = max_payload;
    context_ = SSL_CTX_new(TLS_client_method());
    if (context_ == nullptr) {
        throw TransportError(openssl_error("cannot create TLS context"));
    }
    try {
        if (SSL_CTX_set_min_proto_version(context_, TLS1_VERSION) != 1 ||
            SSL_CTX_set_max_proto_version(context_, TLS1_2_VERSION) != 1) {
            throw TransportError(openssl_error("cannot set TGW TLS version range"));
        }
        if (SSL_CTX_set_cipher_list(context_, "DEFAULT:@SECLEVEL=0") != 1) {
            throw TransportError(openssl_error("cannot set TGW TLS cipher profile"));
        }
        SSL_CTX_set_verify(context_, SSL_VERIFY_PEER, nullptr);
        if (ca_file.empty()) {
            if (SSL_CTX_set_default_verify_paths(context_) != 1) {
                throw TransportError(openssl_error("cannot load default CA paths"));
            }
        } else if (SSL_CTX_load_verify_locations(context_, ca_file.c_str(), nullptr) != 1) {
            throw TransportError(openssl_error("cannot load TGW CA file"));
        }
        socket_fd_.store(connect_tcp(host, port, timeout));
        ssl_ = SSL_new(context_);
        if (ssl_ == nullptr) {
            throw TransportError(openssl_error("cannot create TLS session"));
        }
        if (SSL_set_fd(ssl_, socket_fd_.load()) != 1 ||
            SSL_set_tlsext_host_name(ssl_, server_name.c_str()) != 1) {
            throw TransportError(openssl_error("cannot configure TGW TLS session"));
        }
        X509_VERIFY_PARAM* verify = SSL_get0_param(ssl_);
        X509_VERIFY_PARAM_clear_flags(verify, X509_V_FLAG_X509_STRICT);
        if (X509_VERIFY_PARAM_set1_host(verify, server_name.c_str(), 0) != 1) {
            throw TransportError(openssl_error("cannot configure TLS hostname verification"));
        }
        if (SSL_connect(ssl_) != 1) {
            throw TransportError(openssl_error("TGW TLS handshake failed"));
        }
        if (SSL_get_verify_result(ssl_) != X509_V_OK) {
            throw TransportError("TGW TLS certificate verification failed");
        }

        std::array<unsigned char, 16> nonce{};
        if (RAND_bytes(nonce.data(), static_cast<int>(nonce.size())) != 1) {
            throw TransportError(openssl_error("WebSocket nonce generation failed"));
        }
        const std::string key = base64(nonce);
        std::ostringstream request;
        request << "GET " << endpoint << " HTTP/1.1\r\n"
                << "Connection: Upgrade\r\n"
                << "Host: " << host << ':' << port << "\r\n"
                << "Sec-WebSocket-Key: " << key << "\r\n"
                << "Sec-WebSocket-Version: 13\r\n"
                << "Upgrade: websocket\r\n"
                << "User-Agent: WebSocket++/0.8.2\r\n\r\n";
        const std::string upgrade_request = request.str();
        write_all(std::span<const std::uint8_t>(
            reinterpret_cast<const std::uint8_t*>(upgrade_request.data()),
            upgrade_request.size()
        ));

        std::string response;
        while (response.find("\r\n\r\n") == std::string::npos) {
            std::array<char, 4096> chunk{};
            const int received = SSL_read(ssl_, chunk.data(), static_cast<int>(chunk.size()));
            if (received <= 0) {
                throw TransportError(openssl_error(
                    "connection closed during WebSocket upgrade"));
            }
            response.append(chunk.data(), static_cast<std::size_t>(received));
            if (response.size() > 64U * 1024U) {
                throw TransportError("oversized WebSocket upgrade response");
            }
        }
        const auto header_end = response.find("\r\n\r\n") + 4U;
        if (header_end != response.size()) {
            throw TransportError("unexpected data after WebSocket upgrade headers");
        }
        std::istringstream lines(response.substr(0, header_end - 2U));
        std::string status_line;
        std::getline(lines, status_line);
        if (status_line.find(" 101 ") == std::string::npos) {
            throw TransportError("WebSocket upgrade rejected: " + trim(status_line));
        }
        std::map<std::string, std::string> headers;
        std::string line;
        while (std::getline(lines, line)) {
            if (!line.empty() && line.back() == '\r') {
                line.pop_back();
            }
            const auto colon = line.find(':');
            if (colon == std::string::npos) {
                continue;
            }
            std::string name = trim(line.substr(0, colon));
            for (char& character : name) {
                character = static_cast<char>(std::tolower(static_cast<unsigned char>(character)));
            }
            headers[name] = trim(line.substr(colon + 1U));
        }
        const std::string source = key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
        std::array<unsigned char, SHA_DIGEST_LENGTH> digest{};
        SHA1(reinterpret_cast<const unsigned char*>(source.data()), source.size(), digest.data());
        if (headers["sec-websocket-accept"] != base64(digest)) {
            throw TransportError("invalid Sec-WebSocket-Accept response");
        }

        const timeval no_timeout{0, 0};
        const int fd = socket_fd_.load();
        (void)::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &no_timeout, sizeof(no_timeout));
        (void)::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &no_timeout, sizeof(no_timeout));
    } catch (...) {
        close();
        throw;
    }
}

void SecureWebSocket::write_all(std::span<const std::uint8_t> bytes) {
    std::size_t offset = 0;
    while (offset < bytes.size()) {
        const std::size_t remaining = bytes.size() - offset;
        const int chunk = static_cast<int>(std::min<std::size_t>(
            remaining, static_cast<std::size_t>(std::numeric_limits<int>::max())
        ));
        const int written = SSL_write(ssl_, bytes.data() + offset, chunk);
        if (written <= 0) {
            throw TransportError(openssl_error("TGW TLS write failed"));
        }
        offset += static_cast<std::size_t>(written);
    }
}

std::vector<std::uint8_t> SecureWebSocket::read_exact(std::size_t count) {
    std::vector<std::uint8_t> output(count);
    std::size_t offset = 0;
    while (offset < count) {
        const std::size_t remaining = count - offset;
        const int chunk = static_cast<int>(std::min<std::size_t>(
            remaining, static_cast<std::size_t>(std::numeric_limits<int>::max())
        ));
        const int received = SSL_read(ssl_, output.data() + offset, chunk);
        if (received <= 0) {
            const int error = SSL_get_error(ssl_, received);
            if (error == SSL_ERROR_ZERO_RETURN) {
                throw TransportError("WebSocket peer closed the connection");
            }
            throw TransportError(openssl_error("TGW TLS read failed"));
        }
        offset += static_cast<std::size_t>(received);
    }
    return output;
}

void SecureWebSocket::send_frame(std::span<const std::uint8_t> payload,
                                 std::uint8_t opcode) {
    if (!open()) {
        throw TransportError("TGW WebSocket is not connected");
    }
    const auto frame = encode_frame(payload, opcode);
    std::scoped_lock lock(write_mutex_);
    write_all(frame);
}

WebSocketFrame SecureWebSocket::read_frame() {
    const auto header = read_exact(2);
    WebSocketFrame frame;
    frame.fin = (header[0] & 0x80U) != 0;
    const auto rsv = static_cast<std::uint8_t>(header[0] & 0x70U);
    frame.opcode = static_cast<std::uint8_t>(header[0] & 0x0FU);
    if (rsv != 0) {
        throw ProtocolError("unsupported WebSocket RSV bits");
    }
    const bool masked = (header[1] & 0x80U) != 0;
    std::uint64_t length = header[1] & 0x7FU;
    if (length == 126U) {
        const auto extended = read_exact(2);
        length = (static_cast<std::uint64_t>(extended[0]) << 8U) | extended[1];
    } else if (length == 127U) {
        const auto extended = read_exact(8);
        length = 0;
        for (const auto byte : extended) {
            length = (length << 8U) | byte;
        }
        if ((length & (UINT64_C(1) << 63U)) != 0) {
            throw ProtocolError("invalid 64-bit WebSocket payload length");
        }
    }
    if (length > max_payload_) {
        throw ProtocolError("WebSocket payload exceeds configured maximum");
    }
    if (frame.opcode >= 0x08U && (!frame.fin || length > 125U)) {
        throw ProtocolError("invalid WebSocket control frame");
    }
    const auto mask = masked ? read_exact(4) : std::vector<std::uint8_t>{};
    frame.payload = read_exact(static_cast<std::size_t>(length));
    if (masked) {
        for (std::size_t index = 0; index < frame.payload.size(); ++index) {
            frame.payload[index] ^= mask[index & 3U];
        }
    }
    return frame;
}

void SecureWebSocket::close() noexcept {
    const int fd = socket_fd_.exchange(-1);
    if (fd >= 0) {
        (void)::shutdown(fd, SHUT_RDWR);
    }
    if (ssl_ != nullptr) {
        SSL_free(ssl_);
        ssl_ = nullptr;
    }
    if (context_ != nullptr) {
        SSL_CTX_free(context_);
        context_ = nullptr;
    }
    if (fd >= 0) {
        (void)::close(fd);
    }
}

void SecureWebSocket::interrupt() noexcept {
    const int fd = socket_fd_.load();
    if (fd >= 0) {
        (void)::shutdown(fd, SHUT_RDWR);
    }
}

bool SecureWebSocket::open() const noexcept {
    return socket_fd_.load() >= 0 && ssl_ != nullptr;
}

WebSocketClient::WebSocketClient(std::string endpoint,
                                 std::chrono::milliseconds timeout,
                                 std::chrono::milliseconds heartbeat,
                                 std::size_t max_payload)
    : endpoint_(std::move(endpoint)), timeout_(timeout), heartbeat_(heartbeat),
      max_payload_(max_payload) {}

WebSocketClient::~WebSocketClient() {
    close();
}

void WebSocketClient::connect(const std::string& host, std::uint16_t port,
                              const std::string& ca_file,
                              const std::string& server_name) {
    if (connected()) {
        throw TransportError("TGW client is already connected");
    }
    socket_.connect(host, port, endpoint_, ca_file, server_name, timeout_, max_payload_);
    stop_.store(false);
    reader_error_ = nullptr;
    reader_ = std::thread(&WebSocketClient::reader_loop, this);
}

RequestOutcome WebSocketClient::request_many(
    std::int64_t request_id,
    std::string_view payload,
    DonePredicate done,
    std::chrono::milliseconds timeout
) {
    if (!connected()) {
        throw TransportError("TGW WebSocket is not connected");
    }
    auto waiter = std::make_shared<Waiter>();
    {
        std::scoped_lock lock(waiters_mutex_);
        if (waiters_.contains(request_id)) {
            throw ProtocolError("duplicate TGW request id");
        }
        waiters_.emplace(request_id, waiter);
    }
    struct Eraser {
        WebSocketClient* client;
        std::int64_t id;
        ~Eraser() {
            std::scoped_lock lock(client->waiters_mutex_);
            client->waiters_.erase(id);
        }
    } eraser{this, request_id};

    send(payload);
    const auto effective_timeout = timeout.count() > 0 ? timeout : timeout_;
    const auto deadline = std::chrono::steady_clock::now() + effective_timeout;
    RequestOutcome outcome;
    while (true) {
        std::unique_lock lock(waiter->mutex);
        if (!waiter->cv.wait_until(lock, deadline, [&] {
                return !waiter->messages.empty() || waiter->error != nullptr;
            })) {
            throw TimeoutError("TGW request " + std::to_string(request_id) + " timed out");
        }
        if (waiter->messages.empty() && waiter->error != nullptr) {
            try {
                std::rethrow_exception(waiter->error);
            } catch (const std::exception& error) {
                throw TransportError(std::string("TGW reader failed: ") + error.what());
            }
        }
        std::string message = std::move(waiter->messages.front());
        waiter->messages.pop_front();
        lock.unlock();
        outcome.messages.push_back(std::move(message));
        if (done(outcome.messages.back())) {
            return outcome;
        }
    }
}

void WebSocketClient::send(std::string_view payload) {
    socket_.send_frame(std::span<const std::uint8_t>(
        reinterpret_cast<const std::uint8_t*>(payload.data()), payload.size()
    ));
}

void WebSocketClient::start_heartbeat() {
    if (heartbeat_.count() <= 0 || heartbeat_thread_.joinable()) {
        return;
    }
    heartbeat_thread_ = std::thread(&WebSocketClient::heartbeat_loop, this);
}

std::string WebSocketClient::receive_event(std::chrono::milliseconds timeout) {
    std::unique_lock lock(events_mutex_);
    if (!events_cv_.wait_for(lock, timeout, [&] {
            return !events_.empty() || reader_error_ != nullptr || stop_.load();
        })) {
        throw TimeoutError("timed out waiting for TGW push event");
    }
    if (!events_.empty()) {
        std::string value = std::move(events_.front());
        events_.pop_front();
        return value;
    }
    if (reader_error_ != nullptr) {
        try {
            std::rethrow_exception(reader_error_);
        } catch (const std::exception& error) {
            throw TransportError(std::string("TGW push reader failed: ") + error.what());
        }
    }
    throw TransportError("TGW push connection is closed");
}

bool WebSocketClient::wait_closed(std::chrono::milliseconds timeout) {
    std::unique_lock lock(stop_mutex_);
    return stop_cv_.wait_for(lock, timeout, [&] { return stop_.load(); });
}

void WebSocketClient::reader_loop() {
    std::optional<std::uint8_t> fragmented_opcode;
    std::vector<std::uint8_t> fragmented;
    try {
        while (!stop_.load()) {
            auto frame = socket_.read_frame();
            if (frame.opcode == 0x08U) {
                if (!stop_.load()) {
                    std::ostringstream detail;
                    detail << "server closed TGW WebSocket";
                    if (frame.payload.size() >= 2U) {
                        const auto code = static_cast<unsigned int>(
                            (static_cast<unsigned int>(frame.payload[0]) << 8U) |
                            static_cast<unsigned int>(frame.payload[1]));
                        detail << " (code=" << code;
                        if (frame.payload.size() > 2U) {
                            detail << ", reason="
                                   << std::string(frame.payload.begin() + 2,
                                                  frame.payload.end());
                        }
                        detail << ')';
                    }
                    throw TransportError(detail.str());
                }
                break;
            }
            if (frame.opcode == 0x09U) {
                socket_.send_frame(frame.payload, 0x0AU);
                continue;
            }
            if (frame.opcode == 0x0AU) {
                continue;
            }
            if (frame.opcode == 0x01U || frame.opcode == 0x02U) {
                if (fragmented_opcode.has_value()) {
                    throw ProtocolError(
                        "new WebSocket data frame before fragmented message completed");
                }
                if (frame.fin) {
                    for (auto& message : protocol::decode_server_payload(
                             frame.payload, max_payload_)) {
                        dispatch_message(std::move(message));
                    }
                } else {
                    fragmented_opcode = frame.opcode;
                    fragmented = std::move(frame.payload);
                }
                continue;
            }
            if (frame.opcode == 0x00U) {
                if (!fragmented_opcode.has_value()) {
                    throw ProtocolError("unexpected WebSocket continuation frame");
                }
                fragmented.insert(fragmented.end(), frame.payload.begin(), frame.payload.end());
                if (fragmented.size() > max_payload_) {
                    throw ProtocolError("fragmented TGW message exceeds configured maximum");
                }
                if (frame.fin) {
                    for (auto& message : protocol::decode_server_payload(
                             fragmented, max_payload_)) {
                        dispatch_message(std::move(message));
                    }
                    fragmented.clear();
                    fragmented_opcode.reset();
                }
                continue;
            }
            throw ProtocolError("unsupported WebSocket opcode");
        }
    } catch (...) {
        if (!stop_.load()) {
            const auto error = std::current_exception();
            {
                std::scoped_lock lock(events_mutex_);
                reader_error_ = error;
            }
            fail_waiters(error);
            events_cv_.notify_all();
        }
    }
    mark_stopped();
}

void WebSocketClient::heartbeat_loop() {
    while (!stop_.load()) {
        std::unique_lock lock(stop_mutex_);
        if (stop_cv_.wait_for(lock, heartbeat_, [&] { return stop_.load(); })) {
            return;
        }
        lock.unlock();
        try {
            static constexpr std::string_view heartbeat = "Heartbeat";
            socket_.send_frame(std::span<const std::uint8_t>(
                reinterpret_cast<const std::uint8_t*>(heartbeat.data()),
                heartbeat.size()), 0x09U);
        } catch (...) {
            fail_waiters(std::current_exception());
            mark_stopped();
            socket_.interrupt();
            return;
        }
    }
}

void WebSocketClient::dispatch_message(std::string message) {
    const auto meta = protocol::inspect_envelope(message);
    std::shared_ptr<Waiter> waiter;
    if (meta.request_id.has_value()) {
        std::scoped_lock lock(waiters_mutex_);
        const auto found = waiters_.find(*meta.request_id);
        if (found != waiters_.end()) {
            waiter = found->second;
        }
    }
    if (waiter != nullptr) {
        {
            std::scoped_lock lock(waiter->mutex);
            waiter->messages.push_back(std::move(message));
        }
        waiter->cv.notify_one();
    } else {
        offer_event(std::move(message));
    }
}

void WebSocketClient::fail_waiters(std::exception_ptr error) {
    std::vector<std::shared_ptr<Waiter>> waiters;
    {
        std::scoped_lock lock(waiters_mutex_);
        for (const auto& [unused, waiter] : waiters_) {
            (void)unused;
            waiters.push_back(waiter);
        }
    }
    for (const auto& waiter : waiters) {
        {
            std::scoped_lock lock(waiter->mutex);
            waiter->error = error;
        }
        waiter->cv.notify_all();
    }
}

void WebSocketClient::offer_event(std::string message) {
    {
        std::scoped_lock lock(events_mutex_);
        if (events_.size() >= kEventLimit) {
            events_.pop_front();
        }
        events_.push_back(std::move(message));
    }
    events_cv_.notify_one();
}

void WebSocketClient::mark_stopped() noexcept {
    stop_.store(true);
    stop_cv_.notify_all();
    events_cv_.notify_all();
}

void WebSocketClient::close() noexcept {
    if (!stop_.exchange(true) && socket_.open()) {
        try {
            socket_.send_frame({}, 0x08U);
        } catch (...) {
        }
    }
    // Wake the blocking reader, but keep SSL and its crypto state alive until
    // both I/O threads have exited. Freeing SSL before the reader returns is a
    // real use-after-free on OpenSSL 3.x.
    socket_.interrupt();
    stop_cv_.notify_all();
    events_cv_.notify_all();
    if (reader_.joinable() && reader_.get_id() != std::this_thread::get_id()) {
        reader_.join();
    }
    if (heartbeat_thread_.joinable() &&
        heartbeat_thread_.get_id() != std::this_thread::get_id()) {
        heartbeat_thread_.join();
    }
    socket_.close();
}

bool WebSocketClient::connected() const noexcept {
    return !stop_.load() && socket_.open();
}

} // namespace tgw::detail
