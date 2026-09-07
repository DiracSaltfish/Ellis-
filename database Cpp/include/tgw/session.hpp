#pragma once

#include "tgw/types.hpp"

#include <chrono>
#include <cstdint>
#include <memory>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace tgw {

TGW_API Config load_ini_config(const std::string& path);
TGW_API std::int64_t next_task_id();
TGW_API const char* error_message(int error_code) noexcept;

class TGW_API Session {
public:
    explicit Session(Config config);
    ~Session();

    Session(const Session&) = delete;
    Session& operator=(const Session&) = delete;
    Session(Session&&) noexcept;
    Session& operator=(Session&&) noexcept;

    void connect();
    LoginInfo login();
    LoginInfo connect_and_login();
    void close() noexcept;

    [[nodiscard]] bool connected() const noexcept;
    [[nodiscard]] bool authenticated() const noexcept;
    [[nodiscard]] const std::string& active_host() const noexcept;

    void subscribe(const std::vector<SubscribeItem>& items);
    void unsubscribe(const std::vector<SubscribeItem>& items);
    std::string receive_raw_event(std::chrono::milliseconds timeout);

    std::vector<KlineRow> query_kline(const KlineRequest& request);
    QueryResult<SnapshotRow> query_snapshot(const SnapshotRequest& request);
    std::vector<CodeTableRow> query_code_table();
    std::vector<EtfRecord> query_etf_info(const SecurityItem& item);
    std::vector<SecuritiesInfoRow> query_securities_info(
        const std::vector<SecurityItem>& items);
    std::vector<ExFactorRow> query_ex_factor(std::string_view security_code);
    std::vector<std::string> query_third_info(
        const std::vector<std::pair<std::string, std::string>>& parameters,
        std::int64_t offset = 0,
        std::int64_t count = 1000
    );

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace tgw
