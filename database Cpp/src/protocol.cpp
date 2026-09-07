#include "tgw/protocol.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <cctype>
#include <iomanip>
#include <ifaddrs.h>
#include <limits>
#include <memory>
#include <net/if.h>
#if defined(__APPLE__)
#include <net/if_dl.h>
#endif
#include <optional>
#include <set>
#include <simdjson.h>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <type_traits>
#include <sys/types.h>
#include <unistd.h>
#include <zstd.h>

namespace tgw::protocol {
namespace {

using simdjson::dom::array;
using simdjson::dom::element;
using simdjson::dom::object;

class ParsedJson {
public:
    explicit ParsedJson(std::string_view json) : input_(std::string(json)) {
        const auto error = parser_.parse(input_).get(root_);
        if (error) {
            throw ProtocolError("invalid TGW JSON: " + std::string(simdjson::error_message(error)));
        }
    }

    object root_object() const {
        object value;
        const auto error = root_.get_object().get(value);
        if (error) {
            throw ProtocolError("TGW JSON root is not an object");
        }
        return value;
    }

private:
    simdjson::padded_string input_;
    simdjson::dom::parser parser_;
    element root_;
};

element required(object source, std::string_view key, std::string_view context) {
    element value;
    const auto error = source.at_key(key).get(value);
    if (error) {
        throw ProtocolError(std::string(context) + " is missing field " + std::string(key));
    }
    return value;
}

std::optional<element> optional(object source, std::string_view key) {
    element value;
    const auto error = source.at_key(key).get(value);
    if (error == simdjson::NO_SUCH_FIELD) {
        return std::nullopt;
    }
    if (error) {
        throw ProtocolError("cannot inspect TGW JSON field " + std::string(key));
    }
    return value;
}

object as_object(element value, std::string_view context) {
    object result;
    if (value.get_object().get(result)) {
        throw ProtocolError(std::string(context) + " is not an object");
    }
    return result;
}

array as_array(element value, std::string_view context) {
    array result;
    if (value.get_array().get(result)) {
        throw ProtocolError(std::string(context) + " is not an array");
    }
    return result;
}

std::int64_t as_int(element value, std::string_view context) {
    std::int64_t result = 0;
    if (value.get_int64().get(result)) {
        throw ProtocolError(std::string(context) + " is not an integer");
    }
    return result;
}

std::string as_string(element value, std::string_view context) {
    std::string_view result;
    if (value.get_string().get(result)) {
        throw ProtocolError(std::string(context) + " is not a string");
    }
    return std::string(result);
}

std::vector<std::string> split(std::string_view input, char separator) {
    std::vector<std::string> output;
    std::size_t begin = 0;
    while (begin <= input.size()) {
        const auto next = input.find(separator, begin);
        output.emplace_back(input.substr(
            begin, next == std::string_view::npos ? std::string_view::npos : next - begin));
        if (next == std::string_view::npos) {
            break;
        }
        begin = next + 1U;
    }
    return output;
}

void validate_max_bytes(std::string_view value, std::size_t capacity,
                        std::string_view context) {
    if (value.size() > capacity) {
        throw ProtocolError(std::string(context) +
                            " exceeds the public header byte capacity");
    }
}

std::int64_t parse_integer(std::string_view value, std::string_view context,
                           bool allow_negative = true) {
    if (value.empty()) {
        throw ProtocolError(std::string(context) + " is not an integer");
    }
    std::int64_t result = 0;
    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size() ||
        (!allow_negative && result < 0)) {
        throw ProtocolError(std::string(context) + " is not an integer");
    }
    return result;
}

template <typename Target>
Target checked_integer(std::int64_t value, std::string_view context) {
    static_assert(std::is_integral_v<Target>);
    if constexpr (std::is_unsigned_v<Target>) {
        if (value < 0 || static_cast<std::uint64_t>(value) >
                static_cast<std::uint64_t>(std::numeric_limits<Target>::max())) {
            throw ProtocolError(std::string(context) + " is outside the target integer range");
        }
    } else if (value < static_cast<std::int64_t>(std::numeric_limits<Target>::min()) ||
               value > static_cast<std::int64_t>(std::numeric_limits<Target>::max())) {
        throw ProtocolError(std::string(context) + " is outside the target integer range");
    }
    return static_cast<Target>(value);
}

template <typename Target>
Target parse_integer_as(std::string_view value, std::string_view context) {
    return checked_integer<Target>(parse_integer(value, context), context);
}

template <typename Target>
Target as_integer_as(element value, std::string_view context) {
    return checked_integer<Target>(as_int(value, context), context);
}

std::string json_quote(std::string_view value) {
    return '"' + escape_json(value) + '"';
}

std::string headers_user_token(std::string_view username, std::string_view token,
                               std::int64_t request_id, bool id_first = false) {
    std::ostringstream output;
    if (id_first) {
        output << "{\"id\":" << request_id << ",\"userName\":" << json_quote(username)
               << ",\"token\":" << json_quote(token) << '}';
    } else {
        output << "{\"userName\":" << json_quote(username) << ",\"token\":"
               << json_quote(token) << ",\"id\":" << request_id << '}';
    }
    return output.str();
}

std::string join(const std::vector<std::string>& values, std::string_view separator) {
    std::ostringstream output;
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index != 0) {
            output << separator;
        }
        output << values[index];
    }
    return output.str();
}

std::vector<std::string> decompress_zstd(std::span<const std::uint8_t> compressed,
                                         std::size_t max_output) {
    ZSTD_DStream* stream = ZSTD_createDStream();
    if (stream == nullptr) {
        throw ProtocolError("cannot allocate Zstandard decoder");
    }
    struct Cleanup {
        ZSTD_DStream* stream;
        ~Cleanup() { ZSTD_freeDStream(stream); }
    } cleanup{stream};
    const std::size_t initialized = ZSTD_initDStream(stream);
    if (ZSTD_isError(initialized)) {
        throw ProtocolError("cannot initialize Zstandard decoder");
    }
    ZSTD_inBuffer input{compressed.data(), compressed.size(), 0};
    std::vector<std::uint8_t> decoded;
    std::array<std::uint8_t, 64U * 1024U> chunk{};
    while (input.pos < input.size) {
        ZSTD_outBuffer output{chunk.data(), chunk.size(), 0};
        const std::size_t remaining = ZSTD_decompressStream(stream, &output, &input);
        if (ZSTD_isError(remaining)) {
            throw ProtocolError("invalid TGW Zstandard payload");
        }
        if (decoded.size() + output.pos > max_output) {
            throw ProtocolError("decompressed TGW payload exceeds configured maximum");
        }
        decoded.insert(decoded.end(), chunk.begin(), chunk.begin() +
                       static_cast<std::ptrdiff_t>(output.pos));
        if (remaining == 0 && input.pos == input.size) {
            break;
        }
        if (output.pos == 0 && input.pos == input.size && remaining != 0) {
            throw ProtocolError("truncated TGW Zstandard payload");
        }
    }
    return {std::string(reinterpret_cast<const char*>(decoded.data()), decoded.size())};
}

