#include "tgw/session.hpp"

#include "internal.hpp"
#include "tgw/protocol.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <span>
#include <stdexcept>
#include <string_view>
#include <unistd.h>

namespace tgw {

class Session::Impl {
public:
    explicit Impl(Config config) : config_(std::move(config)) {
        if (config_.hosts.empty()) {
            throw std::invalid_argument("TGW Config.hosts must not be empty");
        }
        if (config_.port == 0) {
            throw std::invalid_argument("TGW Config.port must not be zero");
        }
        if (config_.username.empty() || config_.password.empty()) {
            throw std::invalid_argument("TGW Config username/password must not be empty");
        }
        if (config_.query_endpoints.empty()) {
            throw std::invalid_argument("TGW Config.query_endpoints must not be empty");
        }
    }

    ~Impl() { close(); }

    void connect() {
        if (push_ != nullptr && push_->connected()) {
            throw TransportError("TGW session is already connected");
        }
        std::string last_error;
        for (const auto& host : config_.hosts) {
            auto candidate = std::make_unique<detail::WebSocketClient>(
                "/amd/dgw/push", config_.timeout, config_.heartbeat,
                config_.max_payload);
            try {
                candidate->connect(host, config_.port, config_.ca_file,
                                   config_.tls_server_name);
                active_host_ = host;
                push_ = std::move(candidate);
                return;
            } catch (const std::exception& error) {
                last_error = error.what();
                candidate->close();
            }
        }
        throw TransportError("all TGW push endpoints failed: " + last_error);
    }

    LoginInfo login() {
        if (push_ == nullptr || !push_->connected()) {
            throw TransportError("TGW session is not connected");
        }
        const std::string payload = protocol::build_logon_request(
            config_.username, config_.password, config_.force_logout,
            config_.client_version, static_cast<std::int64_t>(::getpid()));
        const auto outcome = push_->request_many(
            0, payload, [](std::string_view) { return true; });
        if (outcome.messages.empty()) {
            throw ProtocolError("empty TGW logon response");
        }
        const auto meta = protocol::inspect_envelope(outcome.messages.front());
        LoginInfo info;
        info.status = meta.status.value_or(-100);
        info.response_tag = meta.tag;
        info.authenticated = info.status == 0 && info.response_tag == "OnRspLogon" &&
                             !meta.token.empty();
        if (!info.authenticated) {
            return info;
        }
        token_ = meta.token;
        authenticated_ = true;
        push_->start_heartbeat();
        return info;
    }

    LoginInfo connect_and_login() {
        connect();
        return login();
    }

    void subscribe(const std::vector<SubscribeItem>& items, bool unsubscribe) {
        require_authenticated();
        const auto request_id = subscription_id_.fetch_add(1);
        const std::string payload = protocol::build_subscribe_request(
            config_.username, token_, request_id, items, unsubscribe);
        const auto outcome = push_->request_many(
            request_id, payload, [](std::string_view) { return true; });
        if (outcome.messages.empty()) {
            throw ProtocolError("empty TGW subscription response");
        }
        const auto meta = protocol::inspect_envelope(outcome.messages.front());
        if (!meta.status.has_value() || *meta.status != 0) {
            throw ProtocolError("TGW subscription was rejected");
        }
    }

    std::string receive_event(std::chrono::milliseconds timeout) {
        require_authenticated();
        return push_->receive_event(timeout);
    }

    std::vector<KlineRow> query_kline(const KlineRequest& request) {
        require_authenticated();
        const auto request_id = next_task_id();
        const auto payload = protocol::build_kline_request(
            config_.username, token_, request_id, request);
        return protocol::parse_kline_packets(
            query_exchange(request_id, payload),
            protocol::kline_wire_period(request.cyc_type));
    }

    QueryResult<SnapshotRow> query_snapshot(const SnapshotRequest& request) {
        require_authenticated();
        const auto request_id = next_task_id();
        const auto payload = protocol::build_snapshot_request(
            config_.username, token_, request_id, request);
        return protocol::parse_snapshot_packets(query_exchange(request_id, payload));
    }

