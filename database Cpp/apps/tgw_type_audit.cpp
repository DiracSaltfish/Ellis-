#include "tgw/client.hpp"

#include <simdjson.h>

#include <algorithm>
#include <array>
#include <charconv>
#include <cctype>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <string_view>
#include <type_traits>
#include <typeinfo>
#include <utility>
#include <variant>
#include <vector>

namespace {

struct FieldStats {
    std::set<std::string> types;
    std::size_t count{0};
    std::size_t absent_count{0};
    std::size_t empty_string_count{0};
    std::size_t numeric_text_count{0};
    std::size_t max_bytes{0};
    std::optional<std::int64_t> signed_min;
    std::optional<std::int64_t> signed_max;
    std::optional<std::uint64_t> unsigned_min;
    std::optional<std::uint64_t> unsigned_max;
    std::optional<double> double_min;
    std::optional<double> double_max;
    std::optional<std::int64_t> numeric_text_min;
    std::optional<std::int64_t> numeric_text_max;
};

using Audit = std::map<std::string, FieldStats>;

std::string json_string(std::string_view value) {
    return "\"" + tgw::protocol::escape_json(value) + "\"";
}

bool integer_text(std::string_view text) {
    if (text.empty()) return false;
    std::size_t offset = text.front() == '-' ? 1U : 0U;
    if (offset == text.size()) return false;
    return std::all_of(text.begin() + static_cast<std::ptrdiff_t>(offset), text.end(),
                       [](unsigned char value) { return std::isdigit(value) != 0; });
}

void observe(Audit& audit, std::string_view name, std::string_view value,
             std::string_view type = "string") {
    auto& stats = audit[std::string(name)];
    stats.types.emplace(type);
    ++stats.count;
    stats.max_bytes = std::max(stats.max_bytes, value.size());
    if (value.empty()) ++stats.empty_string_count;
    if (integer_text(value)) {
        ++stats.numeric_text_count;
        std::int64_t number = 0;
        const auto parsed = std::from_chars(value.data(), value.data() + value.size(), number);
        if (parsed.ec == std::errc{} && parsed.ptr == value.data() + value.size()) {
            stats.numeric_text_min = stats.numeric_text_min
                ? std::min(*stats.numeric_text_min, number) : number;
            stats.numeric_text_max = stats.numeric_text_max
                ? std::max(*stats.numeric_text_max, number) : number;
        }
    }
}

void observe(Audit& audit, std::string_view name, std::int64_t value) {
    auto& stats = audit[std::string(name)];
    stats.types.emplace("int64");
    ++stats.count;
    stats.signed_min = stats.signed_min ? std::min(*stats.signed_min, value) : value;
    stats.signed_max = stats.signed_max ? std::max(*stats.signed_max, value) : value;
}

void observe(Audit& audit, std::string_view name, std::uint32_t value) {
    auto& stats = audit[std::string(name)];
    stats.types.emplace("uint32");
    ++stats.count;
    const auto wide = static_cast<std::uint64_t>(value);
    stats.unsigned_min = stats.unsigned_min ? std::min(*stats.unsigned_min, wide) : wide;
    stats.unsigned_max = stats.unsigned_max ? std::max(*stats.unsigned_max, wide) : wide;
}

void observe(Audit& audit, std::string_view name, std::uint8_t value) {
    auto& stats = audit[std::string(name)];
    stats.types.emplace("uint8");
    ++stats.count;
    const auto wide = static_cast<std::uint64_t>(value);
    stats.unsigned_min = stats.unsigned_min ? std::min(*stats.unsigned_min, wide) : wide;
    stats.unsigned_max = stats.unsigned_max ? std::max(*stats.unsigned_max, wide) : wide;
}

void observe(Audit& audit, std::string_view name, char value) {
    auto& stats = audit[std::string(name)];
    stats.types.emplace("char");
    ++stats.count;
    const auto wide = static_cast<std::uint64_t>(static_cast<unsigned char>(value));
    stats.unsigned_min = stats.unsigned_min ? std::min(*stats.unsigned_min, wide) : wide;
    stats.unsigned_max = stats.unsigned_max ? std::max(*stats.unsigned_max, wide) : wide;
}

void observe(Audit& audit, std::string_view name, double value) {
    auto& stats = audit[std::string(name)];
    stats.types.emplace("double");
    ++stats.count;
    stats.double_min = stats.double_min ? std::min(*stats.double_min, value) : value;
    stats.double_max = stats.double_max ? std::max(*stats.double_max, value) : value;
}

template <typename T>
void observe_optional(Audit& audit, std::string_view name, const std::optional<T>& value) {
    if (value) {
        observe(audit, name, *value);
    } else {
        auto& stats = audit[std::string(name)];
        stats.types.emplace("absent-on-wire");
        ++stats.absent_count;
    }
}

void observe_json(Audit& audit, std::string_view name,
                  simdjson::dom::element value) {
    using simdjson::dom::element_type;
    auto& stats = audit[std::string(name)];
    switch (value.type()) {
    case element_type::INT64: {
        std::int64_t number = 0;
        if (value.get_int64().get(number)) throw tgw::ProtocolError("audit int64 failure");
        observe(audit, name, number);
        break;
    }
    case element_type::UINT64: {
        std::uint64_t number = 0;
        if (value.get_uint64().get(number)) throw tgw::ProtocolError("audit uint64 failure");
        stats.types.emplace("uint64");
        ++stats.count;
        stats.unsigned_min = stats.unsigned_min ? std::min(*stats.unsigned_min, number) : number;
        stats.unsigned_max = stats.unsigned_max ? std::max(*stats.unsigned_max, number) : number;
        break;
    }
    case element_type::DOUBLE: {
        double number = 0.0;
        if (value.get_double().get(number)) throw tgw::ProtocolError("audit double failure");
        observe(audit, name, number);
        break;
    }
    case element_type::STRING: {
        std::string_view text;
        if (value.get_string().get(text)) throw tgw::ProtocolError("audit string failure");
        observe(audit, name, text, "string");
        break;
    }
    case element_type::ARRAY:
        stats.types.emplace("array");
        ++stats.count;
        break;
    case element_type::OBJECT:
        stats.types.emplace("object");
        ++stats.count;
        break;
    case element_type::BOOL:
        stats.types.emplace("bool");
        ++stats.count;
        break;
    case element_type::NULL_VALUE:
        stats.types.emplace("null");
        ++stats.count;
        break;
    case element_type::BIGINT:
        // Deliberately do not coerce an out-of-uint64 JSON integer.  Its
        // presence is itself an audit finding for a schema-specific consumer.
        stats.types.emplace("bigint-uncoerced");
        ++stats.count;
        break;
    }
}

void print_audit(std::string_view endpoint, std::string_view wire_shape,
                 std::size_t rows, const Audit& audit) {
    std::cout << "{\"endpoint\":" << json_string(endpoint)
              << ",\"ok\":true,\"wire_shape\":" << json_string(wire_shape)
              << ",\"rows\":" << rows << ",\"fields\":[";
    bool first_field = true;
    for (const auto& [name, stats] : audit) {
        if (!first_field) std::cout << ',';
        first_field = false;
        std::cout << "{\"name\":" << json_string(name) << ",\"types\":[";
        bool first_type = true;
        for (const auto& type : stats.types) {
            if (!first_type) std::cout << ',';
            first_type = false;
            std::cout << json_string(type);
        }
        std::cout << "],\"count\":" << stats.count;
        if (stats.absent_count) std::cout << ",\"absent_count\":" << stats.absent_count;
        if (stats.signed_min) {
            std::cout << ",\"min\":" << json_string(std::to_string(*stats.signed_min))
                      << ",\"max\":" << json_string(std::to_string(*stats.signed_max));
        } else if (stats.unsigned_min) {
            std::cout << ",\"min\":" << json_string(std::to_string(*stats.unsigned_min))
                      << ",\"max\":" << json_string(std::to_string(*stats.unsigned_max));
        } else if (stats.double_min) {
            std::cout << std::setprecision(17)
                      << ",\"min\":" << *stats.double_min
                      << ",\"max\":" << *stats.double_max;
        }
        if (stats.types.contains("string") || stats.types.contains("raw-string")) {
            std::cout << ",\"empty_count\":" << stats.empty_string_count
                      << ",\"max_bytes\":" << stats.max_bytes
                      << ",\"integer_text_count\":" << stats.numeric_text_count;
            if (stats.numeric_text_min) {
                std::cout << ",\"integer_text_min\":"
                          << json_string(std::to_string(*stats.numeric_text_min))
                          << ",\"integer_text_max\":"
                          << json_string(std::to_string(*stats.numeric_text_max));
            }
        }
        std::cout << '}';
    }
    std::cout << "]}\n";
}

template <typename Callable>
bool run_endpoint(std::string_view endpoint, Callable&& callable) {
    try {
        callable();
        return true;
    } catch (const std::exception& error) {
        std::cout << "{\"endpoint\":" << json_string(endpoint)
                  << ",\"ok\":false,\"error_type\":"
                  << json_string(typeid(error).name())
                  << ",\"message\":" << json_string(error.what()) << "}\n";
        return false;
    }
}

void audit_calendar(tgw::Session& session) {
    const std::vector<std::pair<std::string, std::string>> parameters{
        {"function_id", "A010061003"}, {"start_date", "20260801"},
        {"end_date", "20260826"}, {"market", "SSE"}
    };
    const auto rows = session.query_third_info(parameters);
    Audit audit;
    for (const auto& row : rows) {
        simdjson::dom::parser parser;
        simdjson::dom::object object;
        if (parser.parse(row).get_object().get(object)) {
            throw tgw::ProtocolError("calendar audit row is not an object");
        }
        for (const auto field : object) observe_json(audit, field.key, field.value);
    }
    print_audit("calendar", "JSON string containing nested JSON object rows", rows.size(), audit);
}

void audit_kline(tgw::Session& session) {
    const auto rows = session.query_kline({
        "510300", 101, 0, 1, 10008, 20260825, 20260825, 0, 0
    });
    Audit audit;
    for (const auto& row : rows) {
        observe(audit, "market_type", row.market_type);
        observe(audit, "security_code", row.security_code);
        observe_optional(audit, "orig_time", row.orig_time);
        observe(audit, "kline_time", row.kline_time);
        observe(audit, "open_price", row.open_price);
        observe(audit, "high_price", row.high_price);
        observe(audit, "low_price", row.low_price);
        observe(audit, "close_price", row.close_price);
        observe(audit, "volume_trade", row.volume_trade);
        observe(audit, "value_trade", row.value_trade);
        observe_optional(audit, "variety_category", row.variety_category);
    }
    print_audit("kline-daily", "data[] JSON string; CSV with exactly 9 fields", rows.size(), audit);
}

void audit_snapshot(tgw::Session& session) {
    const auto result = session.query_snapshot({
        "510300", 101, 20260825, 93000000, 93030000, 0, 0
    });
    if (!result.ok()) throw tgw::ProtocolError("snapshot returned mapped data-empty error");
    Audit audit;
    for (const auto& row : result.rows) {
        observe(audit, "market_type", row.market_type);
        observe(audit, "security_code", row.security_code);
        observe_optional(audit, "variety_category", row.variety_category);
        observe(audit, "orig_time", row.orig_time);
        observe(audit, "trading_phase_code", row.trading_phase_code);
        observe(audit, "pre_close_price", row.pre_close_price);
        observe(audit, "open_price", row.open_price);
        observe(audit, "high_price", row.high_price);
        observe(audit, "low_price", row.low_price);
        observe(audit, "last_price", row.last_price);
        observe(audit, "close_price", row.close_price);
        for (const auto value : row.bid_price) observe(audit, "bid_price[]", value);
        for (const auto value : row.bid_volume) observe(audit, "bid_volume[]", value);
        for (const auto value : row.offer_price) observe(audit, "offer_price[]", value);
        for (const auto value : row.offer_volume) observe(audit, "offer_volume[]", value);
        observe(audit, "num_trades", row.num_trades);
        observe(audit, "total_volume_trade", row.total_volume_trade);
        observe(audit, "total_value_trade", row.total_value_trade);
        observe(audit, "iopv", row.iopv);
        observe(audit, "high_limited", row.high_limited);
        observe(audit, "low_limited", row.low_limited);
        for (std::size_t index = 0; index < row.unverified_tail_raw.size(); ++index) {
            observe(audit, "wire_tail[" + std::to_string(index + 20U) + "]",
                    row.unverified_tail_raw[index], "raw-string");
        }
    }
    print_audit("snapshot-sse",
                "data[] JSON string; CSV 36 fields; four fields are pipe-packed int64[10]",
                result.rows.size(), audit);
}

void audit_etf(tgw::Session& session) {
    const auto rows = session.query_etf_info({101, "510300"});
    Audit basic;
    Audit constituent;
    std::size_t constituent_rows = 0;
    for (const auto& row : rows) {
        const auto& item = row.basic;
        observe(basic, "security_code", item.security_code);
        observe(basic, "creation_redemption_unit", item.creation_redemption_unit);
        observe(basic, "max_cash_ratio", item.max_cash_ratio);
        observe(basic, "publish", item.publish);
        observe(basic, "creation", item.creation);
        observe(basic, "redemption", item.redemption);
        observe(basic, "creation_redemption_switch", item.creation_redemption_switch);
        observe(basic, "record_num", item.record_num);
        observe(basic, "total_record_num", item.total_record_num);
        observe(basic, "estimate_cash_component", item.estimate_cash_component);
        observe(basic, "trading_day", item.trading_day);
        observe(basic, "pre_trading_day", item.pre_trading_day);
        observe(basic, "cash_component", item.cash_component);
        observe(basic, "nav_per_cu", item.nav_per_cu);
        observe(basic, "nav", item.nav);
        observe(basic, "market_type", item.market_type);
        observe(basic, "symbol", item.symbol);
        observe(basic, "fund_management_company", item.fund_management_company);
        observe(basic, "underlying_security_id", item.underlying_security_id);
        observe(basic, "underlying_security_id_source", item.underlying_security_id_source);
        observe(basic, "dividend_per_cu", item.dividend_per_cu);
        observe(basic, "creation_limit", item.creation_limit);
        observe(basic, "redemption_limit", item.redemption_limit);
        observe(basic, "creation_limit_per_user", item.creation_limit_per_user);
        observe(basic, "redemption_limit_per_user", item.redemption_limit_per_user);
        observe(basic, "net_creation_limit", item.net_creation_limit);
        observe(basic, "net_redemption_limit", item.net_redemption_limit);
        observe(basic, "net_creation_limit_per_user", item.net_creation_limit_per_user);
        observe(basic, "net_redemption_limit_per_user", item.net_redemption_limit_per_user);
        observe(basic, "all_cash_flag", item.all_cash_flag);
        observe(basic, "all_cash_amount", item.all_cash_amount);
        observe(basic, "all_cash_premium_rate", item.all_cash_premium_rate);
        observe(basic, "all_cash_discount_rate", item.all_cash_discount_rate);
        observe(basic, "rtgs_flag", item.rtgs_flag);
        observe(basic, "reserved", item.reserved);
        for (const auto& item : row.constituents) {
            observe(constituent, "security_code", item.security_code);
            observe(constituent, "market_type", item.market_type);
            observe(constituent, "underlying_symbol", item.underlying_symbol);
            observe(constituent, "component_share", item.component_share);
            observe(constituent, "substitute_flag", item.substitute_flag);
            observe(constituent, "premium_ratio", item.premium_ratio);
            observe(constituent, "discount_ratio", item.discount_ratio);
            observe(constituent, "creation_cash_substitute", item.creation_cash_substitute);
            observe(constituent, "redemption_cash_substitute", item.redemption_cash_substitute);
            observe(constituent, "substitution_cash_amount", item.substitution_cash_amount);
            observe(constituent, "underlying_security_id", item.underlying_security_id);
            observe(constituent, "buy_or_sell_to_open", item.buy_or_sell_to_open);
            observe(constituent, "reserved", item.reserved);
            ++constituent_rows;
        }
    }
    print_audit("etf-sse.basic", "data[] JSON object with numeric string keys 1..36",
                rows.size(), basic);
    print_audit("etf-sse.constituents", "slot 36 JSON array; objects with keys 1..13",
                constituent_rows, constituent);
}

void audit_securities(tgw::Session& session) {
    const auto rows = session.query_securities_info({
        {101, "510300"}, {102, "159919"}
    });
    Audit audit;
    for (const auto& row : rows) {
        observe(audit, "security_code", row.security_code);
        observe(audit, "market_type", row.market_type);
        observe(audit, "symbol", row.symbol);
        observe(audit, "english_name", row.english_name);
        observe(audit, "security_type", row.security_type);
        observe(audit, "currency", row.currency);
        observe(audit, "variety_category", row.variety_category);
        observe(audit, "pre_close_price", row.pre_close_price);
        observe(audit, "underlying_security_id", row.underlying_security_id);
        observe(audit, "contract_type", row.contract_type);
        observe(audit, "exercise_price", row.exercise_price);
        observe(audit, "expire_date", row.expire_date);
        observe(audit, "high_limited", row.high_limited);
        observe(audit, "low_limited", row.low_limited);
        observe(audit, "security_status", row.security_status);
        observe(audit, "price_tick", row.price_tick);
        observe(audit, "buy_qty_unit", row.buy_qty_unit);
        observe(audit, "sell_qty_unit", row.sell_qty_unit);
        observe(audit, "market_buy_qty_unit", row.market_buy_qty_unit);
        observe(audit, "market_sell_qty_unit", row.market_sell_qty_unit);
        observe(audit, "buy_qty_lower_limit", row.buy_qty_lower_limit);
        observe(audit, "buy_qty_upper_limit", row.buy_qty_upper_limit);
        observe(audit, "sell_qty_lower_limit", row.sell_qty_lower_limit);
        observe(audit, "sell_qty_upper_limit", row.sell_qty_upper_limit);
        observe(audit, "market_buy_qty_lower_limit", row.market_buy_qty_lower_limit);
        observe(audit, "market_buy_qty_upper_limit", row.market_buy_qty_upper_limit);
        observe(audit, "market_sell_qty_lower_limit", row.market_sell_qty_lower_limit);
        observe(audit, "market_sell_qty_upper_limit", row.market_sell_qty_upper_limit);
        observe(audit, "list_day", row.list_day);
        observe(audit, "par_value", row.par_value);
        observe(audit, "outstanding_share", row.outstanding_share);
        observe(audit, "public_float_share_quantity", row.public_float_share_quantity);
        observe(audit, "contract_multiplier", row.contract_multiplier);
        observe(audit, "regular_share", row.regular_share);
        observe(audit, "interest", row.interest);
        observe(audit, "coupon_rate", row.coupon_rate);
        observe(audit, "product_code", row.product_code);
        observe(audit, "delivery_year", row.delivery_year);
        observe(audit, "delivery_month", row.delivery_month);
        observe(audit, "create_date", row.create_date);
        observe(audit, "start_deliv_date", row.start_deliv_date);
        observe(audit, "end_deliv_date", row.end_deliv_date);
        observe(audit, "position_type", row.position_type);
    }
    print_audit("secinfo-pair", "data[] JSON object with numeric string keys 1..43",
                rows.size(), audit);
}

void audit_ex_factor(tgw::Session& session) {
    const auto rows = session.query_ex_factor("000001");
    Audit audit;
    for (const auto& row : rows) {
        observe(audit, "inner_code", row.inner_code);
        observe(audit, "security_code", row.security_code);
        observe(audit, "ex_date", row.ex_date);
        observe(audit, "ex_factor", row.ex_factor);
        observe(audit, "cum_factor", row.cum_factor);
        observe(audit, "ex_factor_raw", row.ex_factor_raw, "raw-string");
        observe(audit, "cum_factor_raw", row.cum_factor_raw, "raw-string");
    }
    print_audit("ex-factor", "data[] JSON string; CSV with exactly 5 fields",
                rows.size(), audit);
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 3 || std::string_view(argv[1]) != "--config") {
        std::cerr << "Usage: " << argv[0] << " --config PATH\n";
        return 2;
    }
    try {
        tgw::Session session(tgw::load_ini_config(argv[2]));
        const auto login = session.connect_and_login();
        if (!login.authenticated) {
            std::cout << "{\"endpoint\":\"login\",\"ok\":false,\"status\":"
                      << login.status << ",\"tag\":" << json_string(login.response_tag)
                      << "}\n";
            return 3;
        }
        std::cout << "{\"endpoint\":\"login\",\"ok\":true,"
                     "\"authenticated\":true}\n";
        std::size_t failed = 0;
        failed += !run_endpoint("calendar", [&] { audit_calendar(session); });
        failed += !run_endpoint("kline-daily", [&] { audit_kline(session); });
        failed += !run_endpoint("snapshot-sse", [&] { audit_snapshot(session); });
        failed += !run_endpoint("secinfo-pair", [&] { audit_securities(session); });
        failed += !run_endpoint("etf-sse", [&] { audit_etf(session); });
        failed += !run_endpoint("ex-factor", [&] { audit_ex_factor(session); });
        session.close();
        return failed == 0U ? 0 : 4;
    } catch (const std::exception& error) {
        std::cout << "{\"endpoint\":\"audit\",\"ok\":false,\"error_type\":"
                  << json_string(typeid(error).name())
                  << ",\"message\":" << json_string(error.what()) << "}\n";
        return 1;
    }
}
