#pragma once

#include "tgw/types.hpp"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <exception>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <span>
#include <string>
#include <thread>
#include <vector>

struct ssl_ctx_st;
struct ssl_st;

namespace tgw::detail {

struct WebSocketFrame {
    bool fin{true};
    std::uint8_t opcode{0};
    std::vector<std::uint8_t> payload;
};

class SecureWebSocket {
public:
    SecureWebSocket() = default;
    ~SecureWebSocket();

    SecureWebSocket(const SecureWebSocket&) = delete;
    SecureWebSocket& operator=(const SecureWebSocket&) = delete;

    void connect(
        const std::string& host,
        std::uint16_t port,
        const std::string& endpoint,
        const std::string& ca_file,
        const std::string& server_name,
        std::chrono::milliseconds timeout,
        std::size_t max_payload
    );
    void send_frame(std::span<const std::uint8_t> payload, std::uint8_t opcode = 0x2);
    WebSocketFrame read_frame();
    void interrupt() noexcept;
    void close() noexcept;
    [[nodiscard]] bool open() const noexcept;

private:
    void write_all(std::span<const std::uint8_t> bytes);
    std::vector<std::uint8_t> read_exact(std::size_t count);

    ssl_ctx_st* context_{nullptr};
    ssl_st* ssl_{nullptr};
    std::atomic<int> socket_fd_{-1};
    std::size_t max_payload_{64U * 1024U * 1024U};
    std::mutex write_mutex_;
};

struct RequestOutcome {
    std::vector<std::string> messages;
};

class WebSocketClient {
public:
    using DonePredicate = std::function<bool(std::string_view)>;

    WebSocketClient(std::string endpoint, std::chrono::milliseconds timeout,
                    std::chrono::milliseconds heartbeat, std::size_t max_payload);
    ~WebSocketClient();

    WebSocketClient(const WebSocketClient&) = delete;
    WebSocketClient& operator=(const WebSocketClient&) = delete;

    void connect(const std::string& host, std::uint16_t port,
                 const std::string& ca_file, const std::string& server_name);
    RequestOutcome request_many(std::int64_t request_id, std::string_view payload,
                                DonePredicate done,
                                std::chrono::milliseconds timeout = {});
    void send(std::string_view payload);
    void start_heartbeat();
    std::string receive_event(std::chrono::milliseconds timeout);
    bool wait_closed(std::chrono::milliseconds timeout);
    void close() noexcept;

    [[nodiscard]] bool connected() const noexcept;

private:
    struct Waiter {
        std::mutex mutex;
        std::condition_variable cv;
        std::deque<std::string> messages;
        std::exception_ptr error;
    };

    void reader_loop();
    void heartbeat_loop();
    void dispatch_message(std::string message);
    void fail_waiters(std::exception_ptr error);
    void offer_event(std::string message);
    void mark_stopped() noexcept;

    std::string endpoint_;
    std::chrono::milliseconds timeout_;
    std::chrono::milliseconds heartbeat_;
    std::size_t max_payload_;
    SecureWebSocket socket_;
    std::atomic<bool> stop_{true};
    std::thread reader_;
    std::thread heartbeat_thread_;

    std::mutex waiters_mutex_;
    std::map<std::int64_t, std::shared_ptr<Waiter>> waiters_;

    std::mutex events_mutex_;
    std::condition_variable events_cv_;
    std::deque<std::string> events_;
    std::exception_ptr reader_error_;
    static constexpr std::size_t kEventLimit = 10000;

    std::mutex stop_mutex_;
    std::condition_variable stop_cv_;
};

} // namespace tgw::detail