bool json_space(unsigned char value) {
    return value == ' ' || value == '\t' || value == '\r' || value == '\n';
}

std::vector<std::string> split_json_objects(std::string_view raw) {
    while (!raw.empty() && (raw.back() == '\0' ||
           json_space(static_cast<unsigned char>(raw.back())))) {
        raw.remove_suffix(1);
    }
    if (raw.empty()) {
        throw ProtocolError("TGW server payload contains no JSON object");
    }
    std::vector<std::string> messages;
    std::size_t offset = 0;
    while (offset < raw.size()) {
        if (!messages.empty()) {
            const std::size_t separator_start = offset;
            while (offset < raw.size() &&
                   json_space(static_cast<unsigned char>(raw[offset]))) {
                ++offset;
            }
            if (offset < raw.size() && raw[offset] == '`') {
                ++offset;
                while (offset < raw.size() &&
                       json_space(static_cast<unsigned char>(raw[offset]))) {
                    ++offset;
                }
            } else if (offset == separator_start) {
                throw ProtocolError(
                    "TGW JSON objects must be separated by whitespace or ASCII 0x60");
            }
            if (offset >= raw.size()) {
                throw ProtocolError("TGW payload ends after a JSON separator");
            }
        } else {
            while (offset < raw.size() &&
                   json_space(static_cast<unsigned char>(raw[offset]))) {
                ++offset;
            }
        }
        if (offset >= raw.size() || raw[offset] != '{') {
            throw ProtocolError("TGW server JSON members must be objects");
        }
        const std::size_t begin = offset;
        std::int64_t depth = 0;
        bool in_string = false;
        bool escaped = false;
        for (; offset < raw.size(); ++offset) {
            const char character = raw[offset];
            if (in_string) {
                if (escaped) {
                    escaped = false;
                } else if (character == '\\') {
                    escaped = true;
                } else if (character == '"') {
                    in_string = false;
                }
                continue;
            }
            if (character == '"') {
                in_string = true;
            } else if (character == '{' || character == '[') {
                ++depth;
            } else if (character == '}' || character == ']') {
                --depth;
                if (depth < 0) {
                    throw ProtocolError("unbalanced TGW JSON payload");
                }
                if (depth == 0) {
                    ++offset;
                    break;
                }
            }
        }
        if (depth != 0 || in_string) {
            throw ProtocolError("truncated TGW JSON object");
        }
        std::string message(raw.substr(begin, offset - begin));
        (void)ParsedJson(message).root_object();
        messages.push_back(std::move(message));
    }
    return messages;
}

std::vector<std::string> ordered_packets(const std::vector<std::string>& packets,
                                         std::int64_t expected_tag) {
    if (packets.empty()) {
        throw ProtocolError("empty query response");
    }
    std::vector<std::pair<std::int64_t, std::string>> ordered;
    std::optional<std::int64_t> expected_count;
    for (const auto& packet : packets) {
        const auto meta = inspect_envelope(packet);
        if (!meta.status.has_value() || *meta.status != 0) {
            throw ProtocolError("query rejected by TGW server");
        }
        if (meta.tag != std::to_string(expected_tag)) {
            throw ProtocolError("unexpected query response tag");
        }
        if (!meta.pack_num.has_value() || !meta.all_pack_num.has_value()) {
            throw ProtocolError("query response is missing packet counters");
        }
        if (*meta.pack_num < 1 || *meta.all_pack_num < 1 ||
            *meta.pack_num > *meta.all_pack_num) {
            throw ProtocolError("invalid query packet counters");
        }
        if (!expected_count.has_value()) {
            expected_count = meta.all_pack_num;
        } else if (*expected_count != *meta.all_pack_num) {
            throw ProtocolError("inconsistent query packet count");
        }
        ordered.emplace_back(*meta.pack_num, packet);
    }
    std::sort(ordered.begin(), ordered.end(), [](const auto& left, const auto& right) {
        return left.first < right.first;
    });
    if (!expected_count.has_value() ||
        static_cast<std::int64_t>(ordered.size()) != *expected_count) {
        throw ProtocolError("incomplete query packet sequence");
    }
    for (std::size_t index = 0; index < ordered.size(); ++index) {
        if (ordered[index].first != static_cast<std::int64_t>(index + 1U)) {
            throw ProtocolError("duplicate or incomplete query packet sequence");
        }
    }
    std::vector<std::string> result;
    result.reserve(ordered.size());
    for (auto& [unused, packet] : ordered) {
        (void)unused;
        result.push_back(std::move(packet));
    }
    return result;
}

array data_array(ParsedJson& parsed) {
    const object root = parsed.root_object();
    return as_array(required(root, "data", "query response"), "query response data");
}

std::array<std::int64_t, 10> packed_levels(std::string_view value,
                                           std::string_view name) {
    const auto fields = split(value, '|');
    if (fields.size() != 10U) {
        throw ProtocolError("snapshot " + std::string(name) +
                            " is not a pipe-packed array of 10 integers");
    }
    std::array<std::int64_t, 10> result{};
    for (std::size_t index = 0; index < fields.size(); ++index) {
        result[index] = parse_integer(fields[index], name);
    }
    return result;
}

enum class FieldKind { Int64, UInt32, UInt8, String, Character };
struct SlotField {
    int slot;
    const char* name;
    FieldKind kind;
    std::size_t max_bytes{0};
};

