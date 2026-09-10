#pragma once

#include "tgw/export.hpp"

#include <array>
#include <chrono>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace tgw {

inline constexpr const char* kClientVersion = "V4.3.0.260626-rc2.0-YHZQ";

class TGW_API TransportError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class TGW_API ProtocolError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class TGW_API TimeoutError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

struct Config {
    std::vector<std::string> hosts;
    std::uint16_t port{0};
    std::string username;
    std::string password;
    bool force_logout{false};
    std::string ca_file;
    std::string tls_server_name{"www.dgw.com"};
    std::string client_version{kClientVersion};
    std::vector<std::string> query_endpoints{
        "/amd/dgw/dgw1_query", "/amd/dgw/dgw2_query"
    };
    std::chrono::milliseconds timeout{15000};
    std::chrono::milliseconds heartbeat{5000};
    std::size_t max_payload{64U * 1024U * 1024U};
};

struct LoginInfo {
    bool authenticated{false};
    std::int64_t status{-100};
    std::string response_tag;
};

struct SubscribeItem {
    std::int32_t market{0};
    std::uint64_t flag{0};
    std::string security_code;
    std::uint8_t category_type{0};
};

struct KlineRequest {
    std::string security_code;
    std::int32_t market_type{0};
    std::int32_t cq_flag{0};
    std::int32_t auto_complete{1};
    std::int32_t cyc_type{0};
    std::int32_t begin_date{0};
    std::int32_t end_date{0};
    std::int32_t begin_time{0};
    std::int32_t end_time{0};
};

struct SnapshotRequest {
    std::string security_code;
    std::int32_t market_type{0};
    std::int32_t date{0};
    std::int32_t begin_time{0};
    std::int32_t end_time{0};
    std::int32_t data_type{0};
    std::int32_t level_type{0};
};

struct SecurityItem {
    std::int32_t market{0};
    std::string security_code;
};

// Numeric alternatives intentionally preserve the signedness/width declared by
// the vendor's public structures.  Dynamic JSON from ThirdInfo is not coerced
// into Scalar; it is returned as validated JSON text instead.
using Scalar = std::variant<
    std::int64_t,
    std::uint32_t,
    std::uint8_t,
    double,
    std::string
>;
using Record = std::vector<std::pair<std::string, Scalar>>;

struct KlineRow {
    std::uint8_t market_type{0};
    std::string security_code;
    // The verified nine-field wire row does not carry these two public-header
    // fields.  nullopt is used instead of manufacturing Python-compatible 0s.
    std::optional<std::int64_t> orig_time;
    std::int64_t kline_time{0};
    std::int64_t open_price{0};
    std::int64_t high_price{0};
    std::int64_t low_price{0};
    std::int64_t close_price{0};
    std::int64_t volume_trade{0};
    std::int64_t value_trade{0};
    std::optional<std::uint8_t> variety_category;
};

struct SnapshotRow {
    std::uint8_t market_type{0};
    std::string security_code;
    // Not present in the verified CSV wire row.
    std::optional<std::uint8_t> variety_category;
    std::int64_t orig_time{0};
    std::string trading_phase_code;
    std::int64_t pre_close_price{0};
    std::int64_t open_price{0};
    std::int64_t high_price{0};
    std::int64_t low_price{0};
    std::int64_t last_price{0};
    std::int64_t close_price{0};
    std::array<std::int64_t, 10> bid_price{};
    std::array<std::int64_t, 10> bid_volume{};
    std::array<std::int64_t, 10> offer_price{};
    std::array<std::int64_t, 10> offer_volume{};
    std::int64_t num_trades{0};
    std::int64_t total_volume_trade{0};
    std::int64_t total_value_trade{0};
    std::int64_t iopv{0};
    std::int64_t high_limited{0};
    std::int64_t low_limited{0};
    // Wire positions 20..35 exist but have no verified public semantics.  They
    // are retained losslessly instead of being silently discarded or guessed.
    std::array<std::string, 16> unverified_tail_raw{};
};

struct CodeTableRow {
    std::string security_code;
    std::string symbol;
    std::string english_name;
    std::uint8_t market_type{0};
    std::string security_type;
    std::string currency;
};

