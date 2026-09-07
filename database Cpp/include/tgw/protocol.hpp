#pragma once

#include "tgw/types.hpp"

#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace tgw::protocol {

struct EnvelopeMeta {
    std::optional<std::int64_t> request_id;
    std::optional<std::int64_t> status;
    std::string tag;
    std::string token;
    std::optional<std::int64_t> pack_num;
    std::optional<std::int64_t> all_pack_num;
};

TGW_API std::string escape_json(std::string_view value);
TGW_API std::vector<std::string> default_mac_addresses();
TGW_API std::string build_logon_request(
    std::string_view username,
    std::string_view password,
    bool force_logout,
    std::string_view client_version,
    std::int64_t process_id,
    std::span<const std::string> mac_addresses = {}
);
TGW_API std::string build_subscribe_request(
    std::string_view username,
    std::string_view token,
    std::int64_t request_id,
    std::span<const SubscribeItem> items,
    bool unsubscribe = false
);
TGW_API std::string build_third_info_request(
    std::string_view username,
    std::string_view token,
    std::int64_t request_id,
    const std::vector<std::pair<std::string, std::string>>& parameters,
    std::int64_t offset = 0,
    std::int64_t count = 1000
);
TGW_API std::string build_query_complete_request(
    std::string_view username, std::string_view token, std::int64_t request_id
);
TGW_API std::int64_t kline_wire_period(std::int64_t cyc_type);
TGW_API std::string build_kline_request(
    std::string_view username, std::string_view token,
    std::int64_t request_id, const KlineRequest& request
);
TGW_API std::string build_snapshot_request(
    std::string_view username, std::string_view token,
    std::int64_t request_id, const SnapshotRequest& request
);
TGW_API std::string build_code_table_request(
    std::string_view username, std::string_view token, std::int64_t request_id
);
TGW_API std::string build_get_package_request(
    std::string_view username, std::string_view token,
    std::int64_t request_id, std::int64_t pack_num
);
TGW_API std::string build_etf_info_request(
    std::string_view username, std::string_view token,
    std::int64_t request_id, std::span<const SecurityItem> items
);
TGW_API std::string build_codelist_complete_request(
    std::string_view username, std::string_view token, std::int64_t request_id
);
TGW_API std::string build_securities_info_request(
    std::string_view username, std::string_view token,
    std::int64_t request_id, std::span<const SecurityItem> items
);
TGW_API std::string build_ex_factor_request(
    std::string_view username, std::string_view token,
    std::int64_t request_id, std::string_view security_code
);

TGW_API EnvelopeMeta inspect_envelope(std::string_view json);
TGW_API std::vector<std::string> decode_server_payload(
    std::span<const std::uint8_t> payload,
    std::size_t max_output = 64U * 1024U * 1024U
);
TGW_API std::vector<KlineRow> parse_kline_packets(
    const std::vector<std::string>& packets, std::int64_t expected_tag
);
TGW_API QueryResult<SnapshotRow> parse_snapshot_packets(
    const std::vector<std::string>& packets
);
TGW_API std::vector<CodeTableRow> parse_code_table_packets(
    const std::vector<std::string>& packets
);
TGW_API std::vector<EtfRecord> parse_etf_info_packets(
    const std::vector<std::string>& packets,
    std::optional<std::int64_t> expected_request_id = std::nullopt
);
TGW_API std::vector<SecuritiesInfoRow> parse_securities_info_packets(
    const std::vector<std::string>& packets,
    std::optional<std::int64_t> expected_request_id = std::nullopt
);
TGW_API std::vector<ExFactorRow> parse_ex_factor_packets(
    const std::vector<std::string>& packets
);
TGW_API std::vector<std::string> parse_third_info_packets(
    const std::vector<std::string>& packets
);

TGW_API std::string scalar_to_json(const Scalar& value);
TGW_API std::string record_to_json(const Record& record);

} // namespace tgw::protocol