const std::array<SlotField, 35> kEtfFields{{
    {1,"security_code",FieldKind::String,16},{2,"creation_redemption_unit",FieldKind::Int64},
    {3,"max_cash_ratio",FieldKind::Int64},{4,"publish",FieldKind::Character},
    {5,"creation",FieldKind::Character},{6,"redemption",FieldKind::Character},
    {7,"creation_redemption_switch",FieldKind::Character},{8,"record_num",FieldKind::Int64},
    {9,"total_record_num",FieldKind::Int64},{10,"estimate_cash_component",FieldKind::Int64},
    {11,"trading_day",FieldKind::Int64},{12,"pre_trading_day",FieldKind::Int64},
    {13,"cash_component",FieldKind::Int64},{14,"nav_per_cu",FieldKind::Int64},
    {15,"nav",FieldKind::Int64},{16,"market_type",FieldKind::UInt8},
    {17,"symbol",FieldKind::String,128},{18,"fund_management_company",FieldKind::String,128},
    {19,"underlying_security_id",FieldKind::String,16},
    {20,"underlying_security_id_source",FieldKind::String,4},
    {21,"dividend_per_cu",FieldKind::Int64},{22,"creation_limit",FieldKind::Int64},
    {23,"redemption_limit",FieldKind::Int64},{24,"creation_limit_per_user",FieldKind::Int64},
    {25,"redemption_limit_per_user",FieldKind::Int64},{26,"net_creation_limit",FieldKind::Int64},
    {27,"net_redemption_limit",FieldKind::Int64},
    {28,"net_creation_limit_per_user",FieldKind::Int64},
    {29,"net_redemption_limit_per_user",FieldKind::Int64},
    {30,"all_cash_flag",FieldKind::Character},{31,"all_cash_amount",FieldKind::String,12},
    {32,"all_cash_premium_rate",FieldKind::String,7},
    {33,"all_cash_discount_rate",FieldKind::String,7},{34,"rtgs_flag",FieldKind::Character},
    {35,"reserved",FieldKind::String,30}
}};

const std::array<SlotField, 13> kConstituentFields{{
    {1,"security_code",FieldKind::String,32},{2,"market_type",FieldKind::UInt8},
    {3,"underlying_symbol",FieldKind::String,128},{4,"component_share",FieldKind::Int64},
    {5,"substitute_flag",FieldKind::Character},{6,"premium_ratio",FieldKind::Int64},
    {7,"discount_ratio",FieldKind::Int64},{8,"creation_cash_substitute",FieldKind::Int64},
    {9,"redemption_cash_substitute",FieldKind::Int64},
    {10,"substitution_cash_amount",FieldKind::Int64},
    {11,"underlying_security_id",FieldKind::String,4},
    {12,"buy_or_sell_to_open",FieldKind::Character},{13,"reserved",FieldKind::String,30}
}};

const std::array<SlotField, 43> kSecurityFields{{
    {1,"security_code",FieldKind::String,32},{2,"market_type",FieldKind::UInt8},
    {3,"symbol",FieldKind::String,128},{4,"english_name",FieldKind::String,64},
    {5,"security_type",FieldKind::String,16},{6,"currency",FieldKind::String,8},
    {7,"variety_category",FieldKind::UInt8},{8,"pre_close_price",FieldKind::Int64},
    {9,"underlying_security_id",FieldKind::String,16},{10,"contract_type",FieldKind::String,16},
    {11,"exercise_price",FieldKind::Int64},{12,"expire_date",FieldKind::UInt32},
    {13,"high_limited",FieldKind::Int64},{14,"low_limited",FieldKind::Int64},
    {15,"security_status",FieldKind::String,16},{16,"price_tick",FieldKind::Int64},
    {17,"buy_qty_unit",FieldKind::Int64},{18,"sell_qty_unit",FieldKind::Int64},
    {19,"market_buy_qty_unit",FieldKind::Int64},{20,"market_sell_qty_unit",FieldKind::Int64},
    {21,"buy_qty_lower_limit",FieldKind::Int64},{22,"buy_qty_upper_limit",FieldKind::Int64},
    {23,"sell_qty_lower_limit",FieldKind::Int64},{24,"sell_qty_upper_limit",FieldKind::Int64},
    {25,"market_buy_qty_lower_limit",FieldKind::Int64},
    {26,"market_buy_qty_upper_limit",FieldKind::Int64},
    {27,"market_sell_qty_lower_limit",FieldKind::Int64},
    {28,"market_sell_qty_upper_limit",FieldKind::Int64},
    {29,"list_day",FieldKind::UInt32},{30,"par_value",FieldKind::Int64},
    {31,"outstanding_share",FieldKind::Int64},
    {32,"public_float_share_quantity",FieldKind::Int64},
    {33,"contract_multiplier",FieldKind::Int64},{34,"regular_share",FieldKind::String,9},
    {35,"interest",FieldKind::Int64},{36,"coupon_rate",FieldKind::Int64},
    {37,"product_code",FieldKind::String,32},{38,"delivery_year",FieldKind::UInt32},
    {39,"delivery_month",FieldKind::UInt32},{40,"create_date",FieldKind::UInt32},
    {41,"start_deliv_date",FieldKind::UInt32},{42,"end_deliv_date",FieldKind::UInt32},
    {43,"position_type",FieldKind::UInt32}
}};

template <std::size_t Size>
Record decode_slots(object source, const std::array<SlotField, Size>& fields,
                    std::size_t extra_slots = 0) {
    if (source.size() != Size + extra_slots) {
        throw ProtocolError("TGW numeric-key record slot count mismatch");
    }
    Record result;
    result.reserve(Size);
    for (const auto& field : fields) {
        const std::string key = std::to_string(field.slot);
        const element value = required(source, key, "TGW numeric-key record");
        if (field.kind == FieldKind::Int64) {
            result.emplace_back(field.name, as_int(value, field.name));
        } else if (field.kind == FieldKind::UInt32) {
            result.emplace_back(field.name, as_integer_as<std::uint32_t>(value, field.name));
        } else if (field.kind == FieldKind::UInt8) {
            result.emplace_back(field.name, as_integer_as<std::uint8_t>(value, field.name));
        } else if (field.kind == FieldKind::String) {
            auto text = as_string(value, field.name);
            if (field.max_bytes != 0 && text.size() > field.max_bytes) {
                throw ProtocolError(std::string(field.name) +
                                    " exceeds the public header byte capacity");
            }
            result.emplace_back(field.name, std::move(text));
        } else {
            const auto code = as_int(value, field.name);
            if (code < 0 || code > 255) {
                throw ProtocolError(std::string(field.name) + " ASCII code is out of range");
            }
            result.emplace_back(field.name, static_cast<std::uint8_t>(code));
        }
    }
    return result;
}

template <typename T>
T take_record_value(Record& record, std::size_t index) {
    if (index >= record.size() || !std::holds_alternative<T>(record[index].second)) {
        throw ProtocolError("internal typed-record schema mismatch");
    }
    return std::get<T>(std::move(record[index].second));
}

char take_record_char(Record& record, std::size_t index) {
    return static_cast<char>(take_record_value<std::uint8_t>(record, index));
}