    std::vector<CodeTableRow> query_code_table() {
        require_authenticated();
        const auto request_id = next_task_id();
        const auto payload = protocol::build_code_table_request(
            config_.username, token_, request_id);
        auto client = open_query_client(request_id);
        std::map<std::int64_t, std::string> seen;
        std::optional<std::int64_t> expected;
        const auto collect = [&](std::string_view message) {
            const auto meta = protocol::inspect_envelope(message);
            if (!meta.status.has_value() || *meta.status != 0) {
                return true;
            }
            if (!meta.pack_num.has_value() || !meta.all_pack_num.has_value()) {
                return true;
            }
            if (!expected.has_value()) {
                expected = meta.all_pack_num;
            }
            seen[*meta.pack_num] = std::string(message);
            return expected.has_value() &&
                   static_cast<std::int64_t>(seen.size()) == *expected;
        };
        try {
            (void)client->request_many(request_id, payload, collect);
        } catch (const TimeoutError&) {
            if (!expected.has_value()) {
                throw;
            }
            for (std::int64_t number = 1; number <= *expected; ++number) {
                if (seen.contains(number)) {
                    continue;
                }
                try {
                    (void)client->request_many(
                        request_id,
                        protocol::build_get_package_request(
                            config_.username, token_, request_id, number),
                        collect);
                } catch (const TimeoutError&) {
                    break;
                }
            }
        }
        if (!expected.has_value() || static_cast<std::int64_t>(seen.size()) != *expected) {
            throw TimeoutError("code-table response is missing packets");
        }
        client->send(protocol::build_query_complete_request(
            config_.username, token_, request_id));
        (void)client->wait_closed(std::min(config_.timeout,
                                           std::chrono::milliseconds(2000)));
        client->close();
        std::vector<std::string> packets;
        packets.reserve(seen.size());
        for (auto& [number, packet] : seen) {
            (void)number;
            packets.push_back(std::move(packet));
        }
        return protocol::parse_code_table_packets(packets);
    }

    std::vector<EtfRecord> query_etf_info(const SecurityItem& item) {
        require_authenticated();
        const auto request_id = codelist_id_.fetch_add(1);
        const std::array<SecurityItem, 1> items{item};
        const auto payload = protocol::build_etf_info_request(
            config_.username, token_, request_id, items);
        const auto outcome = push_->request_many(
            request_id, payload, [](std::string_view) { return true; });
        try {
            push_->send(protocol::build_codelist_complete_request(
                config_.username, token_, request_id));
        } catch (const TransportError&) {
        }
        return protocol::parse_etf_info_packets(outcome.messages, request_id);
    }

    std::vector<SecuritiesInfoRow> query_securities_info(
        const std::vector<SecurityItem>& items) {
        require_authenticated();
        const auto request_id = codelist_id_.fetch_add(1);
        const auto payload = protocol::build_securities_info_request(
            config_.username, token_, request_id, items);
        const auto outcome = push_->request_many(
            request_id, payload, [](std::string_view) { return true; });
        try {
            push_->send(protocol::build_codelist_complete_request(
                config_.username, token_, request_id));
        } catch (const TransportError&) {
        }
        return protocol::parse_securities_info_packets(outcome.messages, request_id);
    }

    std::vector<ExFactorRow> query_ex_factor(std::string_view security_code) {
        require_authenticated();
        const auto request_id = next_task_id();
        const auto payload = protocol::build_ex_factor_request(
            config_.username, token_, request_id, security_code);
        return protocol::parse_ex_factor_packets(query_exchange(request_id, payload));
    }

    std::vector<std::string> query_third_info(
        const std::vector<std::pair<std::string, std::string>>& parameters,
        std::int64_t offset,
        std::int64_t count
    ) {
        require_authenticated();
        const auto request_id = next_task_id();
        const auto payload = protocol::build_third_info_request(
            config_.username, token_, request_id, parameters, offset, count);
        return protocol::parse_third_info_packets(query_exchange(request_id, payload));
    }

    void close() noexcept {
        authenticated_ = false;
        if (push_ != nullptr) {
            push_->close();
            push_.reset();
        }
        secure_clear(token_);
        secure_clear(config_.password);
        active_host_.clear();
    }

    [[nodiscard]] bool connected() const noexcept {
        return push_ != nullptr && push_->connected();
    }

    [[nodiscard]] bool authenticated() const noexcept { return authenticated_; }
    [[nodiscard]] const std::string& active_host() const noexcept { return active_host_; }

private:
    static void secure_clear(std::string& value) noexcept {
        std::fill(value.begin(), value.end(), '\0');
        value.clear();
    }

    void require_authenticated() const {
        if (!authenticated_ || push_ == nullptr || !push_->connected()) {
            throw TransportError("TGW session is not authenticated");
        }
    }

