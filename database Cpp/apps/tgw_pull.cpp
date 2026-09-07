#include "tgw/client.hpp"

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <typeinfo>
#include <vector>

namespace {

void usage(const char* program) {
    std::cerr
        << "Usage: " << program << " --config PATH COMMAND\n"
        << "Commands:\n"
        << "  login              authenticate only\n"
        << "  calendar           verified A010061003 page (20260801..20260826, SSE)\n"
        << "  kline-daily        verified 510300 SSE daily K-line (20260825)\n"
        << "  kline-minute       verified 159691 SZSE one-minute K-line (20260826)\n"
        << "  snapshot-sse       verified 510300 SSE L1 snapshot window\n"
        << "  etf-sse            verified 510300 SSE ETF information\n"
        << "  secinfo-pair       verified SSE/SZSE securities-information pair\n"
        << "  ex-factor          verified 000001 ex-factor table\n"
        << "  code-table         full-market code table (known server missing-packet risk)\n";
}

std::string json_string(std::string_view value) {
    return "\"" + tgw::protocol::escape_json(value) + "\"";
}

void print_summary(std::string_view command, std::size_t rows,
                   std::string_view detail = {}) {
    std::cout << "{\"command\":" << json_string(command)
              << ",\"ok\":true,\"rows\":" << rows;
    if (!detail.empty()) {
        std::cout << ',' << detail;
    }
    std::cout << "}\n";
}

} // namespace

int main(int argc, char** argv) {
    std::string config_path;
    std::string command;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--config" && index + 1 < argc) {
            config_path = argv[++index];
        } else if (argument == "--help" || argument == "-h") {
            usage(argv[0]);
            return 0;
        } else if (command.empty()) {
            command = argument;
        } else {
            usage(argv[0]);
            return 2;
        }
    }
    if (config_path.empty() || command.empty()) {
        usage(argv[0]);
        return 2;
    }

    try {
        tgw::Session session(tgw::load_ini_config(config_path));
        const auto login = session.connect_and_login();
        if (!login.authenticated) {
            std::cout << "{\"command\":" << json_string(command)
                      << ",\"ok\":false,\"phase\":\"login\",\"status\":"
                      << login.status << ",\"tag\":" << json_string(login.response_tag)
                      << "}\n";
            return 3;
        }
        if (command == "login") {
            std::cout << "{\"command\":\"login\",\"ok\":true,"
                         "\"authenticated\":true,\"tag\":"
                      << json_string(login.response_tag) << "}\n";
        } else if (command == "calendar") {
            const std::vector<std::pair<std::string, std::string>> parameters{
                {"function_id", "A010061003"}, {"start_date", "20260801"},
                {"end_date", "20260826"}, {"market", "SSE"}
            };
            const auto rows = session.query_third_info(parameters);
            print_summary(command, rows.size(),
                          "\"schema\":\"third-info JSON object rows\"");
        } else if (command == "kline-daily") {
            tgw::KlineRequest request{
                "510300", 101, 0, 1, 10008, 20260825, 20260825, 0, 0
            };
            const auto rows = session.query_kline(request);
            print_summary(command, rows.size(),
                rows.empty() ? "" : "\"security_code\":" +
                    json_string(rows.front().security_code) +
                    ",\"first_kline_time\":" + std::to_string(rows.front().kline_time));
        } else if (command == "kline-minute") {
            tgw::KlineRequest request{
                "159691", 102, 0, 1, 10000, 20260826, 20260826, 900, 1500
            };
            const auto rows = session.query_kline(request);
            print_summary(command, rows.size(),
                rows.empty() ? "" : "\"security_code\":" +
                    json_string(rows.front().security_code) +
                    ",\"first_kline_time\":" + std::to_string(rows.front().kline_time));
        } else if (command == "snapshot-sse") {
            tgw::SnapshotRequest request{
                "510300", 101, 20260825, 93000000, 93030000, 0, 0
            };
            const auto result = session.query_snapshot(request);
            std::cout << "{\"command\":" << json_string(command)
                      << ",\"ok\":" << (result.ok() ? "true" : "false")
                      << ",\"rows\":" << result.rows.size()
                      << ",\"error_code\":" << result.error_code << "}\n";
        } else if (command == "etf-sse") {
            const auto rows = session.query_etf_info({101, "510300"});
            std::size_t constituents = 0;
            for (const auto& row : rows) constituents += row.constituents.size();
            print_summary(command, rows.size(),
                          "\"constituents\":" + std::to_string(constituents));
        } else if (command == "secinfo-pair") {
            const auto rows = session.query_securities_info({
                {101, "510300"}, {102, "159919"}
            });
            print_summary(command, rows.size(), "\"columns\":43");
        } else if (command == "ex-factor") {
            const auto rows = session.query_ex_factor("000001");
            print_summary(command, rows.size(), "\"columns\":5");
        } else if (command == "code-table") {
            const auto rows = session.query_code_table();
            print_summary(command, rows.size(), "\"columns\":6");
        } else {
            throw std::invalid_argument("unknown command: " + command);
        }
        session.close();
        return 0;
    } catch (const std::exception& error) {
        std::cout << "{\"command\":" << json_string(command)
                  << ",\"ok\":false,\"error_type\":"
                  << json_string(typeid(error).name()) << ",\"message\":"
                  << json_string(error.what()) << "}\n";
        return 1;
    }
}
