#include "tgw/client.hpp"

#include <zstd.h>

#include <cstdint>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

namespace {

void check(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error("test failure: " + message);
}

template <typename Exception, typename Callable>
void check_throws(Callable&& callable, const std::string& message) {
    try {
        callable();
    } catch (const Exception&) {
        return;
    }
    throw std::runtime_error("test failure: expected exception: " + message);
}

void test_logon_contract() {
    const std::vector<std::string> macs{"00:11:22:33:44:55"};
    const auto request = tgw::protocol::build_logon_request(
        "user", "pass", false, tgw::kClientVersion, 123, macs);
    check(request ==
        "{\"headers\":{\"id\":0,\"userName\":\"user\"},"
        "\"method\":\"ReqLogon\",\"params\":{\"Username\":\"user\","
        "\"Password\":\"pass\",\"MacAddress\":\"00:11:22:33:44:55\","
        "\"Version\":\"V4.3.0.260626-rc2.0-YHZQ\",\"ProcessId\":123,"
        "\"ForceLogout\":false,\"PushBandWidth\":0.0,\"QueryBandWidth\":0.0}}",
        "logon envelope/key order");
}

void test_all_request_builders() {
    const std::vector<tgw::SubscribeItem> subscriptions{{101, 10, "510300", 0}};
    const auto subscribe = tgw::protocol::build_subscribe_request(
        "user", "token", 1000000, subscriptions);
    check(subscribe.find("\"subscribeDataType\":[14]") != std::string::npos,
          "public subscription enum maps to wire 14");
    check(subscribe.find("\"method\":\"ReqSubscribeBatch\"") != std::string::npos,
          "subscribe method");

    const std::vector<std::pair<std::string, std::string>> third{
        {"function_id", "A010061003"}, {"market", "SSE"}
    };
    check(tgw::protocol::build_third_info_request(
        "u", "t", 7, third, 5, 20) ==
        "{\"headers\":{\"userName\":\"u\",\"token\":\"t\",\"id\":7},"
        "\"method\":\"ReqGetThirdInfo\",\"params\":{\"QueryBandWidth\":0.0},"
        "\"function_id\":\"A010061003\",\"offset\":5,\"count\":20,"
        "\"item\":[{\"key\":\"market\",\"value\":\"SSE\"}]}",
        "third-info envelope");

    const tgw::KlineRequest kline{
        "510300", 101, 0, 1, 10008, 20260825, 20260825, 0, 0
    };
    const auto kline_json = tgw::protocol::build_kline_request("u", "t", 8, kline);
    check(kline_json.find("\"period_type\":10100") != std::string::npos,
          "daily K-line wire period");
    check(kline_json.find("\"method\":\"ReqGetKline\"") != std::string::npos,
          "K-line method");

    const tgw::SnapshotRequest snapshot{
        "510300", 101, 20260825, 93000000, 93030000, 0, 0
    };
    const auto snapshot_json = tgw::protocol::build_snapshot_request(
        "u", "t", 9, snapshot);
    check(snapshot_json.find("\"method\":\"ReqGetSnapshot\"") != std::string::npos,
          "snapshot method");
    check(snapshot_json.find("level_type") == std::string::npos,
          "untransmitted level_type stays off wire");

    check(tgw::protocol::build_code_table_request("u", "t", 10) ==
        "{\"headers\":{\"id\":10,\"userName\":\"u\",\"token\":\"t\"},"
        "\"method\":\"ReqGetReduceCodeTable\",\"params\":{\"QueryBandWidth\":0.0}}",
        "code table envelope");
    check(tgw::protocol::build_get_package_request("u", "t", 10, 3).find(
        "\"pack_num\":\"3,\"") != std::string::npos, "missing packet retry shape");

    const std::vector<tgw::SecurityItem> etf{{101, "510300"}};
    check(tgw::protocol::build_etf_info_request("u", "t", 1, etf).find(
        "\"Security\":\"510300|101\"") != std::string::npos,
        "ETF codelist Security value");
    const std::vector<tgw::SecurityItem> pair{{101, "510300"}, {102, "159919"}};
    check(tgw::protocol::build_securities_info_request("u", "t", 2, pair).find(
        "\"Security\":\"510300|101,159919|102\"") != std::string::npos,
        "securities-info verified pair wire order");
    check(tgw::protocol::build_ex_factor_request("u", "t", 3, "000001").find(
        "\"method\":\"ReqGetExFactor\"") != std::string::npos,
        "ex-factor method");
}

void test_zstd_multi_object_decode() {
    const std::string source = "{\"headers\":{\"id\":1}}`{\"status\":0}\0";
    std::vector<std::uint8_t> compressed(ZSTD_compressBound(source.size()) + 1U);
    compressed[0] = 0x59;
    const auto written = ZSTD_compress(
        compressed.data() + 1U, compressed.size() - 1U,
        source.data(), source.size(), 1);
    check(!ZSTD_isError(written), "test fixture compression");
    compressed.resize(written + 1U);
    const auto decoded = tgw::protocol::decode_server_payload(compressed);
    check(decoded.size() == 2U, "0x59+ZSTD object stream split");
    check(tgw::protocol::inspect_envelope(decoded[0]).request_id == 1,
          "first decoded message id");
    check(tgw::protocol::inspect_envelope(decoded[1]).status == 0,
          "second decoded message status");
    const std::string bad = "{}{}";
    check_throws<tgw::ProtocolError>([&] {
        (void)tgw::protocol::decode_server_payload(std::span<const std::uint8_t>(
            reinterpret_cast<const std::uint8_t*>(bad.data()), bad.size()));
    }, "unseparated object stream");
}

void test_kline_and_packet_integrity() {
    const std::vector<std::string> packets{
        "{\"headers\":{\"id\":1,\"tag\":10100,\"pack_num\":2,"
        "\"all_pack_num\":2},\"status\":0,"
        "\"data\":[\"510300,101,20260825,4000000,4100000,3900000,4050000,100,200\"]}",
        "{\"headers\":{\"id\":1,\"tag\":10100,\"pack_num\":1,"
        "\"all_pack_num\":2},\"status\":0,"
        "\"data\":[\"510300,101,20260824,3900000,4000000,3800000,3950000,90,180\"]}"
    };
    const auto rows = tgw::protocol::parse_kline_packets(packets, 10100);
    check(rows.size() == 2U, "two K-line rows");
    check(rows[0].kline_time == 20260824 && rows[1].kline_time == 20260825,
          "packet sequence reordered before row flattening");
    check(!rows[0].orig_time.has_value() && !rows[0].variety_category.has_value(),
          "wire-absent K-line fields stay absent instead of synthetic zero");
    check_throws<tgw::ProtocolError>([] {
        (void)tgw::protocol::parse_kline_packets({
            "{\"headers\":{\"id\":1,\"tag\":10100,\"pack_num\":1,"
            "\"all_pack_num\":1},\"status\":0,"
            "\"data\":[\"510300,256,20260825,1,2,3,4,5,6\"]}"
        }, 10100);
    }, "K-line uint8 market overflow rejected");
    check_throws<tgw::ProtocolError>([&] {
        (void)tgw::protocol::parse_kline_packets({packets[0], packets[0]}, 10100);
    }, "duplicate packet rejected");
}

void test_snapshot_success_and_error() {
    const std::string levels = "1|2|3|4|5|6|7|8|9|10";
    std::string row = "510300,101,20260825093000000,T,1,2,3,1,2,2," + levels +
        "," + levels + "," + levels + "," + levels + ",4,5,6,7,8,9";
    for (int index = 20; index < 36; ++index) row += ",0";
    const std::vector<std::string> success{
        "{\"headers\":{\"id\":1,\"tag\":11000,\"pack_num\":1,"
        "\"all_pack_num\":1},\"status\":0,\"data\":[\"" + row + "\"]}"
    };
    const auto parsed = tgw::protocol::parse_snapshot_packets(success);
    check(parsed.ok() && parsed.rows.size() == 1U, "snapshot success row");
    check(parsed.rows[0].bid_price[9] == 10, "snapshot packed level decode");
    check(parsed.rows[0].unverified_tail_raw[0] == "0" &&
          parsed.rows[0].unverified_tail_raw[15] == "0",
          "snapshot unverified wire tail retained as raw text");
    check(!parsed.rows[0].variety_category.has_value(),
          "wire-absent snapshot category stays absent");

    const std::vector<std::string> error{
        "{\"headers\":{\"id\":1,\"tag\":\"DataEmpty\","
        "\"pack_num\":0,\"all_pack_num\":0},\"status\":-100,\"data\":\"\"}"
    };
    const auto empty = tgw::protocol::parse_snapshot_packets(error);
    check(!empty.ok() && empty.error_code == -76 && empty.rows.empty(),
          "snapshot DataEmpty mapping");
}

std::string numeric_record(int count, const std::set<int>& string_slots,
                           const std::set<int>& character_slots = {}) {
    std::string json = "{";
    for (int slot = 1; slot <= count; ++slot) {
        if (slot != 1) json += ',';
        json += "\"" + std::to_string(slot) + "\":";
        if (string_slots.contains(slot)) json += "\"text\"";
        else if (character_slots.contains(slot)) json += "89";
        else json += std::to_string(slot);
    }
    json += '}';
    return json;
}

void test_etf_and_securities_records() {
    const std::set<int> etf_strings{1,17,18,19,20,31,32,33,35};
    const std::set<int> etf_chars{4,5,6,7,30,34};
    std::string etf = numeric_record(35, etf_strings, etf_chars);
    etf.pop_back();
    etf += ",\"36\":[]}";
    const std::vector<std::string> etf_packets{
        "{\"headers\":{\"id\":1,\"tag\":\"111\"},\"status\":0,"
        "\"data\":[" + etf + "]}"
    };
    const auto etf_rows = tgw::protocol::parse_etf_info_packets(etf_packets, 1);
    check(etf_rows.size() == 1U && etf_rows[0].basic.market_type == 16U,
          "ETF 35-slot record decode");
    static_assert(std::is_same_v<
        decltype(tgw::EtfBasicRow{}.market_type), std::uint8_t>);
    check(etf_rows[0].basic.publish == 'Y', "ETF ASCII code maps to native char");

    std::string bad_etf_char = etf;
    const auto publish = bad_etf_char.find("\"4\":89");
    check(publish != std::string::npos, "ETF char overflow fixture");
    bad_etf_char.replace(publish, 6U, "\"4\":256");
    check_throws<tgw::ProtocolError>([&] {
        (void)tgw::protocol::parse_etf_info_packets({
            "{\"headers\":{\"id\":1,\"tag\":\"111\"},\"status\":0,"
            "\"data\":[" + bad_etf_char + "]}"
        }, 1);
    }, "ETF character overflow rejected");

    const std::set<int> security_strings{1,3,4,5,6,9,10,15,34,37};
    const std::string security = numeric_record(43, security_strings);
    const std::vector<std::string> security_packets{
        "{\"headers\":{\"id\":2,\"tag\":\"109\",\"code_num\":1},"
        "\"status\":0,\"data\":[" + security + "]}"
    };
    const auto security_rows = tgw::protocol::parse_securities_info_packets(
        security_packets, 2);
    check(security_rows.size() == 1U && security_rows[0].position_type == 43U,
          "securities-info 43-slot record decode");
    static_assert(std::is_same_v<
        decltype(tgw::SecuritiesInfoRow{}.market_type), std::uint8_t>);
    static_assert(std::is_same_v<
        decltype(tgw::SecuritiesInfoRow{}.expire_date), std::uint32_t>);
    static_assert(std::is_same_v<
        decltype(tgw::SecuritiesInfoRow{}.par_value), std::int64_t>);

    std::string overflow = security;
    const auto market = overflow.find("\"2\":2");
    check(market != std::string::npos, "overflow fixture market slot");
    overflow.replace(market, 5U, "\"2\":256");
    check_throws<tgw::ProtocolError>([&] {
        (void)tgw::protocol::parse_securities_info_packets({
            "{\"headers\":{\"id\":2,\"tag\":\"109\",\"code_num\":1},"
            "\"status\":0,\"data\":[" + overflow + "]}"
        }, 2);
    }, "securities-info uint8 overflow rejected");

    std::string long_code = security;
    const auto code = long_code.find("\"1\":\"text\"");
    check(code != std::string::npos, "string capacity fixture");
    long_code.replace(code, 10U, "\"1\":\"123456789012345678901234567890123\"");
    check_throws<tgw::ProtocolError>([&] {
        (void)tgw::protocol::parse_securities_info_packets({
            "{\"headers\":{\"id\":2,\"tag\":\"109\",\"code_num\":1},"
            "\"status\":0,\"data\":[" + long_code + "]}"
        }, 2);
    }, "public header string byte capacity enforced");
}

void test_misc_parsers_and_validation() {
    const std::vector<std::string> code_packets{
        "{\"headers\":{\"id\":1,\"tag\":11103,\"pack_num\":1,"
        "\"all_pack_num\":1},\"status\":0,"
        "\"data\":[\"510300`symbol`name`101`ETF`CNY\"]}"
    };
    check(tgw::protocol::parse_code_table_packets(code_packets).size() == 1U,
          "code table row decode");
    const std::vector<std::string> factor_packets{
        "{\"headers\":{\"id\":1,\"tag\":11102,\"pack_num\":1,"
        "\"all_pack_num\":1},\"status\":0,"
        "\"data\":[\"inner,000001,20260825,1.250000000000000000,2.5\"]}"
    };
    const auto factors = tgw::protocol::parse_ex_factor_packets(factor_packets);
    check(factors.size() == 1U && factors[0].ex_factor == 1.25,
          "ex-factor double decode");
    check(factors[0].ex_factor_raw == "1.250000000000000000" &&
          factors[0].cum_factor_raw == "2.5",
          "ex-factor exact decimal tokens preserved");

    check_throws<std::invalid_argument>([] {
        (void)tgw::protocol::kline_wire_period(10001);
    }, "unverified K-line cycle");
    check(std::string(tgw::error_message(-76)) == "数据为空", "official error text");
    const auto task_id = tgw::next_task_id();
    check(task_id > 0, "local task id allocated");
}

} // namespace

int main() {
    try {
        static_assert(std::is_same_v<decltype(tgw::KlineRow{}.market_type), std::uint8_t>);
        static_assert(std::is_same_v<decltype(tgw::SnapshotRow{}.market_type), std::uint8_t>);
        static_assert(std::is_same_v<decltype(tgw::CodeTableRow{}.market_type), std::uint8_t>);
        static_assert(std::is_same_v<decltype(tgw::ExFactorRow{}.ex_date), std::uint32_t>);
        test_logon_contract();
        test_all_request_builders();
        test_zstd_multi_object_decode();
        test_kline_and_packet_integrity();
        test_snapshot_success_and_error();
        test_etf_and_securities_records();
        test_misc_parsers_and_validation();
        std::cout << "all offline C++ protocol tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