    std::unique_ptr<detail::WebSocketClient> open_query_client(
        std::int64_t request_id) const {
        std::string last_error;
        const std::size_t start = static_cast<std::size_t>(
            (request_id - 1) % static_cast<std::int64_t>(config_.query_endpoints.size()));
        for (std::size_t step = 0; step < config_.query_endpoints.size(); ++step) {
            const auto& endpoint = config_.query_endpoints[
                (start + step) % config_.query_endpoints.size()];
            auto candidate = std::make_unique<detail::WebSocketClient>(
                endpoint, config_.timeout, std::chrono::milliseconds(0),
                config_.max_payload);
            try {
                candidate->connect(active_host_, config_.port, config_.ca_file,
                                   config_.tls_server_name);
                return candidate;
            } catch (const std::exception& error) {
                last_error = error.what();
                candidate->close();
            }
        }
        throw TransportError("all TGW query endpoints failed: " + last_error);
    }

    std::vector<std::string> query_exchange(std::int64_t request_id,
                                            const std::string& payload) const {
        auto client = open_query_client(request_id);
        std::set<std::int64_t> seen;
        std::optional<std::int64_t> expected;
        const auto outcome = client->request_many(
            request_id, payload, [&](std::string_view message) {
                const auto meta = protocol::inspect_envelope(message);
                if (!meta.status.has_value() || *meta.status != 0) {
                    return true;
                }
                if (!meta.pack_num.has_value() || !meta.all_pack_num.has_value()) {
                    return true;
                }
                if (!expected.has_value()) {
                    expected = meta.all_pack_num;
                } else if (*expected != *meta.all_pack_num) {
                    return true;
                }
                seen.insert(*meta.pack_num);
                return expected.has_value() &&
                       static_cast<std::int64_t>(seen.size()) == *expected;
            });
        bool has_error = false;
        for (const auto& message : outcome.messages) {
            const auto meta = protocol::inspect_envelope(message);
            if (!meta.status.has_value() || *meta.status != 0) {
                has_error = true;
                break;
            }
        }
        if (!has_error) {
            client->send(protocol::build_query_complete_request(
                config_.username, token_, request_id));
        }
        (void)client->wait_closed(std::min(config_.timeout,
                                           std::chrono::milliseconds(2000)));
        client->close();
        return outcome.messages;
    }

    Config config_;
    std::string active_host_;
    std::string token_;
    bool authenticated_{false};
    std::unique_ptr<detail::WebSocketClient> push_;
    std::atomic<std::int64_t> subscription_id_{1000000};
    std::atomic<std::int64_t> codelist_id_{1};
};

Session::Session(Config config) : impl_(std::make_unique<Impl>(std::move(config))) {}
Session::~Session() = default;
Session::Session(Session&&) noexcept = default;
Session& Session::operator=(Session&&) noexcept = default;

void Session::connect() { impl_->connect(); }
LoginInfo Session::login() { return impl_->login(); }
LoginInfo Session::connect_and_login() { return impl_->connect_and_login(); }
void Session::close() noexcept { if (impl_) impl_->close(); }
bool Session::connected() const noexcept { return impl_ && impl_->connected(); }
bool Session::authenticated() const noexcept { return impl_ && impl_->authenticated(); }
const std::string& Session::active_host() const noexcept {
    static const std::string empty;
    return impl_ ? impl_->active_host() : empty;
}
void Session::subscribe(const std::vector<SubscribeItem>& items) {
    impl_->subscribe(items, false);
}
void Session::unsubscribe(const std::vector<SubscribeItem>& items) {
    impl_->subscribe(items, true);
}
std::string Session::receive_raw_event(std::chrono::milliseconds timeout) {
    return impl_->receive_event(timeout);
}
std::vector<KlineRow> Session::query_kline(const KlineRequest& request) {
    return impl_->query_kline(request);
}
QueryResult<SnapshotRow> Session::query_snapshot(const SnapshotRequest& request) {
    return impl_->query_snapshot(request);
}
std::vector<CodeTableRow> Session::query_code_table() {
    return impl_->query_code_table();
}
std::vector<EtfRecord> Session::query_etf_info(const SecurityItem& item) {
    return impl_->query_etf_info(item);
}
std::vector<SecuritiesInfoRow> Session::query_securities_info(
    const std::vector<SecurityItem>& items) {
    return impl_->query_securities_info(items);
}
std::vector<ExFactorRow> Session::query_ex_factor(std::string_view security_code) {
    return impl_->query_ex_factor(security_code);
}
std::vector<std::string> Session::query_third_info(
    const std::vector<std::pair<std::string, std::string>>& parameters,
    std::int64_t offset,
    std::int64_t count
) {
    return impl_->query_third_info(parameters, offset, count);
}

} // namespace tgw
