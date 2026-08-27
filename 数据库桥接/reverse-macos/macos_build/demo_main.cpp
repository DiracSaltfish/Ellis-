// demo_main.cpp —— arm64 验证程序：完整走一遍 Init→Login→Subscribe→QueryKline→Release
#include "tgw_core_api.hpp"
#include <cstdio>
#include <string>

using namespace galaxy::tgw;

class DemoSpi : public IGMDSpi {
public:
    void OnLog(const int32_t& lv, const char* msg, uint32_t len) override {
        std::printf("  [spi-log %d] %.*s\n", lv, (int)len, msg);
    }
    void OnLogon(LogonResponse* r) override {
        std::printf("  [OnLogon] success=%d err=%d server=%s task=%llu\n",
                    r->is_success, r->err_code, r->server_version,
                    (unsigned long long)r->task_id);
    }
};

int main(int argc, char** argv) {
    const char* host = argc > 1 ? argv[1] : "127.0.0.1";
    uint16_t port = (uint16_t)(argc > 2 ? atoi(argv[2]) : 8600);
    const char* user = argc > 3 ? argv[3] : "demo_user";
    const char* pass = argc > 4 ? argv[4] : "demo_pass";

    std::printf("== tgw_macos demo (%s) ==\n", TGWCore::Instance().Version().c_str());

    DemoSpi spi;
    Cfg cfg;
    cfg.Set(user, pass, host, port, /*force_logout=*/false);

    ErrorCode ec = TGWCore::Instance().Init(&spi, cfg, kInternetMode);
    std::printf("Init -> %s\n", ec == kSuccess ? "OK" : "FAIL");
    if (ec != kSuccess) return 1;

    std::printf("Login -> %s\n",
                TGWCore::Instance().Login() == kSuccess ? "OK" : "FAIL");

    std::vector<std::string> codes{"510300.SH", "000001.SZ"};
    TGWCore::Instance().Subscribe(codes, /*snapshot*/1);
    TGWCore::Instance().QueryKline(codes, 20260801, 20260826, /*min1*/1);

    TGWCore::Instance().Release();
    std::printf("== done ==\n");
    return 0;
}