EtfBasicRow typed_etf_basic(Record record) {
    EtfBasicRow row;
    row.security_code = take_record_value<std::string>(record, 0);
    row.creation_redemption_unit = take_record_value<std::int64_t>(record, 1);
    row.max_cash_ratio = take_record_value<std::int64_t>(record, 2);
    row.publish = take_record_char(record, 3);
    row.creation = take_record_char(record, 4);
    row.redemption = take_record_char(record, 5);
    row.creation_redemption_switch = take_record_char(record, 6);
    row.record_num = take_record_value<std::int64_t>(record, 7);
    row.total_record_num = take_record_value<std::int64_t>(record, 8);
    row.estimate_cash_component = take_record_value<std::int64_t>(record, 9);
    row.trading_day = take_record_value<std::int64_t>(record, 10);
    row.pre_trading_day = take_record_value<std::int64_t>(record, 11);
    row.cash_component = take_record_value<std::int64_t>(record, 12);
    row.nav_per_cu = take_record_value<std::int64_t>(record, 13);
    row.nav = take_record_value<std::int64_t>(record, 14);
    row.market_type = take_record_value<std::uint8_t>(record, 15);
    row.symbol = take_record_value<std::string>(record, 16);
    row.fund_management_company = take_record_value<std::string>(record, 17);
    row.underlying_security_id = take_record_value<std::string>(record, 18);
    row.underlying_security_id_source = take_record_value<std::string>(record, 19);
    row.dividend_per_cu = take_record_value<std::int64_t>(record, 20);
    row.creation_limit = take_record_value<std::int64_t>(record, 21);
    row.redemption_limit = take_record_value<std::int64_t>(record, 22);
    row.creation_limit_per_user = take_record_value<std::int64_t>(record, 23);
    row.redemption_limit_per_user = take_record_value<std::int64_t>(record, 24);
    row.net_creation_limit = take_record_value<std::int64_t>(record, 25);
    row.net_redemption_limit = take_record_value<std::int64_t>(record, 26);
    row.net_creation_limit_per_user = take_record_value<std::int64_t>(record, 27);
    row.net_redemption_limit_per_user = take_record_value<std::int64_t>(record, 28);
    row.all_cash_flag = take_record_char(record, 29);
    row.all_cash_amount = take_record_value<std::string>(record, 30);
    row.all_cash_premium_rate = take_record_value<std::string>(record, 31);
    row.all_cash_discount_rate = take_record_value<std::string>(record, 32);
    row.rtgs_flag = take_record_char(record, 33);
    row.reserved = take_record_value<std::string>(record, 34);
    return row;
}

EtfConstituentRow typed_etf_constituent(Record record) {
    EtfConstituentRow row;
    row.security_code = take_record_value<std::string>(record, 0);
    row.market_type = take_record_value<std::uint8_t>(record, 1);
    row.underlying_symbol = take_record_value<std::string>(record, 2);
    row.component_share = take_record_value<std::int64_t>(record, 3);
    row.substitute_flag = take_record_char(record, 4);
    row.premium_ratio = take_record_value<std::int64_t>(record, 5);
    row.discount_ratio = take_record_value<std::int64_t>(record, 6);
    row.creation_cash_substitute = take_record_value<std::int64_t>(record, 7);
    row.redemption_cash_substitute = take_record_value<std::int64_t>(record, 8);
    row.substitution_cash_amount = take_record_value<std::int64_t>(record, 9);
    row.underlying_security_id = take_record_value<std::string>(record, 10);
    row.buy_or_sell_to_open = take_record_char(record, 11);
    row.reserved = take_record_value<std::string>(record, 12);
    return row;
}

SecuritiesInfoRow typed_securities_info(Record record) {
    SecuritiesInfoRow row;
    row.security_code = take_record_value<std::string>(record, 0);
    row.market_type = take_record_value<std::uint8_t>(record, 1);
    row.symbol = take_record_value<std::string>(record, 2);
    row.english_name = take_record_value<std::string>(record, 3);
    row.security_type = take_record_value<std::string>(record, 4);
    row.currency = take_record_value<std::string>(record, 5);
    row.variety_category = take_record_value<std::uint8_t>(record, 6);
    row.pre_close_price = take_record_value<std::int64_t>(record, 7);
    row.underlying_security_id = take_record_value<std::string>(record, 8);
    row.contract_type = take_record_value<std::string>(record, 9);
    row.exercise_price = take_record_value<std::int64_t>(record, 10);
    row.expire_date = take_record_value<std::uint32_t>(record, 11);
    row.high_limited = take_record_value<std::int64_t>(record, 12);
    row.low_limited = take_record_value<std::int64_t>(record, 13);
    row.security_status = take_record_value<std::string>(record, 14);
    row.price_tick = take_record_value<std::int64_t>(record, 15);
    row.buy_qty_unit = take_record_value<std::int64_t>(record, 16);
    row.sell_qty_unit = take_record_value<std::int64_t>(record, 17);
    row.market_buy_qty_unit = take_record_value<std::int64_t>(record, 18);
    row.market_sell_qty_unit = take_record_value<std::int64_t>(record, 19);
    row.buy_qty_lower_limit = take_record_value<std::int64_t>(record, 20);
    row.buy_qty_upper_limit = take_record_value<std::int64_t>(record, 21);
    row.sell_qty_lower_limit = take_record_value<std::int64_t>(record, 22);
    row.sell_qty_upper_limit = take_record_value<std::int64_t>(record, 23);
    row.market_buy_qty_lower_limit = take_record_value<std::int64_t>(record, 24);
    row.market_buy_qty_upper_limit = take_record_value<std::int64_t>(record, 25);
    row.market_sell_qty_lower_limit = take_record_value<std::int64_t>(record, 26);
    row.market_sell_qty_upper_limit = take_record_value<std::int64_t>(record, 27);
    row.list_day = take_record_value<std::uint32_t>(record, 28);
    row.par_value = take_record_value<std::int64_t>(record, 29);
    row.outstanding_share = take_record_value<std::int64_t>(record, 30);
    row.public_float_share_quantity = take_record_value<std::int64_t>(record, 31);
    row.contract_multiplier = take_record_value<std::int64_t>(record, 32);
    row.regular_share = take_record_value<std::string>(record, 33);
    row.interest = take_record_value<std::int64_t>(record, 34);
    row.coupon_rate = take_record_value<std::int64_t>(record, 35);
    row.product_code = take_record_value<std::string>(record, 36);
    row.delivery_year = take_record_value<std::uint32_t>(record, 37);
    row.delivery_month = take_record_value<std::uint32_t>(record, 38);
    row.create_date = take_record_value<std::uint32_t>(record, 39);
    row.start_deliv_date = take_record_value<std::uint32_t>(record, 40);
    row.end_deliv_date = take_record_value<std::uint32_t>(record, 41);
    row.position_type = take_record_value<std::uint32_t>(record, 42);
    return row;
}

} // namespace

