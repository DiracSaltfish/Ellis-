#include "tgw/session.hpp"

#include <algorithm>
#include <charconv>
#include <cstdlib>
#include <ctime>
#include <cstdio>
#include <fstream>
#include <limits>
#include <map>
#include <mutex>
#include <sstream>
#include <string_view>

#ifndef TGW_DEFAULT_CA_FILE
#define TGW_DEFAULT_CA_FILE ""
#endif

namespace tgw {
namespace {

std::string trim(std::string value) {
    const auto begin = value.find_first_not_of(" \t\r\n");
    if (begin == std::string::npos) {
        return {};
    }
    const auto end = value.find_last_not_of(" \t\r\n");
    return value.substr(begin, end - begin + 1U);
}

std::string strip_inline(std::string value) {
    const auto hash = value.find('#');
    if (hash != std::string::npos) {
        value.resize(hash);
    }
    const auto semicolon = value.find(';');
    if (semicolon != std::string::npos) {
        value.resize(semicolon);
    }
    return trim(std::move(value));
}

std::string env_or(std::string_view name, std::string fallback) {
    const char* value = std::getenv(std::string(name).c_str());
    return value != nullptr && *value != '\0' ? std::string(value) : std::move(fallback);
}

std::int64_t parse_integer(const std::string& value, std::string_view field) {
    std::int64_t result = 0;
    const char* begin = value.data();
    const char* end = value.data() + value.size();
    const auto parsed = std::from_chars(begin, end, result);
    if (parsed.ec != std::errc{} || parsed.ptr != end) {
        throw std::invalid_argument("invalid integer in [galaxy]." + std::string(field));
    }
    return result;
}

std::vector<std::string> split_hosts(std::string value) {
    static constexpr std::string_view full_width_comma = "，";
    std::size_t position = 0;
    while ((position = value.find(full_width_comma, position)) != std::string::npos) {
        value.replace(position, full_width_comma.size(), " ");
        ++position;
    }
    std::istringstream stream(value);
    std::vector<std::string> hosts;
    std::string host;
    while (stream >> host) {
        hosts.push_back(std::move(host));
    }
    return hosts;
}

std::vector<std::string> split_csv(const std::string& value) {
    std::vector<std::string> output;
    std::size_t begin = 0;
    while (begin <= value.size()) {
        const auto comma = value.find(',', begin);
        std::string item = trim(value.substr(
            begin, comma == std::string::npos ? std::string::npos : comma - begin));
        if (!item.empty()) {
            output.push_back(std::move(item));
        }
        if (comma == std::string::npos) {
            break;
        }
        begin = comma + 1U;
    }
    return output;
}

std::chrono::milliseconds seconds_from_env(std::string_view name, double fallback) {
    const std::string text = env_or(name, std::to_string(fallback));
    std::size_t consumed = 0;
    double value = 0.0;
    try {
        value = std::stod(text, &consumed);
    } catch (const std::exception&) {
        throw std::invalid_argument("invalid positive seconds value in " + std::string(name));
    }
    if (consumed != text.size() || value < 0.0) {
        throw std::invalid_argument("invalid positive seconds value in " + std::string(name));
    }
    return std::chrono::milliseconds(static_cast<std::int64_t>(value * 1000.0));
}

} // namespace

Config load_ini_config(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::invalid_argument("cannot open TGW INI config: " + path);
    }
    std::map<std::string, std::string> values;
    bool in_galaxy = false;
    std::string line;
    while (std::getline(input, line)) {
        const std::string stripped = trim(line);
        if (stripped.empty() || stripped.front() == '#' || stripped.front() == ';') {
            continue;
        }
        if (stripped.front() == '[' && stripped.back() == ']') {
            in_galaxy = trim(stripped.substr(1, stripped.size() - 2U)) == "galaxy";
            continue;
        }
        if (!in_galaxy) {
            continue;
        }
        const auto equals = line.find('=');
        if (equals == std::string::npos) {
            continue;
        }
        values[trim(line.substr(0, equals))] = trim(line.substr(equals + 1U));
    }
    for (const std::string key : {"host", "port", "username", "password"}) {
        if (!values.contains(key)) {
            throw std::invalid_argument("TGW INI is missing [galaxy]." + key);
        }
    }
    const std::string mode = strip_inline(values.contains("api_mode")
        ? values["api_mode"] : "kInternetMode");
    if (mode != "kInternetMode" && mode != "2") {
        throw std::invalid_argument("native C++ client supports kInternetMode only");
    }

