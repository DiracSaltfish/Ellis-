// tgw_core.cpp —— TGWCore 骨架实现（原创代码）
#include "tgw_core_api.hpp"
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include <chrono>
#include <cstdio>

namespace galaxy { namespace tgw {

bool TcpProbeTransport::Connect(const std::string& host, uint16_t port) {
    Close();
    addrinfo hints{}; hints.ai_family = AF_INET; hints.ai_socktype = SOCK_STREAM;
    addrinfo* res = nullptr;
    char port_s[8]; std::snprintf(port_s, sizeof(port_s), "%u", port);
    if (getaddrinfo(host.c_str(), port_s, &hints, &res) != 0 || !res) return false;
    fd_ = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    bool ok = false;
    if (fd_ >= 0) {
        timeval tv{5, 0};
        setsockopt(fd_, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        setsockopt(fd_, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
        ok = connect(fd_, res->ai_addr, res->ai_addrlen) == 0;
        if (!ok) { ::close(fd_); fd_ = -1; }
    }
    freeaddrinfo(res);
    return ok;
}

void TcpProbeTransport::Close() {
    if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
}

TGWCore& TGWCore::Instance() {
    static TGWCore inst;
    return inst;
}

void TGWCore::EmitLog(int32_t level, const std::string& msg) {
    if (spi_) spi_->OnLog(level, msg.c_str(), (uint32_t)msg.size());
    else std::fprintf(stderr, "[tgw-core][%d] %s\n", level, msg.c_str());
}

ErrorCode TGWCore::Init(IGMDSpi* spi, const Cfg& cfg, ApiMode mode) {
    std::lock_guard<std::mutex> lk(mtx_);
    spi_ = spi; cfg_ = cfg; mode_ = mode;
    EmitLog(2, "Init: mode=" + std::to_string(mode) +
               " vip=" + cfg.server_vip + ":" + std::to_string(cfg.server_port));

    if (!transport_.Connect(cfg.server_vip, cfg.server_port)) {
        EmitLog(0, "transport connect failed");
        return kFail;
    }
    state_ = 1;
    EmitLog(2, "transport established (tcp probe ok)");
    return Handshake();
}

ErrorCode TGWCore::Handshake() {
    // 协议接缝：此处原版执行 TLS(证书 .ca.crt) + 私有帧协商。
    // 骨架仅完成状态迁移并填充本地 LogonResponse —— 不伪造服务器应答。
    std::memset(&logon_, 0, sizeof(logon_));
    logon_.is_success = true;                 // 本地状态机成功(≠服务端鉴权通过)
    logon_.err_code = kSuccess;
    std::snprintf(logon_.server_version, sizeof(logon_.server_version), "%s",
                  Version().c_str());
    auto now = std::chrono::system_clock::now().time_since_epoch();
    logon_.task_id = (uint64_t)std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
    state_ = 2;
    if (spi_) spi_->OnLogon(&logon_);
    EmitLog(2, "local state-machine LOGON_OK (no proprietary wire protocol)");
    return kSuccess;
}

ErrorCode TGWCore::Login() {
    std::lock_guard<std::mutex> lk(mtx_);
    return state_ >= 2 ? kSuccess : kFail;
}

void TGWCore::Release() {
    std::lock_guard<std::mutex> lk(mtx_);
    transport_.Close();
    state_ = 3;
    EmitLog(2, "released");
}

ErrorCode TGWCore::Subscribe(const std::vector<std::string>& codes, uint32_t sub_type) {
    std::lock_guard<std::mutex> lk(mtx_);
    if (state_ < 2) return kFail;
    EmitLog(2, "subscribe " + std::to_string(codes.size()) + " codes type=" + std::to_string(sub_type));
    return kSuccess;
}

ErrorCode TGWCore::QueryKline(const std::vector<std::string>& codes, int64_t begin_date,
                              int64_t end_date, uint32_t kline_type) {
    std::lock_guard<std::mutex> lk(mtx_);
    if (state_ < 2) return kFail;
    EmitLog(2, "query_kline n=" + std::to_string(codes.size()) +
               " [" + std::to_string(begin_date) + "," + std::to_string(end_date) + "]");
    return kSuccess;
}

int32_t tgw_core_tcp_connect(const char* host, uint16_t port) {
    static TcpProbeTransport t;              // 供 Python 层快速可达性验证
    return t.Connect(host ? host : "", port) ? 1 : 0;
}

const char* tgw_core_version() {
    static const std::string v = TGWCore::Instance().Version();
    return v.c_str();
}

}} // namespace galaxy::tgw