std::string escape_json(std::string_view value) {
    std::ostringstream output;
    for (const char raw_character : value) {
        const auto character = static_cast<unsigned char>(raw_character);
        switch (character) {
        case '"': output << "\\\""; break;
        case '\\': output << "\\\\"; break;
        case '\b': output << "\\b"; break;
        case '\f': output << "\\f"; break;
        case '\n': output << "\\n"; break;
        case '\r': output << "\\r"; break;
        case '\t': output << "\\t"; break;
        default:
            if (character < 0x20U) {
                output << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                       << static_cast<unsigned int>(character) << std::dec;
            } else {
                output << static_cast<char>(character);
            }
        }
    }
    return output.str();
}

std::vector<std::string> default_mac_addresses() {
    const char* configured = std::getenv("TGW_MAC_ADDRESS");
    if (configured != nullptr && *configured != '\0') {
        std::vector<std::string> values;
        for (auto& value : split(configured, ',')) {
            value.erase(std::remove_if(value.begin(), value.end(), [](unsigned char c) {
                return std::isspace(c) != 0;
            }), value.end());
            if (!value.empty()) {
                std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
                    return static_cast<char>(std::tolower(c));
                });
                values.push_back(std::move(value));
            }
        }
        if (!values.empty()) {
            return values;
        }
    }
#if defined(__APPLE__)
    ifaddrs* raw = nullptr;
    if (::getifaddrs(&raw) == 0) {
        std::unique_ptr<ifaddrs, decltype(&freeifaddrs)> interfaces(raw, freeifaddrs);
        for (auto* current = interfaces.get(); current != nullptr; current = current->ifa_next) {
            if (current->ifa_addr == nullptr || current->ifa_addr->sa_family != AF_LINK ||
                (current->ifa_flags & IFF_LOOPBACK) != 0 ||
                (current->ifa_flags & IFF_UP) == 0) {
                continue;
            }
            const auto* address = reinterpret_cast<const sockaddr_dl*>(current->ifa_addr);
            if (address->sdl_alen != 6) {
                continue;
            }
            const auto* bytes = reinterpret_cast<const unsigned char*>(LLADDR(address));
            std::ostringstream formatted;
            formatted << std::hex << std::setfill('0');
            for (int index = 0; index < 6; ++index) {
                if (index != 0) formatted << ':';
                formatted << std::setw(2) << static_cast<unsigned int>(bytes[index]);
            }
            return {formatted.str()};
        }
    }
#endif
    throw TransportError("cannot determine a non-loopback MAC address; set TGW_MAC_ADDRESS");
}

std::string build_logon_request(
    std::string_view username,
    std::string_view password,
    bool force_logout,
    std::string_view client_version,
    std::int64_t process_id,
    std::span<const std::string> mac_addresses
) {
    std::vector<std::string> addresses(mac_addresses.begin(), mac_addresses.end());
    if (addresses.empty()) {
        addresses = default_mac_addresses();
    }
    std::ostringstream output;
    output << "{\"headers\":{\"id\":0,\"userName\":" << json_quote(username)
           << "},\"method\":\"ReqLogon\",\"params\":{\"Username\":"
           << json_quote(username) << ",\"Password\":" << json_quote(password)
           << ",\"MacAddress\":" << json_quote(join(addresses, ","))
           << ",\"Version\":" << json_quote(client_version)
           << ",\"ProcessId\":" << process_id
           << ",\"ForceLogout\":" << (force_logout ? "true" : "false")
           << ",\"PushBandWidth\":0.0,\"QueryBandWidth\":0.0}}";
    return output.str();
}

std::string build_subscribe_request(
    std::string_view username,
    std::string_view token,
    std::int64_t request_id,
    std::span<const SubscribeItem> items,
    bool unsubscribe
) {
    if (items.empty()) {
        throw std::invalid_argument("subscription list is empty");
    }
    std::vector<std::int64_t> wire_types;
    wire_types.reserve(items.size());
    for (const auto& item : items) {
        if (item.flag == 10U) wire_types.push_back(14);
        else if (item.flag == 12U) wire_types.push_back(16);
        else throw std::invalid_argument("subscription flag has not been wire-verified");
    }
    std::ostringstream output;
    output << "{\"headers\":" << headers_user_token(username, token, request_id)
           << ",\"method\":" << json_quote(unsubscribe ? "ReqUnSubscribeBatch" : "ReqSubscribeBatch")
           << ",\"params\":{\"marketType\":[";
    for (std::size_t index = 0; index < items.size(); ++index) {
        if (index) output << ',';
        output << items[index].market;
    }
    output << "],\"categoryType\":[";
    for (std::size_t index = 0; index < items.size(); ++index) {
        if (index) output << ',';
        output << static_cast<unsigned int>(items[index].category_type);
    }
    output << "],\"subscribeDataType\":[";
    for (std::size_t index = 0; index < items.size(); ++index) {
        if (index) output << ',';
        output << wire_types[index];
    }
    output << "],\"securityCode\":[";
    for (std::size_t index = 0; index < items.size(); ++index) {
        if (index) output << ',';
        output << json_quote(items[index].security_code);
    }
    output << "]}}";
    return output.str();
}

std::string build_third_info_request(
    std::string_view username, std::string_view token, std::int64_t request_id,
    const std::vector<std::pair<std::string, std::string>>& parameters,
    std::int64_t offset, std::int64_t count
) {
    const auto function = std::find_if(parameters.begin(), parameters.end(),
        [](const auto& item) { return item.first == "function_id" && !item.second.empty(); });
    if (function == parameters.end()) {
        throw std::invalid_argument("third-info request is missing function_id");
    }
    if (offset < 0 || count <= 0) {
        throw std::invalid_argument("third-info offset/count must be non-negative/positive");
    }
    std::ostringstream output;
    output << "{\"headers\":" << headers_user_token(username, token, request_id)
           << ",\"method\":\"ReqGetThirdInfo\",\"params\":{\"QueryBandWidth\":0.0}"
           << ",\"function_id\":" << json_quote(function->second)
           << ",\"offset\":" << offset << ",\"count\":" << count << ",\"item\":[";
    bool first = true;
    for (const auto& [key, value] : parameters) {
        if (key == "function_id") continue;
        if (!first) output << ',';
        first = false;
        output << "{\"key\":" << json_quote(key) << ",\"value\":" << json_quote(value) << '}';
    }
    output << "]}";
    return output.str();
}

