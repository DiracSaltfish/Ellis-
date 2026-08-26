import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

function reportFatal(error) {
  console.error(JSON.stringify({
    name: error?.name || "Error",
    message: error?.message || String(error),
    stackTail: String(error?.stack || "").split("\n").slice(-6),
  }));
  process.exit(1);
}
process.on("uncaughtException", reportFatal);
process.on("unhandledRejection", reportFatal);

const root = "/Users/ellis/工具程序开发/database for armmac";
const outputDir = path.join(root, "outputs/01a03c21-40ce-7230-8082-fa2313f6d1c6");
const outputFile = path.join(outputDir, "TGW_Mac_API_验收台账.xlsx");

const matrixMd = await fs.readFile(path.join(root, "docs/PDF_API_PARITY_MATRIX.md"), "utf8");
const inventoryMd = await fs.readFile(path.join(root, "docs/PDF_API_INVENTORY.md"), "utf8");

function stripMd(value) {
  return String(value ?? "")
    .replace(/`/g, "")
    .replace(/\*\*/g, "")
    .replace(/\[(.*?)\]\([^)]*\)/g, "$1")
    .replace(/<br\s*\/?>/gi, "；")
    .trim();
}

function parseTableBetween(startMarker, endMarker) {
  const start = matrixMd.indexOf(startMarker);
  const end = endMarker ? matrixMd.indexOf(endMarker, start + startMarker.length) : matrixMd.length;
  const chunk = matrixMd.slice(start, end < 0 ? matrixMd.length : end);
  const tableLines = chunk.split("\n").filter((line) => line.trim().startsWith("|"));
  if (tableLines.length < 3) return [];
  const parseLine = (line) => line.trim().slice(1, -1).split("|").map(stripMd);
  return tableLines.slice(2).map(parseLine).filter((row) => row.some(Boolean));
}

function normalizeStatus(full) {
  const known = [
    "PILOT_READY", "LIVE_ALIGNED", "ARM_IMPLEMENTED", "WIRE_VERIFIED",
    "LINUX_OBSERVED", "STATIC_MATCHED", "INVENTORIED", "CHANGES_REQUESTED",
    "BLOCKED", "OUT_OF_SCOPE_COLOC", "NOT_IMPLEMENTED", "SKELETON_ONLY",
  ];
  return known.find((status) => String(full).includes(status)) || "INVENTORIED";
}

function postMarketFor(api, category = "", mode = "") {
  const text = `${api} ${category} ${mode}`;
  if (/UpdatePassWord|update_password/i.test(text)) return "禁止默认执行";
  if (/Subscribe|UnSubscribe|SubFactor|UnSubFactor|实时|OnKLine/.test(text)) return "否（待开盘）";
  if (/coloc only|托管/.test(text)) return "否（模式不适用）";
  return "是";
}

function riskFor(api, mode = "") {
  if (/UpdatePassWord|update_password/i.test(api)) return "高";
  if (/coloc only/.test(mode)) return "阻塞";
  if (/Subscribe|UnSubscribe|SubFactor|Replay|实时/.test(api)) return "中";
  return "低";
}

const contracts = {
  GetVersion: ["无", "str", "同步返回版本字符串"],
  Init: ["Cfg(pack(1), sizeof=145)", "LogonResponse(pack(1), sizeof=14)", "IGMDSpi::OnLogon"],
  Login: ["Cfg / ApiMode", "bool + LogonResponse", "同步封装；底层 OnLogon"],
  Release: ["无", "无", "释放连接与回调生命周期"],
  Close: ["无", "无", "同步关闭；必须 finally"],
  FreeMemory: ["回调内存指针", "无", "官方内存所有权；Python 兼容策略待定"],
  GetTaskID: ["无", "int64_t", "同步返回任务号"],
  UpdatePassWord: ["UpdatePassWordReq", "错误码", "写操作；未获专项授权不执行"],
  Subscribe: ["SubscribeItem(pack(1), sizeof=42)", "Push JSON / Snapshot族", "IGMDSpi 多类行情回调；Mac 暂为 ReceiveRawEvent"],
  UnSubscribe: ["SubscribeItem(pack(1), sizeof=42)", "错误/完成事件", "取消后再 Close"],
  SubFactor: ["SubFactorItem", "Factor", "OnFactor"],
  UnSubFactor: ["SubFactorItem", "完成事件", "取消因子订阅"],
  SubscribeDerivedData: ["SubscribeDerivedDataItem", "MDOrderBook", "OnMDOrderBook（仅托管）"],
  QueryKline: ["ReqKline(pack(1), sizeof=71)", "MDKLine；公开 dict 11 字段", "同步 list/DataFrame；异步 IGMDKlineSpi 尚未实现"],
  QuerySnapshot: ["ReqDefault(pack(1), sizeof=55；含 level_type:uint16)", "Snapshot族", "同步 list/DataFrame；类型化 SPI 尚未实现"],
  QueryOrderQueue: ["ReqDefault", "MDOrderQueue", "IGMDOrderQueueSpi（仅托管）"],
  QueryTickExecution: ["ReqDefault", "MDTickExecution", "仅托管"],
  QueryTickOrder: ["ReqDefault", "MDTickOrder", "仅托管"],
  QueryCodeTable: ["无业务入参", "MDCodeTable 6 字段；pack(1)=191", "IGMDCodeTableSpi；需异步累计完整批次"],
  QuerySecuritiesInfo: ["SubCodeTableItem", "SecuritiesInfo", "证券信息 SPI"],
  QueryExFactorTable: ["证券代码/市场", "ExFactor 行", "除权因子 SPI"],
  QueryFactor: ["ReqFactor", "Factor", "Factor SPI"],
  SetThirdInfoParam: ["task_id:int64 + key/value:str", "int 错误码", "参数暂存；QueryThirdInfo 后移除"],
  QueryThirdInfo: ["task_id:int64", "ThirdInfoData：嵌套 JSON body.data", "同步 list/DataFrame；分页完成语义"],
  QueryETFInfo: ["SubCodeTableItem(pack(1), sizeof=36；market:int32)", "35字段基础信息 + ConstituentStockInfo[13字段][]", "同步 JSON/DataFrame 嵌套返回已实现；IGMDETFInfoSpi 尚未实现"],
  ReplayRequest: ["ReqReplay", "历史回放结构", "Replay SPI / CancelTask"],
};

function nativeConstruction(api) {
  const map = {
    QueryKline: "ReqKline → ReqGetKline；公共 cyc_type 与 wire period_type 分层映射",
    QuerySnapshot: "ReqDefault → ReqGetSnapshot；严格限制已验证 data_type/level_type/市场",
    SetThirdInfoParam: "GetTaskID → 多次写入 key/value",
    QueryThirdInfo: "参数表 → ReqGetThirdInfo → 1..N 包 → ReqGetComplete",
    QueryCodeTable: "无业务入参 → 异步 SPI 累计 1..N 批 → 完成状态；不得依赖同步首批结果",
    QueryETFInfo: "单个 SubCodeTableItem(101+510300) → push WSS ReqGetETFCodeTableList(Security=code|market) → tag 111 → ReqGetCodelistComplete → 基础信息+成分股两级容器",
    Subscribe: "SubscribeItem[] → ReqSubscribeBatch；公共 flag 转 wire subscribeDataType",
    UnSubscribe: "同一 SubscribeItem[] → 取消请求 → Close",
  };
  return map[api] || "按 C++ 手册最小只读调用；先 Linux oracle，再实现 Mac";
}

const nativeRows = [];
for (const [apiCell, pdfPage, keyContract, mode, fullStatus, next] of parseTableBetween("## 1. 原生 TGW", "## 2. 原生 TGW")) {
  const names = apiCell === "Init/Login" ? ["Init", "Login"] : apiCell === "Release/Close" ? ["Release", "Close"] : [apiCell];
  for (const api of names) {
    nativeRows.push({
      source: "TGW C++ 开发手册",
      pdfPage,
      category: "基础/订阅",
      api,
      lowLevel: api,
      mode,
      postMarket: postMarketFor(api, "", mode),
      risk: riskFor(api, mode),
      status: normalizeStatus(fullStatus),
      fullStatus,
      linuxDate: /LIVE_ALIGNED|WIRE_VERIFIED|LINUX_OBSERVED/.test(fullStatus) ? "2026-08-26" : "",
      linuxResult: /LIVE_ALIGNED|WIRE_VERIFIED/.test(fullStatus) ? "已有脱敏证据；范围见状态/证据" : "未执行",
      wire: "待逐接口取证",
      macDate: /LIVE_ALIGNED|ARM_IMPLEMENTED|WIRE_VERIFIED|CHANGES_REQUESTED/.test(fullStatus) ? "2026-08-26" : "",
      macResult: /LIVE_ALIGNED/.test(fullStatus) ? "同参子范围已对齐" : /ARM_IMPLEMENTED/.test(fullStatus) ? "本地实现，待 live 同参" : "待实现/复验",
      conclusion: fullStatus,
      construction: nativeConstruction(api),
      requestType: contracts[api]?.[0] || keyContract,
      responseType: contracts[api]?.[1] || keyContract,
      callback: contracts[api]?.[2] || keyContract,
      evidence: "docs/PDF_API_PARITY_MATRIX.md",
      next,
      notes: keyContract,
      batch: "",
    });
  }
}

for (const [api, pdfPage, keyContract, mode, fullStatus, next] of parseTableBetween("## 2. 原生 TGW", "## 3. AmazingData")) {
  nativeRows.push({
    source: "TGW C++ 开发手册",
    pdfPage,
    category: "查询/回放",
    api,
    lowLevel: api,
    mode,
    postMarket: postMarketFor(api, "", mode),
    risk: riskFor(api, mode),
    status: normalizeStatus(fullStatus),
    fullStatus,
    linuxDate: /LIVE_ALIGNED|WIRE_VERIFIED|LINUX_OBSERVED/.test(fullStatus) ? "2026-08-26" : "",
    linuxResult: /LIVE_ALIGNED|WIRE_VERIFIED/.test(fullStatus) ? "已有脱敏证据；范围见状态/证据" : "未执行",
    wire: api === "QueryKline" ? "ReqGetKline；daily=10100，weekly=10101，monthly=10102（wire/tag）" : api === "QuerySnapshot" ? "ReqGetSnapshot；tag=11000" : api === "QueryThirdInfo" ? "ReqGetThirdInfo；tag=11101（仅日历证据）" : api === "QueryETFInfo" ? "push WSS ReqGetETFCodeTableList；Security=code|market；tag=字符串111；ReqGetCodelistComplete" : "待逐接口取证",
    macDate: /LIVE_ALIGNED|ARM_IMPLEMENTED|WIRE_VERIFIED|CHANGES_REQUESTED/.test(fullStatus) ? "2026-08-26" : "",
    macResult: /LIVE_ALIGNED/.test(fullStatus) ? "同参子范围已对齐" : /ARM_IMPLEMENTED/.test(fullStatus) ? "本地实现，待 live 同参" : "待实现/复验",
    conclusion: fullStatus,
    construction: nativeConstruction(api),
    requestType: contracts[api]?.[0] || keyContract,
    responseType: contracts[api]?.[1] || keyContract,
    callback: contracts[api]?.[2] || keyContract,
    evidence: api === "QueryKline" ? "docs/evidence/query_kline_daily.md；docs/evidence/query_kline_week.md；docs/evidence/query_kline_month.md" : api === "QuerySnapshot" ? "docs/evidence/query_snapshot_szse_etf.md" : api === "QueryCodeTable" ? "docs/evidence/query_code_table_static.md" : api === "QueryETFInfo" ? "docs/evidence/query_etf_info_static.md；docs/evidence/query_etf_info_sse_etf.md" : "docs/PDF_API_PARITY_MATRIX.md",
    next,
    notes: keyContract,
    batch: "",
  });
}

const amazingRows = [];
for (const [category, apiCell, pdfPage, fullStatus] of parseTableBetween("## 3. AmazingData", "## 4. AmazingData")) {
  const names = apiCell === "login, logout" ? ["login", "logout"] : [apiCell];
  for (const api of names) {
    const realtime = /实时/.test(category) || /^onSnapshot|^OnKLine/.test(api);
    const lowLevel = api === "query_kline" ? "QueryKline" : api === "query_snapshot" ? "QuerySnapshot" : ["login", "logout", "update_password"].includes(api) ? api : "QueryThirdInfo / 高层 wrapper（待逐项证明）";
    amazingRows.push({
      source: "AmazingData 开发手册",
      pdfPage,
      category,
      api,
      lowLevel,
      mode: realtime ? "internet/实时" : "internet/查询",
      postMarket: postMarketFor(api, category, ""),
      risk: riskFor(`${api} ${category}`, ""),
      status: normalizeStatus(fullStatus),
      fullStatus,
      linuxDate: /LIVE_ALIGNED|WIRE_VERIFIED|LINUX_OBSERVED/.test(fullStatus) ? "2026-08-26" : "",
      linuxResult: /LIVE_ALIGNED|WIRE_VERIFIED/.test(fullStatus) ? "已有范围化证据" : "未执行",
      wire: api === "query_kline" ? "委托 QueryKline；底层日线/周线/月线已知，高层 wrapper 未验" : api === "query_snapshot" ? "委托 QuerySnapshot；实验" : /get_calendar/.test(api) ? "ReqGetThirdInfo；已知 function_id 子范围与 PDF 版本需复核" : "待 Linux capture 确认",
      macDate: /LIVE_ALIGNED|ARM_IMPLEMENTED|WIRE_VERIFIED|CHANGES_REQUESTED/.test(fullStatus) ? "2026-08-26" : "",
      macResult: /LIVE_ALIGNED/.test(fullStatus) ? "底层子范围已对齐；高层 wrapper 仍按备注" : "未实现/待验证",
      conclusion: fullStatus,
      construction: realtime ? "高层订阅 wrapper → Subscribe；盘后只做静态登记" : "高层参数 → 底层只读请求；单接口、单市场、窄日期窗",
      requestType: /query_kline/.test(api) ? "ReqKline 字段" : /query_snapshot/.test(api) ? "ReqDefault 字段" : "按 PDF 输入参数；必填性/默认值待逐项静态表",
      responseType: "按 PDF 输出表；逐列核对 Python 类型、空值和缩放",
      callback: realtime ? "官方高层回调；Mac 当前仅部分 raw event" : "同步 DataFrame/list；异常与空结果语义待逐项核对",
      evidence: "docs/PDF_API_PARITY_MATRIX.md",
      next: realtime ? "开盘后领取；先 Linux oracle，再 Mac" : "盘后优先：Linux 最小只读请求 → wire → Mac → 同参",
      notes: "不得因共用 QueryThirdInfo 通道直接标记同族成功",
      batch: "",
    });
  }
}

function parseInventoryRows() {
  const rows = [];
  let headers = null;
  const byPrefix = (obj, prefix) => {
    const key = Object.keys(obj).find((candidate) => candidate.startsWith(prefix));
    return key ? obj[key] : "";
  };
  for (const rawLine of inventoryMd.split("\n")) {
    const line = rawLine.trim();
    if (!line.startsWith("|") || !line.endsWith("|")) continue;
    const cells = line.slice(1, -1).split("|").map(stripMd);
    if (cells[0] === "#") {
      headers = cells;
      continue;
    }
    if (/^---/.test(cells[0])) continue;
    if (!headers || !/^(?:\d+|E-\d+)$/.test(cells[0])) continue;
    const obj = Object.fromEntries(headers.map((header, index) => [header, cells[index] ?? ""]));
    const api = byPrefix(obj, "公开API") || obj["条目"] || obj["功能号/接口"] || obj["结构"] || obj["数据项"] || `候选项 ${cells[0]}`;
    const sourceKey = obj["来源PDF"] || obj["来源"] || "PDF/HDR";
    const source = sourceKey === "TGW-C++" ? "TGW C++ 开发手册" : sourceKey === "AD-Py" ? "AmazingData 开发手册" : sourceKey;
    const fullStatus = obj["当前证据状态"] || "INVENTORIED";
    const category = obj["类别"] || "字典/差异";
    const mode = obj["模式"] || "按文档";
    let postMarket = obj["盘后只读可测"] || postMarketFor(api, category, mode);
    if (/受限/.test(postMarket)) postMarket = "否（待开盘）";
    else if (/写操作禁止/.test(postMarket)) postMarket = "禁止默认执行";
    else if (/^否/.test(postMarket)) postMarket = /托管|回放|模式/.test(`${mode} ${obj["依赖/风险"]}`) ? "否（模式不适用）" : "否（待开盘）";
    else if (/^是/.test(postMarket)) postMarket = "是";
    else if (/待取证/.test(postMarket)) postMarket = "待取证";
    rows.push({
      source,
      pdfPage: obj["PDF页"] || obj["位置"] || "—",
      category,
      api,
      lowLevel: source === "AmazingData 开发手册" ? (obj["依赖/风险"]?.match(/(?:TGW\s*)?#?\d+\s*[^；，]*/)?.[0] || "高层 wrapper；底层待取证") : api,
      mode,
      postMarket,
      risk: riskFor(`${api} ${category}`, mode),
      status: normalizeStatus(fullStatus),
      fullStatus,
      linuxDate: /LIVE_ALIGNED|WIRE_VERIFIED|LINUX_OBSERVED/.test(fullStatus) ? "2026-08-26" : "",
      linuxResult: /LIVE_ALIGNED|WIRE_VERIFIED|LINUX_OBSERVED/.test(fullStatus) ? "已有脱敏证据；严格限于状态所述子范围" : "未执行",
      wire: obj["关键枚举/参数"] || obj["说明"] || "待逐接口取证",
      macDate: /LIVE_ALIGNED|ARM_IMPLEMENTED|WIRE_VERIFIED|CHANGES_REQUESTED/.test(fullStatus) ? "2026-08-26" : "",
      macResult: /LIVE_ALIGNED/.test(fullStatus) ? "已有 Mac 子范围证据" : /ARM_IMPLEMENTED/.test(fullStatus) ? "已有实现；待同参 live" : "待实现/复验",
      conclusion: fullStatus,
      construction: `${obj["推荐最小样本"] || "按单接口最小只读样本"}；Linux oracle → wire → Mac → 同参`,
      requestType: obj["请求结构"] || byPrefix(obj, "请求参数") || obj["字段/关键值"] || obj["说明"] || "参见 PDF/头文件",
      responseType: obj["响应结构"] || byPrefix(obj, "响应要点") || obj["字段表"] || "参见 PDF 返回结构；待逐列登记类型",
      callback: obj["回调"] || "同步/回调合约待逐项核对",
      evidence: "docs/PDF_API_INVENTORY.md",
      next: obj["推荐最小样本"] || "领取单接口任务卡并按工作流闭环",
      notes: obj["依赖/风险"] || obj["说明"] || "",
      batch: "",
    });
  }
  return rows;
}

const inventoryRows = parseInventoryRows();
const apiRows = inventoryRows.length ? inventoryRows : [...nativeRows, ...amazingRows];

// Build the ThirdInfo function-number catalogue from the reviewed PDF inventory.
const functionRows = inventoryRows
  .filter((row) => /^资讯-/.test(row.category) && /A\d{9}/.test(row.api))
  .map((row) => {
    const functionId = row.api.match(/A\d{9}/)?.[0] || "";
    const name = row.api.replace(functionId, "").trim();
    return [
      row.category, name, functionId, row.pdfPage, "是", row.status,
      row.requestType || "参见 PDF 请求参数表；盘后以单代码/窄日期窗构造",
      row.responseType || "参见 PDF 返回值表；逐列登记 int32/double/string 与空值",
      /对应 AD/.test(row.notes) ? row.notes : "待映射到 AmazingData wrapper",
      /A010060001|A010061001/.test(functionId) ? "现有 A010061003 线上证据与本 PDF 目录不一致，必须保留版本差异" : row.notes,
    ];
  });

const structureRows = parseTableBetween("## 4. AmazingData", "## 5. 推荐领取顺序").map(([name, pdfPage, fullStatus, focus]) => [
  "AmazingData 开发手册", name, pdfPage, "输出结构", "按 PDF 字段表逐列登记", normalizeStatus(fullStatus), fullStatus, focus,
]);
structureRows.unshift(
  ["TGW C++ 开发手册", "Cfg", "24（16）/头文件", "pack(1) ctypes", "sizeof=145；服务器、账号、密码、模式等固定宽度字段", "STATIC_MATCHED", "STATIC_MATCHED", "禁止打印凭据字段"],
  ["TGW C++ 开发手册", "LogonResponse", "24（16）/头文件", "pack(1) ctypes", "sizeof=14；状态/版本等控制字段", "STATIC_MATCHED", "STATIC_MATCHED", "token 不进入证据"],
  ["TGW C++ 开发手册", "SubscribeItem", "25–26（17–18）", "pack(1) ctypes", "uint8 market + uint64 flag + char[32] security_code + uint8 category_type；sizeof=42", "STATIC_MATCHED", "STATIC_MATCHED", "公开 flag 与 wire 值分层"],
  ["TGW C++ 开发手册", "ReqKline", "33–34（25–26）", "pack(1) ctypes", "sizeof=71；cyc_type 公共枚举；日期/时间为整数", "LIVE_ALIGNED", "LIVE_ALIGNED(daily + weekly + monthly only)", "季/年/分钟族逐周期验证"],
  ["TGW C++ 开发手册", "ReqDefault", "34（26）/V1.0.8 头文件", "pack(1) ctypes", "sizeof=55；头文件较 PDF 多 level_type:uint16_t=0", "STATIC_MATCHED", "STATIC_MATCHED", "保留 PDF/头文件差异"],
  ["TGW C++ 开发手册", "MDKLine", "60 起", "回调数组", "市场/代码/时间/OHLC/量额/周期；整数缩放", "LIVE_ALIGNED", "LIVE_ALIGNED(daily + weekly + monthly low-level)", "缩放和其它周期时间语义待扩展"],
  ["TGW C++ 开发手册", "MDCodeTable", "36（28）/头文件", "pack(1) callback", "code[16]/symbol[32]/en_name[128]/market u8/type[10]/currency[4]；sizeof=191", "STATIC_MATCHED", "STATIC_MATCHED(static contract only)", "官方同步 wrapper 有首批竞态；wire 未验"],
  ["TGW C++ 开发手册", "SubCodeTableItem", "39（31）/头文件", "pack(1) request", "market:int32（有符号） + security_code[32]；sizeof=36", "LIVE_ALIGNED", "LIVE_ALIGNED(single SSE ETF request)", "仅 101+510300；其它市场另取证"],
  ["TGW C++ 开发手册", "MDETFCodeTableRecord", "85–87（77–79）/头文件", "非 POD callback / wire 两级容器", "35 个固定字段=507 bytes + LP64 std::vector 24 bytes；成分股项 13 字段/sizeof=245", "LIVE_ALIGNED", "LIVE_ALIGNED(single SSE ETF parsed shape)", "不整体 ctypes 化；已验 1×35 + 300×13"],
);

const statusRows = [
  ["INVENTORIED", 10, "已在 PDF/目录登记；尚未证明运行行为", "#E5E7EB"],
  ["STATIC_MATCHED", 25, "PDF、发行头文件、官方对象静态契约已核对", "#DBEAFE"],
  ["LINUX_OBSERVED", 40, "Linux 官方 SDK 最小只读请求已观测", "#BFDBFE"],
  ["WIRE_VERIFIED", 55, "method/字段/枚举/tag/分页与完成语义已取证", "#C7D2FE"],
  ["ARM_IMPLEMENTED", 70, "Mac 已实现，未知分支显式失败，并有协议单测", "#DDD6FE"],
  ["LIVE_ALIGNED", 85, "Linux 与 Mac 同参数、同范围线上对齐", "#BBF7D0"],
  ["PILOT_READY", 100, "另通过资源、超时、重连/恢复和持续观测", "#86EFAC"],
  ["CHANGES_REQUESTED", 45, "数据可能已通，但公开合约/错误语义仍需修复", "#FDE68A"],
  ["BLOCKED", 20, "受权限、服务流控或缺少环境阻塞", "#FED7AA"],
  ["OUT_OF_SCOPE_COLOC", 0, "仅托管模式；当前 internet 项目不实施", "#FECACA"],
  ["NOT_IMPLEMENTED", 0, "公开接口尚未实现", "#FCA5A5"],
  ["SKELETON_ONLY", 5, "仅本地实验骨架，不能表示真实服务可用", "#D1D5DB"],
];

const batches = [
  ["B-20260826-01", "2026-08-26", "Login/Close", "internet 单连接", "只读会话", "成功", "成功", "状态/关闭行为一致", "是", "docs/API_STATUS.md", "LIVE_ALIGNED", "凭据与 token 未记录"],
  ["B-20260826-02", "2026-08-26", "QueryKline", "SSE 单代码单日日线", "盘后查询", "成功；shape 已脱敏", "成功；11 字段容器", "daily 子范围一致", "是", "docs/evidence/query_kline_daily.md", "LIVE_ALIGNED(daily only)", "本轮服务端 1000 限流证据另保留"],
  ["B-20260826-03", "2026-08-26", "QueryThirdInfo", "交易日历单市场窄日期窗", "盘后查询", "成功；嵌套 JSON shape", "成功；list/DataFrame", "功能号子范围一致", "是", "docs/API_STATUS.md", "LIVE_ALIGNED(calendar function only)", "PDF 目录功能号版本差异待复核"],
  ["B-20260826-04", "2026-08-26", "QuerySnapshot", "SZSE ETF L1 单日窄窗口", "盘后查询", "成功；wire/tag 已确认", "数据 shape 同参；错误语义待修", "不得提升整体接口", "是", "docs/evidence/query_snapshot_szse_etf.md", "CHANGES_REQUESTED", "仅 data_type=0 / level_type=0"],
  ["B-20260826-05", "2026-08-26", "QueryKline", "SSE 单代码单周周线", "盘后查询", "成功；1行/11列，wire/tag=10101", "成功；1行/11列，类型一致", "weekly 子范围一致", "是", "docs/evidence/query_kline_week.md", "LIVE_ALIGNED", "仅 cyc_type=10009；未保存行情原值"],
  ["B-20260826-06", "2026-08-26", "QueryKline", "SSE 单代码单月月线", "盘后查询", "成功；1行/11列，wire/tag=10102", "成功；1行/11列，类型一致", "monthly 子范围一致", "是", "docs/evidence/query_kline_month.md", "LIVE_ALIGNED", "仅 cyc_type=10010；未保存行情原值"],
  ["B-20260826-07", "2026-08-26", "QueryCodeTable", "无业务入参静态契约", "静态核对", "未执行", "未实现", "PDF/HDR/Python 六字段契约一致", "否", "docs/evidence/query_code_table_static.md", "STATIC_MATCHED", "同步 wrapper 首批竞态已登记"],
  ["B-20260826-08", "2026-08-26", "QueryETFInfo", "SSE 510300 静态契约", "静态核对", "未执行", "未实现", "请求/嵌套返回契约已锁定", "否", "docs/evidence/query_etf_info_static.md", "STATIC_MATCHED", "含 std::vector，不能整体 ctypes 化"],
  ["B-20260826-09", "2026-08-26", "QueryETFInfo", "SSE 510300 单 ETF 同步查询", "盘后查询", "成功；1条基础35字段+300条成分×13字段", "成功；返回码/行数/列集合/类型一致", "single SSE ETF 子范围一致", "是", "docs/evidence/query_etf_info_sse_etf.md", "LIVE_ALIGNED", "push WSS；tag=字符串111；SZSE/多item/异步未验"],
];

const workbook = Workbook.create();
const summary = workbook.worksheets.add("总览");
const acceptance = workbook.worksheets.add("接口验收");
const functions = workbook.worksheets.add("功能号目录");
const structures = workbook.worksheets.add("数据结构");
const batchSheet = workbook.worksheets.add("测试批次");
const dictionary = workbook.worksheets.add("状态字典");

const navy = "#102A43";
const teal = "#0F766E";
const blue = "#2563EB";
const pale = "#F8FAFC";
const border = "#CBD5E1";
const white = "#FFFFFF";
const amber = "#F59E0B";

function styleTitle(sheet, rangeAddress, title, subtitle, fill = navy) {
  const range = sheet.getRange(rangeAddress);
  range.merge();
  range.values = [[title]];
  range.format = { fill, font: { color: white, bold: true, size: 18 }, verticalAlignment: "center" };
  range.format.rowHeight = 36;
  const endColumn = rangeAddress.split(":")[1].replace(/\d+$/, "");
  const sub = sheet.getRange(`A2:${endColumn}2`);
  sub.merge();
  sub.values = [[subtitle]];
  sub.format = { fill: "#E2E8F0", font: { color: "#334155", italic: true }, wrapText: true, verticalAlignment: "center" };
  sub.format.rowHeight = 32;
}

function styleHeader(range) {
  range.format = {
    fill: teal,
    font: { color: white, bold: true },
    wrapText: true,
    verticalAlignment: "center",
    horizontalAlignment: "center",
    borders: { preset: "all", style: "thin", color: border },
  };
  range.format.rowHeight = 30;
}

function styleBody(range) {
  range.format = {
    fill: white,
    font: { color: "#1E293B", size: 10 },
    wrapText: true,
    verticalAlignment: "top",
    borders: { preset: "all", style: "thin", color: "#E2E8F0" },
  };
}

// Status dictionary first because formulas and validation reference it.
dictionary.showGridLines = false;
styleTitle(dictionary, "A1:D1", "状态字典", "进度百分比由状态自动映射；不得用“请求返回 0”代替同参验收。", navy);
dictionary.getRange("A4:D4").values = [["状态", "完成度%", "进入条件", "建议色"]];
styleHeader(dictionary.getRange("A4:D4"));
dictionary.getRange(`A5:D${4 + statusRows.length}`).values = statusRows;
styleBody(dictionary.getRange(`A5:D${4 + statusRows.length}`));
dictionary.getRange(`B5:B${4 + statusRows.length}`).format.numberFormat = "0\"%\"";
dictionary.getRange("A:A").format.columnWidth = 24;
dictionary.getRange("B:B").format.columnWidth = 12;
dictionary.getRange("C:C").format.columnWidth = 58;
dictionary.getRange("D:D").format.columnWidth = 16;
dictionary.freezePanes.freezeRows(4);

// Main acceptance ledger.
acceptance.showGridLines = false;
styleTitle(acceptance, "A1:Y1", "TGW / AmazingData macOS ARM64 接口验收台账", "范围来自两份 PDF 目录与中央矩阵。盘后优先历史/基础/ThirdInfo 查询；实时订阅只登记，待开盘验证。", navy);
acceptance.getRange("A4:Y4").values = [[
  "序号", "文档来源", "PDF页", "章节/类别", "接口/功能", "底层请求", "模式", "盘后可测", "风险", "当前状态", "完成度%",
  "Linux日期", "Linux结果", "Wire / Tag", "Mac日期", "Mac结果", "对齐结论/范围", "请求构造", "请求字段/类型", "响应结构/类型", "回调/返回合约", "证据", "下一步", "备注", "负责人/批次",
]];
styleHeader(acceptance.getRange("A4:Y4"));
const mainStart = 5;
const mainValues = apiRows.map((row, index) => [
  index + 1, row.source, row.pdfPage, row.category, row.api, row.lowLevel, row.mode, row.postMarket, row.risk, row.status, null,
  row.linuxDate, row.linuxResult, row.wire, row.macDate, row.macResult, row.conclusion, row.construction, row.requestType, row.responseType,
  row.callback, row.evidence, row.next, row.notes, row.batch,
]);
const mainEnd = mainStart + mainValues.length - 1;
acceptance.getRange(`A${mainStart}:Y${mainEnd}`).values = mainValues;
styleBody(acceptance.getRange(`A${mainStart}:Y${mainEnd}`));
acceptance.getRange(`K${mainStart}`).formulas = [[`=IFERROR(VLOOKUP(J${mainStart},'状态字典'!$A$5:$B$16,2,FALSE),0)`]];
acceptance.getRange(`K${mainStart}:K${mainEnd}`).fillDown();
acceptance.getRange(`K${mainStart}:K${mainEnd}`).format.numberFormat = "0\"%\"";
acceptance.getRange(`J${mainStart}:J${mainEnd}`).dataValidation = { rule: { type: "list", formula1: "'状态字典'!$A$5:$A$16" } };
acceptance.getRange(`H${mainStart}:H${mainEnd}`).dataValidation = { rule: { type: "list", values: ["是", "否（待开盘）", "否（模式不适用）", "禁止默认执行", "待取证"] } };
acceptance.getRange(`I${mainStart}:I${mainEnd}`).dataValidation = { rule: { type: "list", values: ["低", "中", "高", "阻塞"] } };
acceptance.getRange(`K${mainStart}:K${mainEnd}`).conditionalFormats.add("dataBar", { color: blue, gradient: true });
acceptance.getRange(`J${mainStart}:J${mainEnd}`).conditionalFormats.add("containsText", { text: "LIVE_ALIGNED", format: { fill: "#BBF7D0", font: { color: "#166534", bold: true } } });
acceptance.getRange(`J${mainStart}:J${mainEnd}`).conditionalFormats.add("containsText", { text: "CHANGES_REQUESTED", format: { fill: "#FDE68A", font: { color: "#92400E", bold: true } } });
acceptance.getRange(`J${mainStart}:J${mainEnd}`).conditionalFormats.add("containsText", { text: "OUT_OF_SCOPE", format: { fill: "#FECACA", font: { color: "#991B1B" } } });
acceptance.getRange(`H${mainStart}:H${mainEnd}`).conditionalFormats.add("containsText", { text: "待开盘", format: { fill: "#FEF3C7", font: { color: "#92400E" } } });
acceptance.freezePanes.freezeRows(4);
acceptance.freezePanes.freezeColumns(5);
acceptance.getRange(`A${mainStart}:Y${mainEnd}`).format.rowHeight = 42;
const widths = [7, 23, 14, 16, 28, 28, 16, 18, 10, 23, 11, 13, 24, 29, 13, 25, 30, 42, 42, 42, 38, 36, 42, 40, 18];
widths.forEach((width, i) => acceptance.getRange(`${String.fromCharCode(65 + i)}:${String.fromCharCode(65 + i)}`).format.columnWidth = width);
acceptance.getRange(`A${mainStart}:A${mainEnd}`).format.horizontalAlignment = "center";

// ThirdInfo function catalogue.
functions.showGridLines = false;
styleTitle(functions, "A1:J1", "TGW ThirdInfo 功能号目录", "直接从 C++ PDF 目录/正文标题提取。每个功能号单独走 Linux → wire → Mac → 同参验收。", navy);
functions.getRange("A4:J4").values = [["章节", "功能名称", "function_id", "PDF页", "盘后可测", "状态", "最小请求构造", "响应结构/类型", "AmazingData映射", "备注"]];
styleHeader(functions.getRange("A4:J4"));
const fnEnd = 4 + functionRows.length;
functions.getRange(`A5:J${fnEnd}`).values = functionRows;
styleBody(functions.getRange(`A5:J${fnEnd}`));
functions.getRange(`F5:F${fnEnd}`).dataValidation = { rule: { type: "list", formula1: "'状态字典'!$A$5:$A$16" } };
functions.getRange(`F5:F${fnEnd}`).conditionalFormats.add("containsText", { text: "LIVE_ALIGNED", format: { fill: "#BBF7D0", font: { color: "#166534", bold: true } } });
functions.freezePanes.freezeRows(4);
functions.getRange(`A5:J${fnEnd}`).format.rowHeight = 34;
[12, 35, 18, 10, 14, 20, 45, 48, 35, 50].forEach((width, i) => functions.getRange(`${String.fromCharCode(65 + i)}:${String.fromCharCode(65 + i)}`).format.columnWidth = width);

// Structures and callbacks.
structures.showGridLines = false;
styleTitle(structures, "A1:H1", "请求、响应与回调结构台账", "类型、位宽、pack、默认值、时间格式和缩放必须逐字段取证；此表先登记已知结构与待办。", navy);
structures.getRange("A4:H4").values = [["来源", "结构", "PDF页", "形态", "字段/类型摘要", "状态", "验证范围", "验收重点/风险"]];
styleHeader(structures.getRange("A4:H4"));
const structEnd = 4 + structureRows.length;
structures.getRange(`A5:H${structEnd}`).values = structureRows;
styleBody(structures.getRange(`A5:H${structEnd}`));
structures.getRange(`F5:F${structEnd}`).dataValidation = { rule: { type: "list", formula1: "'状态字典'!$A$5:$A$16" } };
structures.freezePanes.freezeRows(4);
structures.getRange(`A5:H${structEnd}`).format.rowHeight = 38;
[24, 24, 18, 22, 68, 22, 34, 58].forEach((width, i) => structures.getRange(`${String.fromCharCode(65 + i)}:${String.fromCharCode(65 + i)}`).format.columnWidth = width);

// Execution log template and completed evidence batches.
batchSheet.showGridLines = false;
styleTitle(batchSheet, "A1:L1", "测试批次与差分结果", "只记录脱敏 shape、类型、不变量和控制字段；禁止账号、密码、token、MAC、原始价格或完整响应。", navy);
batchSheet.getRange("A4:L4").values = [["批次", "日期", "接口", "同参范围", "测试类型", "Linux摘要", "Mac摘要", "差分结论", "清理完成", "证据", "拟议状态", "备注"]];
styleHeader(batchSheet.getRange("A4:L4"));
const batchDataEnd = 4 + batches.length;
const batchDivider = batchDataEnd + 1;
const batchBlankStart = batchDivider + 1;
const batchBlankEnd = batchBlankStart + 8;
batchSheet.getRange(`A5:L${batchDataEnd}`).values = batches;
styleBody(batchSheet.getRange(`A5:L${batchDataEnd}`));
batchSheet.getRange(`A${batchBlankStart}:L${batchBlankEnd}`).values = Array.from({ length: 9 }, () => Array(12).fill(""));
styleBody(batchSheet.getRange(`A${batchBlankStart}:L${batchBlankEnd}`));
batchSheet.getRange(`A${batchDivider}:L${batchDivider}`).merge();
batchSheet.getRange(`A${batchDivider}:L${batchDivider}`).values = [["后续 Agent 每次追加一行；一项接口/一个明确子范围/一个批次"]];
batchSheet.getRange(`A${batchDivider}:L${batchDivider}`).format = { fill: "#DBEAFE", font: { color: "#1E40AF", bold: true } };
batchSheet.getRange(`I5:I${batchBlankEnd}`).dataValidation = { rule: { type: "list", values: ["是", "否"] } };
batchSheet.getRange(`K5:K${batchBlankEnd}`).dataValidation = { rule: { type: "list", formula1: "'状态字典'!$A$5:$A$16" } };
batchSheet.freezePanes.freezeRows(4);
batchSheet.getRange(`A5:L${batchBlankEnd}`).format.rowHeight = 38;
[18, 13, 24, 30, 18, 35, 35, 38, 14, 38, 25, 50].forEach((width, i) => batchSheet.getRange(`${String.fromCharCode(65 + i)}:${String.fromCharCode(65 + i)}`).format.columnWidth = width);

// Dashboard formulas reference bounded ranges in the acceptance ledger.
summary.showGridLines = false;
styleTitle(summary, "A1:L1", "macOS ARM64 适配验收总览", "盘后工作台 · 由接口台账公式汇总 · 更新时间 2026-08-26", navy);
summary.getRange("A4:B4").values = [["指标", "值"]];
styleHeader(summary.getRange("A4:B4"));
summary.getRange("A5:A12").values = [["接口总数"], ["盘后可测"], ["LIVE_ALIGNED"], ["ARM_IMPLEMENTED"], ["WIRE_VERIFIED"], ["需返工"], ["仅托管/不适用"], ["平均完成度"]];
summary.getRange("B5:B12").formulas = [
  [`=COUNTA('接口验收'!$E$${mainStart}:$E$${mainEnd})`],
  [`=COUNTIF('接口验收'!$H$${mainStart}:$H$${mainEnd},"是")`],
  [`=COUNTIF('接口验收'!$J$${mainStart}:$J$${mainEnd},"LIVE_ALIGNED")`],
  [`=COUNTIF('接口验收'!$J$${mainStart}:$J$${mainEnd},"ARM_IMPLEMENTED")`],
  [`=COUNTIF('接口验收'!$J$${mainStart}:$J$${mainEnd},"WIRE_VERIFIED")`],
  [`=COUNTIF('接口验收'!$J$${mainStart}:$J$${mainEnd},"CHANGES_REQUESTED")`],
  [`=COUNTIF('接口验收'!$J$${mainStart}:$J$${mainEnd},"OUT_OF_SCOPE_COLOC")`],
  [`=AVERAGE('接口验收'!$K$${mainStart}:$K$${mainEnd})`],
];
styleBody(summary.getRange("A5:B12"));
summary.getRange("B12").format.numberFormat = "0.0\"%\"";
summary.getRange("D4:H4").merge();
summary.getRange("D4:H4").values = [["盘后优先队列"]];
summary.getRange("D4:H4").format = { fill: blue, font: { color: white, bold: true }, horizontalAlignment: "center" };
summary.getRange("D5:H5").values = [["优先级", "接口", "子范围", "当前状态", "下一步"]];
styleHeader(summary.getRange("D5:H5"));
summary.getRange("D6:H13").values = [
  [1, "QuerySnapshot", "SZSE ETF L1 历史", "CHANGES_REQUESTED", "补错误码/空数据/异步语义"],
  [2, "QueryCodeTable", "全市场完整分包", "STATIC_MATCHED", "异步 SPI 累计 shape + wire"],
  [3, "QueryKline", "季线单周期", "INVENTORIED", "Linux wire → Mac 映射 → 同参复验"],
  [4, "QueryETFInfo", "SZSE 单 ETF", "INVENTORIED", "独立市场 oracle + wire → Mac"],
  [5, "get_code_info", "单证券类型", "INVENTORIED", "核对 wrapper 默认值与列类型"],
  [6, "get_code_list", "沪深北单类型", "INVENTORIED", "核对列表顺序/空结果"],
  [7, "get_stock_basic", "单代码/单日", "INVENTORIED", "映射 ThirdInfo 功能号"],
  [8, "财务接口", "单接口/单报告期", "INVENTORIED", "逐字段/分页/空值验收"],
];
styleBody(summary.getRange("D6:H13"));
summary.getRange("A15:L15").merge();
summary.getRange("A15:L15").values = [["判定规则：只有 Linux 官方 SDK 与 Mac 使用完全相同参数，且返回码、行/包数、列集合、Python 类型、不变量、完成/关闭语义一致，才进入 LIVE_ALIGNED。"]];
summary.getRange("A15:L15").format = { fill: "#ECFDF5", font: { color: "#065F46", bold: true }, wrapText: true };
summary.getRange("A17:L17").merge();
summary.getRange("A17:L17").values = [["盘后限制：实时订阅与持续回调项统一标记“否（待开盘）”；不得用无推送代替失败结论。写操作和仅托管接口不进入默认测试队列。"]];
summary.getRange("A17:L17").format = { fill: "#FFF7ED", font: { color: "#9A3412", bold: true }, wrapText: true };
summary.getRange("A:A").format.columnWidth = 24;
summary.getRange("B:B").format.columnWidth = 18;
summary.getRange("C:C").format.columnWidth = 4;
summary.getRange("D:D").format.columnWidth = 10;
summary.getRange("E:E").format.columnWidth = 24;
summary.getRange("F:F").format.columnWidth = 25;
summary.getRange("G:G").format.columnWidth = 24;
summary.getRange("H:H").format.columnWidth = 46;
summary.getRange("I:L").format.columnWidth = 12;
summary.freezePanes.freezeRows(2);

await fs.mkdir(outputDir, { recursive: true });

const inspectSummary = await workbook.inspect({ kind: "workbook,sheet,formula", maxChars: 12000, tableMaxRows: 8, tableMaxCols: 12, options: { maxResults: 200 } });
await fs.writeFile(path.join(outputDir, "inspect_summary.ndjson"), inspectSummary.ndjson ?? JSON.stringify(inspectSummary, null, 2));

const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 200 },
  maxChars: 12000,
});
await fs.writeFile(path.join(outputDir, "formula_errors.ndjson"), formulaErrors.ndjson ?? JSON.stringify(formulaErrors, null, 2));

for (const [sheetName, scale] of [["总览", 1], ["接口验收", 0.5], ["功能号目录", 0.65], ["数据结构", 0.75], ["测试批次", 0.75], ["状态字典", 1]]) {
  const preview = await workbook.render({ sheetName, autoCrop: "all", scale, format: "png" });
  const safeName = sheetName.replace(/[\\/:*?"<>|]/g, "_");
  await fs.writeFile(path.join(outputDir, `preview_${safeName}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputFile);
console.log(JSON.stringify({ outputFile, apiCount: apiRows.length, functionCount: functionRows.length, structureCount: structureRows.length, mainEnd }));
