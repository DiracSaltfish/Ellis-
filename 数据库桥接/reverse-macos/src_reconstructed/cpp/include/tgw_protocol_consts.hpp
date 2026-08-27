// tgw_protocol_consts.hpp —— 从 tgw.pdb 静态还原的线上协议常量（原创整理）
// 依据: docs/PROTOCOL_NOTES.md；字节序/封包顺序待动态验证(见文档§5)
#pragma once
#include <cstddef>
#include <cstdint>

namespace galaxy { namespace tgw { namespace proto {

#pragma pack(push, 1)
struct AmdHeader {                    // sizeof == 32, 紧凑无填充
    uint8_t  major;                   // +0
    uint8_t  minor;                   // +1
    uint16_t module_index;            // +2
    int8_t   sample_flag;             // +4
    uint8_t  relay_type;              // +5
    uint8_t  protocol_type;           // +6
    uint8_t  comp_type;               // +7
    uint8_t  compress;                // +8
    uint8_t  app_type;                // +9
    uint64_t msg_key;                 // +10
    uint64_t timepoint;               // +18
    uint32_t package_size;            // +26
    uint16_t data_offset;             // +30 → body 起始偏移
};
static_assert(sizeof(AmdHeader) == 32, "AmdHeader must be 32 bytes packed");
#pragma pack(pop)

// 控制消息类型
enum CtrlMsgType : int {
    kLogonRequest   = 665,
    kHeartbeat      = 666,
    kLogonOut       = 667,
    kLogonAck       = 668,
    kOverLoad       = 669,
    kInvalidRequest = 700,
    kCancelTaskReq  = 700,
    kCancelTaskAck  = 701,
};

// 数据请求类型 (amd::modules::query::ReqDataType)
enum ReqDataType : uint16_t {
    kSnapshot            = 100,
    kTickOrder           = 107,
    kTickExecution       = 108,
    kOrderQueue          = 109,
    k1MinKline           = 10000,
    k5MinKline           = 10001,
    k30MinKline          = 10002,
    k60MinKline          = 10003,
    k120MinKline         = 10004,
    k15MinKline          = 10006,
    k3MinKline           = 10007,
    k10MinKline          = 10008,
    kDayKline            = 10009,
    kWeekKline           = 10010,
    kMonthKline          = 10011,
    kSeasonKline         = 10012,
    kYearKline           = 10013,
    k1MinKlineOfAvg30Day = 10014,
    kSnapshotDerive      = 10100,
    kCodeTable           = 10200,
    kStockInfo           = 10203,
    kExFactorTable       = 10204,
    kFactor              = 10206,
    kThirdInfo           = 10210,
};

// UMS 连接状态机
enum UmsState : int {
    kUMSConnectSuccess  = 1,
    kUMSConnectFailed   = 2,
    kUMSLogonSuccess    = 3,
    kUMSLogonFailed     = 4,
    kUMSHeartbeatTimeout = 5,
};

}}} // namespace galaxy::tgw::proto
