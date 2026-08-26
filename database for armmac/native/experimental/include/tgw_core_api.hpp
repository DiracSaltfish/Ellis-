// tgw_core_api.hpp —— galaxy::tgw API 骨架（原创实现；接口命名与官方公开头文件对齐）
//
// 范围声明：本骨架覆盖 Init/Login/Subscribe/Query/Close 生命周期状态机与 SPI 回调分发，
// 线上字节流统一收口到 ITcpTransport（协议接缝）。厂商私有线上协议不在本骨架内。
#pragma once
#include <atomic>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

namespace galaxy { namespace tgw {

enum ApiMode : uint16_t { kColocationMode = 1, kInternetMode = 2 };
enum ErrorCode : int32_t { kSuccess = 0, kFail = -1 };

struct Cfg {
    char username[64];
    char password[64];
    char server_vip[64];
    uint16_t server_port;
    bool force_logout;
    uint32_t heartbeat_sec;
    uint32_t reconnect_max_times;
    int32_t min_log_level;

    void Set(const std::string& u, const std::string& p,
             const std::string& vip, uint16_t port, bool force_logout = false) {
        std::snprintf(username, sizeof(username), "%s", u.c_str());
        std::snprintf(password, sizeof(password), "%s", p.c_str());
        std::snprintf(server_vip, sizeof(server_vip), "%s", vip.c_str());
        this->server_port = port;
        this->force_logout = force_logout;
        heartbeat_sec = 5;
        reconnect_max_times = 32;
        min_log_level = 2;
    }
};

struct LogonResponse {
    bool is_success;
    int32_t err_code;
    char err_msg[256];
    uint64_t task_id;
    char server_version[32];
};

// ---- 数据结构（字段名对齐官方 tgw_struct.h）----
struct MDSnapshotL1 {
    char code[32];
    int64_t trade_time;
    double last, open, high, low, close, pre_close;
    uint64_t volume, amount;
};

struct MDKLine {
    char code[32];
    int64_t kline_time;
    double open, high, low, close;
    uint64_t volume, amount;
    uint32_t kline_type;
};

// ---- SPI 接口（回调默认空实现，应用层按需覆写）----
class IGMDSpi {
public:
    virtual ~IGMDSpi() = default;
    virtual void OnLog(const int32_t& level, const char* log, uint32_t len) { (void)level; (void)log; (void)len; }
    virtual void OnLogon(LogonResponse* data) { (void)data; }
    virtual void OnEvent(uint32_t level, uint32_t code, const char* msg, uint32_t len) { (void)level; (void)code; (void)msg; (void)len; }
    virtual void OnMDSnapshot(MDSnapshotL1* data, uint32_t cnt) { (void)data; (void)cnt; }
    virtual void OnKLine(MDKLine* data, uint32_t cnt, uint32_t kline_type) { (void)data; (void)cnt; (void)kline_type; }
};

// ---- 协议接缝：所有网络字节流经此接口 ----
class ITcpTransport {
public:
    virtual ~ITcpTransport() = default;
    virtual bool Connect(const std::string& host, uint16_t port) = 0;
    virtual void Close() = 0;
    virtual bool IsOpen() const = 0;
};

class TcpProbeTransport : public ITcpTransport {
public:
    bool Connect(const std::string& host, uint16_t port) override;
    void Close() override;
    bool IsOpen() const override { return fd_ >= 0; }
private:
    int fd_ = -1;
};

// ---- API 主类 ----
class TGWCore {
public:
    static TGWCore& Instance();

    // IGMDApi_Init 对应入口：建立传输通道并执行登录状态机
    ErrorCode Init(IGMDSpi* spi, const Cfg& cfg, ApiMode mode);
    ErrorCode Login();
    void Release();

    ErrorCode Subscribe(const std::vector<std::string>& codes, uint32_t sub_type);
    ErrorCode QueryKline(const std::vector<std::string>& codes, int64_t begin_date,
                         int64_t end_date, uint32_t kline_type);

    const LogonResponse& logon_response() const { return logon_; }
    std::string Version() const { return "tgw-core-skeleton/1.0 (arm64)"; }

private:
    TGWCore() = default;
    void EmitLog(int32_t level, const std::string& msg);
    ErrorCode Handshake();          // 应用层握手（协议接缝处，骨架为探测+状态迁移）

    IGMDSpi* spi_ = nullptr;
    Cfg cfg_{};
    ApiMode mode_ = kInternetMode;
    std::atomic<int> state_{0};     // 0=idle 1=transport-up 2=logon-ok 3=released
    LogonResponse logon_{};
    TcpProbeTransport transport_;
    std::mutex mtx_;
};

extern "C" {
// 供 Python ctypes 桥接的稳定 ABI
int32_t  tgw_core_tcp_connect(const char* host, uint16_t port);
const char* tgw_core_version();
}

}} // namespace galaxy::tgw