std::string build_query_complete_request(std::string_view username,
                                         std::string_view token,
                                         std::int64_t request_id) {
    return "{\"headers\":" + headers_user_token(username, token, request_id) +
           ",\"method\":\"ReqGetComplete\"}";
}

std::int64_t kline_wire_period(std::int64_t cyc_type) {
    switch (cyc_type) {
    case 10000: return 10000;
    case 10008: return 10100;
    case 10009: return 10101;
    case 10010: return 10102;
    case 10011: return 10103;
    case 10012: return 10104;
    default: throw std::invalid_argument("K-line cycle has not been wire-verified");
    }
}

std::string build_kline_request(std::string_view username, std::string_view token,
                                std::int64_t request_id,
                                const KlineRequest& request) {
    if (request.security_code.empty()) {
        throw std::invalid_argument("kline request is missing security_code");
    }
    std::ostringstream output;
    output << "{\"headers\":" << headers_user_token(username, token, request_id)
           << ",\"method\":\"ReqGetKline\",\"params\":{\"security_code\":"
           << json_quote(request.security_code) << ",\"market_type\":" << request.market_type
           << ",\"cq_flag\":" << request.cq_flag
           << ",\"auto_complete\":" << request.auto_complete
           << ",\"period_type\":" << kline_wire_period(request.cyc_type)
           << ",\"begin_date\":" << request.begin_date
           << ",\"end_date\":" << request.end_date
           << ",\"begin_time\":" << request.begin_time
           << ",\"end_time\":" << request.end_time
           << ",\"QueryBandWidth\":0.0}}";
    return output.str();
}

std::string build_snapshot_request(std::string_view username, std::string_view token,
                                   std::int64_t request_id,
                                   const SnapshotRequest& request) {
    if (request.security_code.empty()) {
        throw std::invalid_argument("snapshot request is missing security_code");
    }
    if (request.data_type != 0 || request.level_type != 0) {
        throw std::invalid_argument("only L1 snapshot data_type=0/level_type=0 is verified");
    }
    if (!((request.market_type == 102 && request.security_code == "159518") ||
          (request.market_type == 101 && request.security_code == "510300"))) {
        throw std::invalid_argument("snapshot target has not been independently verified");
    }
    std::ostringstream output;
    output << "{\"headers\":" << headers_user_token(username, token, request_id)
           << ",\"method\":\"ReqGetSnapshot\",\"params\":{\"security_code\":"
           << json_quote(request.security_code) << ",\"market_type\":" << request.market_type
           << ",\"date\":" << request.date << ",\"begin_time\":" << request.begin_time
           << ",\"end_time\":" << request.end_time
           << ",\"data_type\":" << request.data_type
           << ",\"QueryBandWidth\":0.0}}";
    return output.str();
}

std::string build_code_table_request(std::string_view username, std::string_view token,
                                     std::int64_t request_id) {
    return "{\"headers\":" + headers_user_token(username, token, request_id, true) +
           ",\"method\":\"ReqGetReduceCodeTable\",\"params\":{\"QueryBandWidth\":0.0}}";
}

std::string build_get_package_request(std::string_view username, std::string_view token,
                                      std::int64_t request_id, std::int64_t pack_num) {
    return "{\"headers\":" + headers_user_token(username, token, request_id, true) +
           ",\"method\":\"ReqGetPackage\",\"params\":{\"pack_num\":" +
           json_quote(std::to_string(pack_num) + ",") + "}}";
}

std::string build_etf_info_request(std::string_view username, std::string_view token,
                                   std::int64_t request_id,
                                   std::span<const SecurityItem> items) {
    if (items.size() != 1U) {
        throw std::invalid_argument("only a single ETF info item is wire-verified");
    }
    const auto& item = items.front();
    if (item.market != 101 && item.market != 102) {
        throw std::invalid_argument("ETF info market is not wire-verified");
    }
    if (item.security_code.empty() || item.security_code.size() > 32U) {
        throw std::invalid_argument("ETF info security code must contain 1..32 bytes");
    }
    return "{\"headers\":" + headers_user_token(username, token, request_id, true) +
           ",\"method\":\"ReqGetETFCodeTableList\",\"params\":{\"Security\":" +
           json_quote(item.security_code + "|" + std::to_string(item.market)) + "}}";
}

std::string build_codelist_complete_request(std::string_view username,
                                            std::string_view token,
                                            std::int64_t request_id) {
    return "{\"headers\":" + headers_user_token(username, token, request_id, true) +
           ",\"method\":\"ReqGetCodelistComplete\"}";
}

std::string build_securities_info_request(std::string_view username,
                                          std::string_view token,
                                          std::int64_t request_id,
                                          std::span<const SecurityItem> items) {
    if (items.size() != 1U && items.size() != 2U) {
        throw std::invalid_argument("only verified securities-info item counts are supported");
    }
    const auto verified = [](const SecurityItem& item) {
        return (item.market == 101 && item.security_code == "510300") ||
               (item.market == 102 && item.security_code == "159919");
    };
    if ((items.size() == 1U && !verified(items[0])) ||
        (items.size() == 2U && !(items[0].market == 101 &&
          items[0].security_code == "510300" && items[1].market == 102 &&
          items[1].security_code == "159919"))) {
        throw std::invalid_argument("securities-info request is outside verified scope");
    }
    std::ostringstream security;
    for (std::size_t index = 0; index < items.size(); ++index) {
        if (index) security << ',';
        security << items[index].security_code << '|' << items[index].market;
    }
    return "{\"headers\":" + headers_user_token(username, token, request_id, true) +
           ",\"method\":\"ReqGetCodeTableList\",\"params\":{\"Security\":" +
           json_quote(security.str()) + "}}";
}

std::string build_ex_factor_request(std::string_view username, std::string_view token,
                                    std::int64_t request_id,
                                    std::string_view security_code) {
    if (security_code.empty() || security_code.size() > 32U) {
        throw std::invalid_argument("ex-factor security code must contain 1..32 bytes");
    }
    return "{\"headers\":" + headers_user_token(username, token, request_id, true) +
           ",\"method\":\"ReqGetExFactor\",\"params\":{\"security_code\":" +
           json_quote(security_code) + ",\"QueryBandWidth\":0.0}}";
}