struct ExFactorRow {
    std::string inner_code;
    std::string security_code;
    std::uint32_t ex_date{0};
    double ex_factor{0.0};
    double cum_factor{0.0};
    // Exact CSV tokens are retained because binary64 cannot preserve every
    // decimal digit of the N38(15) wire representation.
    std::string ex_factor_raw;
    std::string cum_factor_raw;
};

struct EtfConstituentRow {
    std::string security_code;
    std::uint8_t market_type{0};
    std::string underlying_symbol;
    std::int64_t component_share{0};
    char substitute_flag{'\0'};
    std::int64_t premium_ratio{0};
    std::int64_t discount_ratio{0};
    std::int64_t creation_cash_substitute{0};
    std::int64_t redemption_cash_substitute{0};
    std::int64_t substitution_cash_amount{0};
    std::string underlying_security_id;
    char buy_or_sell_to_open{'\0'};
    std::string reserved;
};

struct EtfBasicRow {
    std::string security_code;
    std::int64_t creation_redemption_unit{0};
    std::int64_t max_cash_ratio{0};
    char publish{'\0'};
    char creation{'\0'};
    char redemption{'\0'};
    char creation_redemption_switch{'\0'};
    std::int64_t record_num{0};
    std::int64_t total_record_num{0};
    std::int64_t estimate_cash_component{0};
    std::int64_t trading_day{0};
    std::int64_t pre_trading_day{0};
    std::int64_t cash_component{0};
    std::int64_t nav_per_cu{0};
    std::int64_t nav{0};
    std::uint8_t market_type{0};
    std::string symbol;
    std::string fund_management_company;
    std::string underlying_security_id;
    std::string underlying_security_id_source;
    std::int64_t dividend_per_cu{0};
    std::int64_t creation_limit{0};
    std::int64_t redemption_limit{0};
    std::int64_t creation_limit_per_user{0};
    std::int64_t redemption_limit_per_user{0};
    std::int64_t net_creation_limit{0};
    std::int64_t net_redemption_limit{0};
    std::int64_t net_creation_limit_per_user{0};
    std::int64_t net_redemption_limit_per_user{0};
    char all_cash_flag{'\0'};
    std::string all_cash_amount;
    std::string all_cash_premium_rate;
    std::string all_cash_discount_rate;
    char rtgs_flag{'\0'};
    std::string reserved;
};

struct EtfRecord {
    EtfBasicRow basic;
    std::vector<EtfConstituentRow> constituents;
};

struct SecuritiesInfoRow {
    std::string security_code;
    std::uint8_t market_type{0};
    std::string symbol;
    std::string english_name;
    std::string security_type;
    std::string currency;
    std::uint8_t variety_category{0};
    std::int64_t pre_close_price{0};
    std::string underlying_security_id;
    std::string contract_type;
    std::int64_t exercise_price{0};
    std::uint32_t expire_date{0};
    std::int64_t high_limited{0};
    std::int64_t low_limited{0};
    std::string security_status;
    std::int64_t price_tick{0};
    std::int64_t buy_qty_unit{0};
    std::int64_t sell_qty_unit{0};
    std::int64_t market_buy_qty_unit{0};
    std::int64_t market_sell_qty_unit{0};
    std::int64_t buy_qty_lower_limit{0};
    std::int64_t buy_qty_upper_limit{0};
    std::int64_t sell_qty_lower_limit{0};
    std::int64_t sell_qty_upper_limit{0};
    std::int64_t market_buy_qty_lower_limit{0};
    std::int64_t market_buy_qty_upper_limit{0};
    std::int64_t market_sell_qty_lower_limit{0};
    std::int64_t market_sell_qty_upper_limit{0};
    std::uint32_t list_day{0};
    std::int64_t par_value{0};
    std::int64_t outstanding_share{0};
    std::int64_t public_float_share_quantity{0};
    std::int64_t contract_multiplier{0};
    std::string regular_share;
    std::int64_t interest{0};
    std::int64_t coupon_rate{0};
    std::string product_code;
    std::uint32_t delivery_year{0};
    std::uint32_t delivery_month{0};
    std::uint32_t create_date{0};
    std::uint32_t start_deliv_date{0};
    std::uint32_t end_deliv_date{0};
    std::uint32_t position_type{0};
};

template <typename T>
struct QueryResult {
    std::vector<T> rows;
    int error_code{0};

    [[nodiscard]] bool ok() const noexcept { return error_code == 0; }
};

} // namespace tgw
