#include "modules/premium/engine/common/MarketTypes.h"
#include "modules/redemption/RedemptionCore.h"
#include "models/PullMonitorModels.h"
#include "models/EtfMonitorModels.h"
#include <QCoreApplication>
#include <QFile>
#include <QJsonDocument>
#include <QTextStream>
#include <cstdlib>

static int checks = 0;
static void check(bool ok, const char *label) {
    QTextStream(stdout) << (ok ? "PASS " : "FAIL ") << label << '\n';
    if (!ok) std::exit(1);
    ++checks;
}
static QJsonObject wire(const QJsonObject &value) {
    return QJsonDocument::fromJson(QJsonDocument(value).toJson(QJsonDocument::Compact)).object();
}
int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    using namespace qmtui;
    using machome::redemption::RedemptionCore;
    namespace premium = machome::premium::engine;
    QString error;
    premium::QuoteSnapshot source;
    source.symbol = "159518.SZ"; source.sourceSession = "offline-audit";
    source.origTime = 20260907113507123LL;
    source.receiveWallNs = 1788752107123456789LL;
    source.lastPriceE6 = 1234567; source.bidPricesE6[0] = 1230000;
    source.bidVolumesE2[0] = 12340000; source.iopvE6 = 1200000;
    source.sellPremiumPpm = 25000; source.displayPremiumPpm = 28806;
    source.bidRise150sPpm = 12345; source.adaptive5mPpm = 8000;
    source.sourceReady = true; source.numericMappingVerified = true;
    PullMonitorSnapshot summary;
    check(pullMonitorSnapshotFromJson(wire(source.toSummaryJson()), &summary, &error), "V31 parses R4 summary serializer");
    check(summary.symbol == source.symbol && summary.origTime == source.origTime
        && summary.receiveWallNs == source.receiveWallNs, "symbol and int64 timestamps survive JSON wire");
    check(summary.lastPriceE6 == 1234567 && summary.sellPremiumPpm == 25000
        && summary.displayPremiumPpm == 28806 && summary.bidRise150sPpm == 12345
        && summary.adaptive5mPpm == 8000, "E6/PPM and momentum fields agree");
    PullMonitorDetail detail;
    check(pullMonitorDetailFromJson(wire(source.toDetailJson()), &detail, &error)
        && detail.bidVolumesE2.size() == 10 && detail.bidVolumesE2[0] == 12340000,
        "V31 parses R4 ten-level E2 book");
    premium::SignalEvent signal;
    signal.symbol = source.symbol; signal.sequence = 17;
    signal.occurredAt = QDateTime::fromString("2026-09-07T11:35:07.123+08:00", Qt::ISODateWithMs);
    signal.model = "premium+pull+radar"; signal.premiumPpm = 25000; signal.replay = true;
    PullMonitorSignal received;
    check(pullMonitorSignalFromJson(wire(signal.toJson(true)), &received, &error)
        && received.sequence == 17 && received.backfill && received.replay,
        "V31 signal sequence/backfill/replay survive R4 serializer");

    check(argc == 2, "snapshot file supplied");
    QFile fixture(QString::fromLocal8Bit(argv[1]));
    check(fixture.open(QIODevice::ReadOnly), "open saved R4 inspection snapshot (no network)");
    const auto raw = QJsonDocument::fromJson(fixture.readAll()).object();
    EtfSnapshotEvent snapshot;
    check(parseEtfSnapshotEvent(raw, &snapshot, &error) && snapshot.items.size() == 7,
        "V12 parses all seven ETF rows from saved R4 snapshot");
    int pcfs = 0;
    for (const auto &entry : raw.value("items").toArray()) {
        const auto pcf = entry.toObject().value("pcf").toObject();
        if (pcf.isEmpty()) continue;
        EtfPcfDetail parsed;
        check(parseEtfPcfDetail(wire(pcf), &parsed, &error)
            && parsed.hasCreationRedemptionUnit && parsed.creationRedemptionUnit > 0,
            "V12 parses actual R4 PCF detail and basket unit");
        ++pcfs;
    }
    check(pcfs == 7, "all seven saved PCF payloads checked");
    auto item = raw.value("items").toArray().first().toObject();
    const QJsonObject previous{{"etfbuyamount", 0}, {"etfsellamount", 0}, {"netamount", 0}};
    const QJsonObject current{{"etfbuyamount", 500000}, {"etfsellamount", 0}, {"netamount", 500000}};
    const auto changes = RedemptionCore::changeDetails(previous, current);
    item.insert("values", current); item.insert("last_change", changes);
    QJsonObject event{{"type", "change"}, {"protocol", 1}, {"replay", false},
        {"items", QJsonArray{QJsonObject{{"symbol", item.value("symbol")},
            {"windcode", item.value("windcode")}, {"changes", changes}, {"current", item}}}}};
    EtfChangeEvent changed;
    check(parseEtfChangeEvent(wire(event), &changed, &error)
        && changed.items.first().current.values.buyShares == 500000
        && changed.items.first().changes.first().field == "etfbuyamount",
        "V12 parses R4 change envelope with real server changeDetails");
    const auto liveText = formatEtfChangeSummary(changed);
    event.insert("replay", true);
    EtfChangeEvent replayed;
    check(parseEtfChangeEvent(wire(event), &replayed, &error)
        && formatEtfChangeSummary(replayed) == liveText && !liveText.isEmpty(),
        "CONFIRMED GAP: V12 replay flag lost; alert text identical to live change");
    QJsonObject history{{"type", "history"}, {"protocol", 1}, {"date", "2026-09-07"},
        {"items", QJsonArray{QJsonObject{{"symbol", item.value("symbol")},
            {"previous", previous}, {"current", current}, {"changes", changes}}}}};
    EtfHistoryResponse parsedHistory;
    check(parseEtfHistoryResponse(wire(history), &parsedHistory, &error)
        && parsedHistory.items.first().current.buyShares == 500000,
        "V12 accepts R4 history shape without optional count/limit/symbol fields");
    QTextStream(stdout) << "Completed " << checks << " checks. Pure QtCore; no business connections or orders.\n";
}