EnvelopeMeta inspect_envelope(std::string_view json) {
    ParsedJson parsed(json);
    const object root = parsed.root_object();
    EnvelopeMeta meta;
    if (const auto status = optional(root, "status")) {
        meta.status = as_int(*status, "status");
    }
    if (const auto headers_element = optional(root, "headers")) {
        const object headers = as_object(*headers_element, "headers");
        if (const auto id = optional(headers, "id")) meta.request_id = as_int(*id, "headers.id");
        if (const auto tag = optional(headers, "tag")) {
            std::string_view text;
            std::int64_t number = 0;
            if (!tag->get_string().get(text)) meta.tag = std::string(text);
            else if (!tag->get_int64().get(number)) meta.tag = std::to_string(number);
            else throw ProtocolError("headers.tag is neither a string nor an integer");
        }
        if (const auto token = optional(headers, "token")) meta.token = as_string(*token, "headers.token");
        if (const auto pack = optional(headers, "pack_num")) meta.pack_num = as_int(*pack, "pack_num");
        if (const auto all = optional(headers, "all_pack_num")) meta.all_pack_num = as_int(*all, "all_pack_num");
    }
    return meta;
}

std::vector<std::string> decode_server_payload(std::span<const std::uint8_t> payload,
                                               std::size_t max_output) {
    static constexpr std::array<std::uint8_t, 4> magic{0x28, 0xB5, 0x2F, 0xFD};
    std::span<const std::uint8_t> raw = payload;
    bool compressed = raw.size() >= magic.size() &&
        std::equal(magic.begin(), magic.end(), raw.begin());
    if (!compressed && raw.size() > 5U &&
        std::equal(magic.begin(), magic.end(), raw.begin() + 1)) {
        raw = raw.subspan(1);
        compressed = true;
    }
    std::string decoded;
    if (compressed) {
        decoded = decompress_zstd(raw, max_output).front();
    } else {
        decoded.assign(reinterpret_cast<const char*>(raw.data()), raw.size());
    }
    return split_json_objects(decoded);
}

std::vector<KlineRow> parse_kline_packets(const std::vector<std::string>& packets,
                                          std::int64_t expected_tag) {
    std::vector<KlineRow> rows;
    for (const auto& packet : ordered_packets(packets, expected_tag)) {
        ParsedJson parsed(packet);
        for (const element value : data_array(parsed)) {
            const auto fields = split(as_string(value, "kline row"), ',');
            if (fields.size() != 9U) throw ProtocolError("kline row does not contain 9 fields");
            KlineRow row;
            row.security_code = fields[0];
            validate_max_bytes(row.security_code, 32U, "kline security_code");
            row.market_type = parse_integer_as<std::uint8_t>(
                fields[1], "kline market_type");
            row.kline_time = parse_integer(fields[2], "kline time");
            row.open_price = parse_integer(fields[3], "kline open_price");
            row.high_price = parse_integer(fields[4], "kline high_price");
            row.low_price = parse_integer(fields[5], "kline low_price");
            row.close_price = parse_integer(fields[6], "kline close_price");
            row.volume_trade = parse_integer(fields[7], "kline volume_trade");
            row.value_trade = parse_integer(fields[8], "kline value_trade");
            rows.push_back(std::move(row));
        }
    }
    return rows;
}

QueryResult<SnapshotRow> parse_snapshot_packets(const std::vector<std::string>& packets) {
    if (packets.empty()) throw ProtocolError("empty query response");
    bool has_ok = false;
    bool has_error = false;
    int mapped_error = 0;
    for (const auto& packet : packets) {
        const auto meta = inspect_envelope(packet);
        if (meta.status.has_value() && *meta.status == 0) has_ok = true;
        else {
            has_error = true;
            if (meta.tag != "DataEmpty") throw ProtocolError("unobserved snapshot error tag");
            if (mapped_error != 0 && mapped_error != -76) {
                throw ProtocolError("multiple distinct snapshot error frames");
            }
            mapped_error = -76;
        }
    }
    if (has_ok && has_error) throw ProtocolError("snapshot response mixes data and errors");
    if (has_error) return {{}, mapped_error};
    QueryResult<SnapshotRow> result;
    for (const auto& packet : ordered_packets(packets, 11000)) {
        ParsedJson parsed(packet);
        for (const element value : data_array(parsed)) {
            const auto fields = split(as_string(value, "snapshot row"), ',');
            if (fields.size() != 36U) {
                throw ProtocolError("snapshot row does not contain 36 fields");
            }
            SnapshotRow row;
            row.security_code = fields[0];
            validate_max_bytes(row.security_code, 16U, "snapshot security_code");
            row.market_type = parse_integer_as<std::uint8_t>(
                fields[1], "snapshot market_type");
            row.orig_time = parse_integer(fields[2], "snapshot orig_time");
            row.trading_phase_code = fields[3];
            validate_max_bytes(row.trading_phase_code, 8U,
                               "snapshot trading_phase_code");
            row.pre_close_price = parse_integer(fields[4], "snapshot pre_close_price");
            row.open_price = parse_integer(fields[5], "snapshot open_price");
            row.high_price = parse_integer(fields[6], "snapshot high_price");
            row.low_price = parse_integer(fields[7], "snapshot low_price");
            row.last_price = parse_integer(fields[8], "snapshot last_price");
            row.close_price = parse_integer(fields[9], "snapshot close_price");
            row.bid_price = packed_levels(fields[10], "bid_price");
            row.bid_volume = packed_levels(fields[11], "bid_volume");
            row.offer_price = packed_levels(fields[12], "offer_price");
            row.offer_volume = packed_levels(fields[13], "offer_volume");
            row.num_trades = parse_integer(fields[14], "snapshot num_trades");
            row.total_volume_trade = parse_integer(fields[15], "snapshot volume");
            row.total_value_trade = parse_integer(fields[16], "snapshot value");
            row.iopv = parse_integer(fields[17], "snapshot IOPV");
            row.high_limited = parse_integer(fields[18], "snapshot high_limited");
            row.low_limited = parse_integer(fields[19], "snapshot low_limited");
            std::copy_n(fields.begin() + 20, row.unverified_tail_raw.size(),
                        row.unverified_tail_raw.begin());
            result.rows.push_back(std::move(row));
        }
    }
    return result;
}