    Config config;
    config.hosts = split_hosts(strip_inline(values["host"]));
    if (config.hosts.empty()) {
        throw std::invalid_argument("TGW INI contains no host");
    }
    const auto port = parse_integer(strip_inline(values["port"]), "port");
    if (port < 1 || port > std::numeric_limits<std::uint16_t>::max()) {
        throw std::invalid_argument("TGW port must be in 1..65535");
    }
    config.port = static_cast<std::uint16_t>(port);
    config.username = strip_inline(values["username"]);
    config.password = trim(values["password"]);
    if (config.username.empty() || config.password.empty()) {
        throw std::invalid_argument("TGW username/password must not be empty");
    }
    config.ca_file = env_or("TGW_CA_FILE", TGW_DEFAULT_CA_FILE);
    config.tls_server_name = env_or("TGW_TLS_SERVER_NAME", "www.dgw.com");
    config.client_version = env_or("TGW_CLIENT_VERSION", kClientVersion);
    config.timeout = seconds_from_env("TGW_TIMEOUT_SEC", 15.0);
    config.heartbeat = seconds_from_env("TGW_HEARTBEAT_SEC", 5.0);
    config.query_endpoints = split_csv(env_or(
        "TGW_QUERY_ENDPOINTS", "/amd/dgw/dgw1_query,/amd/dgw/dgw2_query"));
    if (config.query_endpoints.empty()) {
        throw std::invalid_argument("TGW_QUERY_ENDPOINTS contains no endpoint");
    }
    return config;
}

std::int64_t next_task_id() {
    static std::mutex mutex;
    static std::time_t previous_second = 0;
    static std::int64_t sequence = 0;
    std::scoped_lock lock(mutex);
    const std::time_t now = std::time(nullptr);
    if (now != previous_second) {
        previous_second = now;
        sequence = 0;
    }
    if (sequence >= 1000000) {
        throw std::runtime_error("task-id sequence exhausted for current second");
    }
    ++sequence;
    std::tm local{};
    localtime_r(&now, &local);
    char second_buffer[16]{};
    if (std::strftime(second_buffer, sizeof(second_buffer), "%m%d%H%M%S", &local) == 0) {
        throw std::runtime_error("cannot format TGW task id");
    }
    char sequence_buffer[16]{};
    std::snprintf(sequence_buffer, sizeof(sequence_buffer), "%06lld",
                  static_cast<long long>(sequence));
    return std::stoll(std::string(second_buffer) + sequence_buffer);
}

const char* error_message(int error_code) noexcept {
    switch (error_code) {
    case -100: return "失败";
    case -99: return "未初始化";
    case -98: return "空指针";
    case -97: return "参数非法";
    case -96: return "网络异常";
    case -95: return "数据无权限";
    case -94: return "未登录";
    case -93: return "分配内存失败";
    case -92: return "通道错误";
    case -91: return "查询服务端hqs任务队列溢出";
    case -90: return "账号已登录";
    case -89: return "查询服务端HQS系统错误";
    case -88: return "非查询时间段(非查询时间段不支持查询)";
    case -87: return "数据库和代码表中没有指定的代码";
    case -86: return "api模式非法";
    case -85: return "超过最大可用线程资源";
    case -84: return "数据解析出错";
    case -83: return "获取数据超时";
    case -82: return "周流量耗尽";
    case -81: return "代码表缓存不可用";
    case -80: return "超过最大订阅限制";
    case -79: return "丢失连接";
    case -78: return "超过最大查询数（含代码表）";
    case -77: return "三方资讯查询未设置功能号";
    case -76: return "数据为空";
    case -75: return "用户不存在";
    case -74: return "账号/密码错误";
    case -73: return "api接口不能同时多次调用";
    case -70: return "任务id重复";
    case -69: return "查询服务端DQS系统错误";
    case 0: return "成功";
    default: return "unknown error code";
    }
}

} // namespace tgw