std::vector<CodeTableRow> parse_code_table_packets(const std::vector<std::string>& packets) {
    std::vector<CodeTableRow> rows;
    for (const auto& packet : ordered_packets(packets, 11103)) {
        ParsedJson parsed(packet);
        for (const element value : data_array(parsed)) {
            const auto fields = split(as_string(value, "code-table row"), '`');
            if (fields.size() != 6U) throw ProtocolError("code-table row does not contain 6 fields");
            validate_max_bytes(fields[0], 32U, "code-table security_code");
            validate_max_bytes(fields[1], 128U, "code-table symbol");
            validate_max_bytes(fields[2], 64U, "code-table english_name");
            validate_max_bytes(fields[4], 16U, "code-table security_type");
            validate_max_bytes(fields[5], 8U, "code-table currency");
            rows.push_back({fields[0], fields[1], fields[2],
                            parse_integer_as<std::uint8_t>(
                                fields[3], "code-table market_type"),
                            fields[4], fields[5]});
        }
    }
    return rows;
}

std::vector<EtfRecord> parse_etf_info_packets(
    const std::vector<std::string>& packets,
    std::optional<std::int64_t> expected_request_id
) {
    if (packets.empty()) throw ProtocolError("empty query response");
    std::vector<EtfRecord> results;
    for (const auto& packet : packets) {
        const auto meta = inspect_envelope(packet);
        if (!meta.status.has_value() || *meta.status != 0 || meta.tag != "111") {
            throw ProtocolError("invalid ETF-info response status/tag");
        }
        if (meta.pack_num.has_value() || meta.all_pack_num.has_value()) {
            throw ProtocolError("unexpected ETF-info packet counters");
        }
        if (expected_request_id.has_value() && meta.request_id != expected_request_id) {
            throw ProtocolError("ETF-info response request id mismatch");
        }
        ParsedJson parsed(packet);
        for (const element value : data_array(parsed)) {
            const object record = as_object(value, "ETF record");
            EtfRecord decoded;
            decoded.basic = typed_etf_basic(decode_slots(record, kEtfFields, 1U));
            const array constituents = as_array(required(record, "36", "ETF record"),
                                                "ETF constituents");
            for (const element constituent : constituents) {
                decoded.constituents.push_back(typed_etf_constituent(decode_slots(
                    as_object(constituent, "ETF constituent"), kConstituentFields)));
            }
            results.push_back(std::move(decoded));
        }
    }
    return results;
}

std::vector<SecuritiesInfoRow> parse_securities_info_packets(
    const std::vector<std::string>& packets,
    std::optional<std::int64_t> expected_request_id
) {
    if (packets.empty()) throw ProtocolError("empty query response");
    std::vector<SecuritiesInfoRow> results;
    for (const auto& packet : packets) {
        const auto meta = inspect_envelope(packet);
        if (!meta.status.has_value() || *meta.status != 0 || meta.tag != "109") {
            throw ProtocolError("invalid securities-info response status/tag");
        }
        if (meta.pack_num.has_value() || meta.all_pack_num.has_value()) {
            throw ProtocolError("unexpected securities-info packet counters");
        }
        if (expected_request_id.has_value() && meta.request_id != expected_request_id) {
            throw ProtocolError("securities-info response request id mismatch");
        }
        ParsedJson parsed(packet);
        const object root = parsed.root_object();
        const array data = as_array(required(root, "data", "securities-info response"),
                                    "securities-info data");
        const object headers = as_object(required(root, "headers", "securities-info response"),
                                         "headers");
        const auto code_num = as_int(required(headers, "code_num", "headers"), "code_num");
        if (code_num != static_cast<std::int64_t>(data.size())) {
            throw ProtocolError("securities-info code_num does not match data length");
        }
        for (const element value : data) {
            results.push_back(typed_securities_info(decode_slots(
                as_object(value, "securities-info record"), kSecurityFields)));
        }
    }
    return results;
}

std::vector<ExFactorRow> parse_ex_factor_packets(const std::vector<std::string>& packets) {
    std::vector<ExFactorRow> rows;
    for (const auto& packet : ordered_packets(packets, 11102)) {
        ParsedJson parsed(packet);
        for (const element value : data_array(parsed)) {
            const auto fields = split(as_string(value, "ex-factor row"), ',');
            if (fields.size() != 5U) throw ProtocolError("ex-factor row does not contain 5 fields");
            std::size_t consumed_factor = 0;
            std::size_t consumed_cumulative = 0;
            double factor = 0.0;
            double cumulative = 0.0;
            try {
                factor = std::stod(fields[3], &consumed_factor);
                cumulative = std::stod(fields[4], &consumed_cumulative);
            } catch (const std::exception&) {
                throw ProtocolError("ex-factor response contains a non-number");
            }
            if (consumed_factor != fields[3].size() || consumed_cumulative != fields[4].size() ||
                !std::isfinite(factor) || !std::isfinite(cumulative)) {
                throw ProtocolError("ex-factor response contains an invalid number");
            }
            validate_max_bytes(fields[0], 16U, "ex-factor inner_code");
            validate_max_bytes(fields[1], 16U, "ex-factor security_code");
            rows.push_back({fields[0], fields[1],
                            parse_integer_as<std::uint32_t>(
                                fields[2], "ex-factor ex_date"),
                            factor, cumulative, fields[3], fields[4]});
        }
    }
    return rows;
}

std::vector<std::string> parse_third_info_packets(const std::vector<std::string>& packets) {
    std::vector<std::string> rows;
    for (const auto& packet : ordered_packets(packets, 11101)) {
        ParsedJson parsed(packet);
        const object root = parsed.root_object();
        const std::string nested = as_string(required(root, "data", "third-info response"),
                                             "third-info data");
        ParsedJson nested_parsed(nested);
        const object nested_root = nested_parsed.root_object();
        const object body = as_object(required(nested_root, "body", "third-info JSON"),
                                      "third-info body");
        const array data = as_array(required(body, "data", "third-info body"),
                                    "third-info body.data");
        for (const element value : data) {
            object checked;
            if (value.get_object().get(checked)) {
                throw ProtocolError("third-info body.data member is not an object");
            }
            rows.push_back(simdjson::minify(value));
        }
    }
    return rows;
}

std::string scalar_to_json(const Scalar& value) {
    return std::visit([](const auto& item) -> std::string {
        using T = std::decay_t<decltype(item)>;
        if constexpr (std::is_same_v<T, std::string>) {
            return json_quote(item);
        } else if constexpr (std::is_same_v<T, double>) {
            std::ostringstream output;
            output << std::setprecision(17) << item;
            return output.str();
        } else {
            return std::to_string(item);
        }
    }, value);
}

std::string record_to_json(const Record& record) {
    std::ostringstream output;
    output << '{';
    for (std::size_t index = 0; index < record.size(); ++index) {
        if (index) output << ',';
        output << json_quote(record[index].first) << ':' << scalar_to_json(record[index].second);
    }
    output << '}';
    return output.str();
}

} // namespace tgw::protocol
