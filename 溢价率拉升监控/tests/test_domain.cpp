#include "common/BridgeFrame.h"
#include "common/MarketSchedule.h"
#include "common/SignalEngine.h"
#include "common/SnapshotParser.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QtTest>

using namespace premium;

namespace {

QJsonArray repeated(qint64 start, qint64 step = 0)
{
    QJsonArray result;
    for (int i = 0; i < 10; ++i) result.append(start + i * step);
    return result;
}

BridgeFrame frameFor(const QString &session, bool delta, const QJsonObject &data)
{
    BridgeFrame frame;
    frame.kind = BridgeFrame::Kind::MarketEvent;
    frame.sequence = 1;
    frame.sessionId = session;
    frame.receiveWallNs = 1'000'000'000;
    frame.isDelta = delta;
    frame.tag = QStringLiteral("14");
    frame.payloadJson = QJsonDocument(QJsonObject{{"headers", QJsonObject{{"tag", "14"}}}, {"is_delta", delta}, {"data", data}}).toJson(QJsonDocument::Compact);
    return frame;
}

BridgeFrame hktFrameFor(const QString &session, bool delta, const QJsonObject &data)
{
    BridgeFrame frame = frameFor(session, delta, data);
    frame.tag = QStringLiteral("16");
    frame.payloadJson = QJsonDocument(QJsonObject{{"headers", QJsonObject{{"tag", "16"}}},
                                                   {"is_delta", delta}, {"data", data}})
                            .toJson(QJsonDocument::Compact);
    return frame;
}

QJsonObject hktFullData()
{
    return {{"1", 102}, {"2", "02800"}, {"3", 20'260'827'154'936'000LL},
            {"4", QString::fromLatin1("T0\0\0\0\0\0", 7)}, {"5", 74'786'891'300LL},
            {"6", 1'952'183'035'644'000LL}, {"7", 26'140'000}, {"8", 26'080'000},
            {"9", 26'300'000}, {"10", 26'040'000}, {"11", 26'080'000},
            {"12", "26080000|0|0|0|0"}, {"13", "1617100000|0|0|0|0"},
            {"14", "26100000|0|0|0|0"}, {"15", "938450000|0|0|0|0"},
            {"16", 0}, {"17", 0}, {"18", 0}, {"19", 0}, {"20", 0}, {"21", 0},
            {"22", 0}, {"23", 6}};
}

QJsonObject fullData(const QString &symbol = QStringLiteral("159518.SZ"))
{
    return {{"security_code", symbol}, {"market_type", 102}, {"orig_time", 1'784'941'200'123LL},
            {"last_price", 1'000'000}, {"open_price", 990'000}, {"high_price", 1'010'000},
            {"low_price", 980'000}, {"close_price", 0}, {"pre_close_price", 990'000},
            {"bid_price", repeated(1'000'000, -1'000)}, {"offer_price", repeated(1'001'000, 1'000)},
            {"bid_volume", repeated(100'000)}, {"offer_volume", repeated(110'000)},
            {"total_volume_trade", 1'000'000}, {"total_value_trade", 10'000'000},
            {"num_trades", 100}, {"trading_phase_code", "T"}, {"IOPV", 1'000'000},
            {"high_limited", 1'100'000}, {"low_limited", 900'000}};
}

QJsonObject liveNumericFullData()
{
    return {{"1", 102}, {"2", "159866"}, {"3", 2}, {"4", 20'260'827'095'409'000LL},
            {"5", QString::fromLatin1("T0\0\0\0\0\0", 7)}, {"6", 1'680'000},
            {"7", 1'675'000}, {"8", 1'689'000}, {"9", 1'674'000}, {"10", 1'684'000},
            {"11", 0},
            {"12", "1684000|1683000|1682000|1681000|1680000|0|0|0|0|0"},
            {"13", "75300000|62120000|60810000|77300000|58570000|0|0|0|0|0"},
            {"14", "1685000|1686000|1687000|1688000|1689000|0|0|0|0|0"},
            {"15", "76430000|77910000|50810000|56290000|68430000|0|0|0|0|0"},
            {"16", 2'759}, {"17", 3'897'510'000LL}, {"18", 6'556'934'250'000LL},
            {"19", 1'634'000}, {"20", 1'848'000}, {"21", 1'512'000}};
}

QuoteSnapshot quote(qint64 bid, qint64 iopv = 1'000'000)
{
    QuoteSnapshot value;
    value.symbol = QStringLiteral("159518.SZ");
    value.sourceReady = true;
    value.lastPriceE6 = bid;
    value.preClosePriceE6 = iopv;
    value.bidPricesE6[0] = bid;
    value.iopvE6 = iopv;
    return value;
}

} // namespace

class DomainTests final : public QObject {
    Q_OBJECT
private Q_SLOTS:
    void protobufRoundTrip()
    {
        BridgeFrame input;
        input.kind = BridgeFrame::Kind::MarketEvent;
        input.sequence = 42;
        input.sessionId = QStringLiteral("session-a");
        input.receiveWallNs = 123456789;
        input.isDelta = true;
        input.tag = QStringLiteral("14");
        input.payloadJson = QByteArrayLiteral("{\"data\":{}}");
        QByteArray buffer = input.encodeLengthPrefixed();
        const auto frames = takeLengthPrefixedFrames(buffer);
        QCOMPARE(frames.size(), 1);
        QVERIFY(buffer.isEmpty());
        BridgeFrame output;
        QString error;
        QVERIFY2(BridgeFrame::decodeProtobuf(frames.front(), &output, &error), qPrintable(error));
        QCOMPARE(output.sequence, input.sequence);
        QCOMPARE(output.payloadJson, input.payloadJson);
        QCOMPARE(output.isDelta, true);
    }

    void parserMergesAndIsolates()
    {
        SnapshotParser parser;
        auto first = parser.consume(frameFor(QStringLiteral("s1"), false, fullData()));
        QVERIFY(first.snapshot.has_value());
        QVERIFY(first.issues.isEmpty());
        QCOMPARE(first.snapshot->iopvE6, 1'000'000);
        QJsonObject delta{{"security_code", "159518.SZ"}, {"last_price", 1'020'000}, {"bid_price", repeated(1'020'000, -1'000)}};
        auto merged = parser.consume(frameFor(QStringLiteral("s1"), true, delta));
        QVERIFY(merged.snapshot.has_value());
        QCOMPARE(merged.snapshot->lastPriceE6, 1'020'000);
        QCOMPARE(merged.snapshot->iopvE6, 1'000'000);
        auto other = parser.consume(frameFor(QStringLiteral("s1"), false, fullData(QStringLiteral("513520.SH"))));
        QVERIFY(other.snapshot.has_value());
        QCOMPARE(parser.readySymbolCount(), 2);
    }

    void deltaWaitsForFullAfterReconnect()
    {
        SnapshotParser parser;
        parser.consume(frameFor(QStringLiteral("old"), false, fullData()));
        QJsonObject delta{{"security_code", "159518.SZ"}, {"last_price", 1'020'000}};
        auto result = parser.consume(frameFor(QStringLiteral("new"), true, delta));
        QVERIFY(!result.snapshot.has_value());
        QVERIFY(result.waitingForFull);
        QVERIFY(result.issues.contains(QStringLiteral("delta_before_full")));
    }

    void removedSymbolMustReceiveANewFull()
    {
        SnapshotParser parser;
        QVERIFY(parser.consume(frameFor(QStringLiteral("s1"), false, fullData())).snapshot.has_value());
        parser.resetSymbol(QStringLiteral("159518.SZ"));
        QCOMPARE(parser.readySymbolCount(), 0);
        QJsonObject delta{{"security_code", "159518.SZ"}, {"last_price", 1'020'000}};
        const auto afterRemoval = parser.consume(frameFor(QStringLiteral("s1"), true, delta));
        QVERIFY(!afterRemoval.snapshot.has_value());
        QVERIFY(afterRemoval.waitingForFull);
    }

    void wrongTypeIsQuarantined()
    {
        QJsonObject data = fullData();
        data.insert(QStringLiteral("IOPV"), QStringLiteral("1000000"));
        SnapshotParser parser;
        auto result = parser.consume(frameFor(QStringLiteral("s1"), false, data));
        QVERIFY(result.snapshot.has_value());
        QVERIFY(result.snapshot->qualityIssues.contains(QStringLiteral("IOPV_not_int64")));
        QVERIFY(result.snapshot->qualityIssues.contains(QStringLiteral("iopv_non_positive")));
    }

    void seventeenDigitExchangeTimeIsExact()
    {
        QJsonObject data = fullData();
        data.insert(QStringLiteral("orig_time"), 20'260'827'093'100'123LL);
        SnapshotParser parser;
        const auto result = parser.consume(frameFor(QStringLiteral("s1"), false, data));
        QVERIFY(result.snapshot.has_value());
        QCOMPARE(result.snapshot->origTime, 20'260'827'093'100'123LL);
    }

    void liveNumericFullSnapshotMapsExactly()
    {
        SnapshotParser parser;
        const auto result = parser.consume(frameFor(QStringLiteral("live-single"), false, liveNumericFullData()));
        QVERIFY(result.snapshot.has_value());
        const QuoteSnapshot &snapshot = *result.snapshot;
        QCOMPARE(snapshot.symbol, QStringLiteral("159866.SZ"));
        QCOMPARE(snapshot.market, QStringLiteral("SZ"));
        QCOMPARE(snapshot.origTime, 20'260'827'095'409'000LL);
        QCOMPARE(snapshot.tradingPhase, QStringLiteral("T0"));
        QCOMPARE(snapshot.preClosePriceE6, 1'680'000);
        QCOMPARE(snapshot.openPriceE6, 1'675'000);
        QCOMPARE(snapshot.highPriceE6, 1'689'000);
        QCOMPARE(snapshot.lowPriceE6, 1'674'000);
        QCOMPARE(snapshot.lastPriceE6, 1'684'000);
        QCOMPARE(snapshot.bidPricesE6[0], 1'684'000);
        QCOMPARE(snapshot.bidPricesE6[4], 1'680'000);
        QCOMPARE(snapshot.bidVolumesE2[0], 75'300'000);
        QCOMPARE(snapshot.askPricesE6[0], 1'685'000);
        QCOMPARE(snapshot.askPricesE6[4], 1'689'000);
        QCOMPARE(snapshot.askVolumesE2[1], 77'910'000);
        QCOMPARE(snapshot.numTrades, 2'759);
        QCOMPARE(snapshot.totalVolumeE2, 3'897'510'000LL);
        QCOMPARE(snapshot.totalAmountE5, 6'556'934'250'000LL);
        QCOMPARE(snapshot.iopvE6, 1'634'000);
        QCOMPARE(snapshot.highLimitE6, 1'848'000);
        QCOMPARE(snapshot.lowLimitE6, 1'512'000);
        QCOMPARE(snapshot.mappingVersion, QStringLiteral("numeric-live-20260827-UNVERIFIED"));
        QVERIFY(!snapshot.numericMappingVerified);
        QVERIFY2(snapshot.qualityIssues.isEmpty(), qPrintable(snapshot.qualityIssues.join(u',')));
    }

    void hktDeepConnectMapsFullDeltaAndLegacyV1()
    {
        SnapshotParser parser;
        const auto full = parser.consume(hktFrameFor(QStringLiteral("hkt-live"), false, hktFullData()));
        QVERIFY(full.snapshot.has_value());
        const QuoteSnapshot &snapshot = *full.snapshot;
        QCOMPARE(snapshot.symbol, QStringLiteral("02800.HK"));
        QCOMPARE(snapshot.code, QStringLiteral("02800"));
        QCOMPARE(snapshot.market, QStringLiteral("HK"));
        QCOMPARE(snapshot.origTime, 20'260'827'154'936'000LL);
        QCOMPARE(snapshot.tradingPhase, QStringLiteral("T0"));
        QCOMPARE(snapshot.preClosePriceE6, 26'140'000);
        QCOMPARE(snapshot.nominalPriceE6, 26'080'000);
        QCOMPARE(snapshot.lastPriceE6, 26'080'000);
        QCOMPARE(snapshot.openPriceE6, 0);
        QCOMPARE(snapshot.levelCount, 5);
        QCOMPARE(snapshot.bidPricesE6[0], 26'080'000);
        QCOMPARE(snapshot.bidPricesE6[5], 0);
        QCOMPARE(snapshot.askVolumesE2[0], 938'450'000);
        QVERIFY(snapshot.numericMappingVerified);
        QVERIFY(snapshot.qualityIssues.contains(QStringLiteral("iopv_unavailable_hkt")));
        const QJsonObject legacy = snapshot.toLegacyBookJson();
        QCOMPARE(legacy.value(QStringLiteral("s")).toString(), QStringLiteral("02800.HK"));
        QCOMPARE(legacy.value(QStringLiteral("lp")).toDouble(), 26.08);
        QCOMPARE(legacy.value(QStringLiteral("o")).toDouble(), 0.0);
        QCOMPARE(legacy.value(QStringLiteral("bp")).toArray().size(), 5);

        const QJsonObject backwards{{"2", "02800"}, {"3", 20'260'827'154'935'000LL},
                                    {"11", 1'000}};
        const auto rejectedBackwards = parser.consume(
            hktFrameFor(QStringLiteral("hkt-live"), true, backwards));
        QVERIFY(!rejectedBackwards.snapshot.has_value());
        QVERIFY(rejectedBackwards.issues.contains(QStringLiteral("hkt_orig_time_backwards")));

        const QJsonObject volumeBackwards{{"2", "02800"}, {"3", 20'260'827'154'937'000LL},
                                          {"5", 1}};
        const auto rejectedVolume = parser.consume(
            hktFrameFor(QStringLiteral("hkt-live"), true, volumeBackwards));
        QVERIFY(!rejectedVolume.snapshot.has_value());
        QVERIFY(rejectedVolume.issues.contains(QStringLiteral("hkt_total_volume_backwards")));

        const QJsonObject malformedDelta{{"2", "02800"}, {"3", 20'260'827'154'938'000LL},
                                         {"12", "26090000|0|0|0"}};
        const auto rejectedMalformed = parser.consume(
            hktFrameFor(QStringLiteral("hkt-live"), true, malformedDelta));
        QVERIFY(!rejectedMalformed.snapshot.has_value());
        QVERIFY(rejectedMalformed.issues.contains(QStringLiteral("bid_price_length_4")));

        const QJsonObject delta{{"2", "02800"}, {"3", 20'260'827'155'001'000LL},
                                {"5", 74'787'091'300LL}, {"11", 26'090'000},
                                {"12", "26090000|0|0|0|0"},
                                {"13", "1315500000|0|0|0|0"}};
        const auto merged = parser.consume(hktFrameFor(QStringLiteral("hkt-live"), true, delta));
        QVERIFY(merged.snapshot.has_value());
        QCOMPARE(merged.snapshot->lastPriceE6, 26'090'000);
        QCOMPARE(merged.snapshot->preClosePriceE6, 26'140'000);
        QCOMPARE(merged.snapshot->bidVolumesE2[0], 1'315'500'000);
    }

    void hktRejectsWrongRouteCodeAndMalformedBook()
    {
        SnapshotParser parser;
        QJsonObject wrongRoute = hktFullData();
        wrongRoute.insert(QStringLiteral("1"), 101);
        auto result = parser.consume(hktFrameFor(QStringLiteral("bad-route"), false, wrongRoute));
        QVERIFY(!result.snapshot.has_value());
        QVERIFY(result.issues.contains(QStringLiteral("hkt_route_market_not_szse")));

        QJsonObject wrongCode = hktFullData();
        wrongCode.insert(QStringLiteral("2"), QStringLiteral("2800"));
        result = parser.consume(hktFrameFor(QStringLiteral("bad-code"), false, wrongCode));
        QVERIFY(!result.snapshot.has_value());
        QVERIFY(result.issues.contains(QStringLiteral("hkt_code_not_five_digits")));

        QJsonObject wrongBook = hktFullData();
        wrongBook.insert(QStringLiteral("12"), QStringLiteral("26080000|0|0|0"));
        result = parser.consume(hktFrameFor(QStringLiteral("bad-book"), false, wrongBook));
        QVERIFY(!result.snapshot.has_value());
        QVERIFY(result.issues.contains(QStringLiteral("bid_price_length_4")));
        const auto deltaAfterInvalidFull = parser.consume(
            hktFrameFor(QStringLiteral("bad-book"), true,
                        QJsonObject{{"2", "02800"}, {"3", 20'260'827'154'937'000LL},
                                    {"11", 26'090'000}}));
        QVERIFY(!deltaAfterInvalidFull.snapshot.has_value());
        QVERIFY(deltaAfterInvalidFull.waitingForFull);

        QJsonObject wrongVariety = hktFullData();
        wrongVariety.insert(QStringLiteral("23"), QStringLiteral("six"));
        result = parser.consume(hktFrameFor(QStringLiteral("bad-variety"), false, wrongVariety));
        QVERIFY(!result.snapshot.has_value());
        QVERIFY(result.issues.contains(QStringLiteral("variety_category_not_int64")));
    }

    void lofWithoutIopvStillProducesSnapshotButNeverSignals()
    {
        QJsonObject data = liveNumericFullData();
        data.insert(QStringLiteral("2"), QStringLiteral("164824"));
        data.insert(QStringLiteral("19"), 0);
        SnapshotParser parser;
        const auto parsed = parser.consume(frameFor(QStringLiteral("live-lof"), false, data));
        QVERIFY(parsed.snapshot.has_value());
        QCOMPARE(parsed.snapshot->symbol, QStringLiteral("164824.SZ"));
        QVERIFY(parsed.snapshot->qualityIssues.contains(QStringLiteral("iopv_non_positive")));

        SignalEngine engine;
        const QDateTime now(QDate(2026, 8, 27), QTime(10, 3), QTimeZone::systemTimeZone());
        const auto decision = engine.evaluate(*parsed.snapshot, now, true, true);
        QVERIFY(!decision.event.has_value());
        QCOMPARE(decision.snapshot.lastPriceE6, 1'684'000);
        QCOMPARE(decision.snapshot.iopvE6, 0);
    }

    void fractionalNumberIsNotAcceptedAsInteger()
    {
        QJsonObject data = fullData();
        data.insert(QStringLiteral("IOPV"), 1'000'000.5);
        SnapshotParser parser;
        const auto result = parser.consume(frameFor(QStringLiteral("s1"), false, data));
        QVERIFY(result.snapshot.has_value());
        QVERIFY(result.snapshot->qualityIssues.contains(QStringLiteral("IOPV_not_int64")));
    }

    void offTickExchangePriceIsQuarantinedWithoutUsingFloatingPoint()
    {
        QJsonObject data = fullData();
        data.insert(QStringLiteral("last_price"), 959'100);
        data.insert(QStringLiteral("bid_price"), repeated(959'100, -1'000));
        SnapshotParser parser;
        const auto result = parser.consume(frameFor(QStringLiteral("s1"), false, data));
        QVERIFY(result.snapshot.has_value());
        QCOMPARE(result.snapshot->lastPriceE6, 959'100);
        QVERIFY(result.snapshot->qualityIssues.contains(QStringLiteral("price_tick_0_001_invalid")));
    }

    void unknownFieldIsQuarantined()
    {
        QJsonObject data = fullData();
        data.insert(QStringLiteral("999"), 1);
        SnapshotParser parser;
        auto result = parser.consume(frameFor(QStringLiteral("s1"), false, data));
        QVERIFY(result.snapshot.has_value());
        QVERIFY(result.snapshot->qualityIssues.contains(QStringLiteral("unknown_field:999")));
    }

    void fixedPointAndSignalBoundary()
    {
        QCOMPARE(calculateRatioPpm(1'015'000, 1'000'000), 15'000);
        QCOMPARE(calculateRatioPpm(1'017'001, 1'000'000), 17'001);
        SignalEngine engine;
        const QDateTime base(QDate(2026, 8, 27), QTime(9, 31), QTimeZone::systemTimeZone());
        QVERIFY(!engine.evaluate(quote(1'014'000), base, true, false).event.has_value());
        QVERIFY(!engine.evaluate(quote(1'015'000), base.addSecs(1), true, false).event.has_value());
        auto triggered = engine.evaluate(quote(1'017'000), base.addSecs(2), true, false);
        QVERIFY(triggered.event.has_value());
        QCOMPARE(triggered.event->reason, QStringLiteral("30s"));
        QVERIFY(!engine.evaluate(quote(1'018'000), base.addSecs(3), true, false).event.has_value());
        QVERIFY(engine.evaluate(quote(1'019'000), base.addSecs(4), true, false).event.has_value());
        QCOMPARE(exchangeTimeToEpochMs(1'784'941'200'123LL), 1'784'941'200'123LL);
        QVERIFY(exchangeTimeToEpochMs(20'260'827'093'100'123LL) > 0);
    }

    void staticIopvStillCalculates()
    {
        SignalEngine engine;
        const QDateTime base(QDate(2026, 8, 27), QTime(9, 31), QTimeZone::systemTimeZone());
        engine.evaluate(quote(1'000'000), base, true, true);
        for (int i = 1; i <= 4; ++i) engine.evaluate(quote(1'000'000), base.addSecs(i), true, true);
        auto result = engine.evaluate(quote(1'020'000), base.addSecs(121), true, true);
        QVERIFY(result.snapshot.iopvStatic);
        QVERIFY(result.event.has_value());
    }

    void fiveMinuteRuleAndThirtySecondReset()
    {
        SignalEngine engine;
        const QDateTime base(QDate(2026, 8, 27), QTime(9, 35), QTimeZone::systemTimeZone());
        QVERIFY(!engine.evaluate(quote(1'000'000), base, false, true).event.has_value());
        auto fiveMinute = engine.evaluate(quote(1'016'000), base.addSecs(300), false, true);
        QVERIFY(fiveMinute.event.has_value());
        QVERIFY(fiveMinute.event->reason.contains(QStringLiteral("5m")));

        QVERIFY(!engine.evaluate(quote(1'012'999), base.addSecs(301), true, true).event.has_value());
        QVERIFY(!engine.evaluate(quote(1'012'999), base.addSecs(331), true, true).event.has_value());
        auto afterReset = engine.evaluate(quote(1'017'000), base.addSecs(332), true, true);
        QVERIFY(afterReset.event.has_value());
        QVERIFY(!afterReset.event->repeat);
    }

    void pullModelWithIopvRequiresPremiumGateAndStrict150Threshold()
    {
        SignalEngine engine;
        const QDateTime base(QDate(2026, 8, 27), QTime(9, 30), QTimeZone::systemTimeZone());
        QVERIFY(!engine.evaluate(quote(995'000), base, true, true).event.has_value());
        auto gated = engine.evaluate(quote(1'004'100), base.addSecs(150), true, true);
        QVERIFY(!gated.event.has_value());
        QCOMPARE(gated.snapshot.bidRise150sPpm, calculateRatioPpm(1'004'100, 995'000));

        engine.resetAll();
        QVERIFY(!engine.evaluate(quote(1'002'000), base, true, true).event.has_value());
        auto triggered = engine.evaluate(quote(1'006'100), base.addSecs(150), true, true);
        QVERIFY(triggered.event.has_value());
        QCOMPARE(triggered.event->model, QStringLiteral("pull"));
        QVERIFY(triggered.event->reason.contains(QStringLiteral("盘口150s")));
        QVERIFY(triggered.snapshot.sellPremiumPpm > 6'000);
        QVERIFY(triggered.snapshot.bidRise150sPpm > 4'000);

        engine.resetAll();
        QVERIFY(!engine.evaluate(quote(1'000'000), base, true, true).event.has_value());
        auto exact = engine.evaluate(quote(1'004'000), base.addSecs(150), true, true);
        QCOMPARE(exact.snapshot.bidRise150sPpm, 4'000);
        QVERIFY(!exact.event.has_value());
    }

    void pullModelWithoutIopvTriggersAndRequiresCompleteWindows()
    {
        auto noIopv = [](qint64 bid) {
            QuoteSnapshot value = quote(bid, 0);
            value.qualityIssues.append(QStringLiteral("iopv_non_positive"));
            return value;
        };
        SignalEngine engine;
        const QDateTime base(QDate(2026, 8, 27), QTime(9, 30), QTimeZone::systemTimeZone());
        QVERIFY(!engine.evaluate(noIopv(1'000'000), base, true, true).event.has_value());
        auto tooEarly = engine.evaluate(noIopv(1'005'000), base.addSecs(149), true, true);
        QCOMPARE(tooEarly.snapshot.bidRise150sPpm, 0);
        QVERIFY(!tooEarly.event.has_value());
        auto triggered = engine.evaluate(noIopv(1'005'000), base.addSecs(150), true, true);
        QVERIFY(triggered.event.has_value());
        QCOMPARE(triggered.event->model, QStringLiteral("pull"));
        QCOMPARE(triggered.event->premiumPpm, 0);
        QVERIFY(triggered.snapshot.bidRise150sPpm > 4'000);
    }

    void pullModel300SecondThresholdIsStrict()
    {
        auto noIopv = [](qint64 bid) {
            QuoteSnapshot value = quote(bid, 0);
            value.qualityIssues.append(QStringLiteral("iopv_non_positive"));
            return value;
        };
        SignalEngine engine;
        const QDateTime base(QDate(2026, 8, 27), QTime(9, 30), QTimeZone::systemTimeZone());
        engine.evaluate(noIopv(1'000'000), base, false, true);
        engine.evaluate(noIopv(1'000'000), base.addSecs(1), false, true);
        auto exact = engine.evaluate(noIopv(1'008'000), base.addSecs(300), false, true);
        QCOMPARE(exact.snapshot.bidRise300sPpm, 8'000);
        QVERIFY(!exact.event.has_value());
        auto above = engine.evaluate(noIopv(1'008'100), base.addSecs(301), false, true);
        QVERIFY(above.event.has_value());
        QVERIFY(above.event->reason.contains(QStringLiteral("盘口300s")));
    }

    void scheduleBoundaries()
    {
        MarketSchedule schedule;
        const QDate day(2026, 8, 27);
        auto auction = schedule.stateAt(QDateTime(day, QTime(9, 15)), false);
        QVERIFY(auction.quotesDesired);
        QVERIFY(!auction.allow30SecondSignal);
        auto morning = schedule.stateAt(QDateTime(day, QTime(9, 31)), false);
        QVERIFY(morning.allow30SecondSignal);
        QVERIFY(!morning.allow300SecondSignal);
        QVERIFY(schedule.stateAt(QDateTime(day, QTime(9, 35)), false).allow300SecondSignal);
        QVERIFY(!schedule.stateAt(QDateTime(day, QTime(14, 57)), false).allow30SecondSignal);
        const auto afterDomesticClose = schedule.stateAt(QDateTime(day, QTime(15, 0)), false);
        QVERIFY(!afterDomesticClose.cnQuotesDesired);
        QVERIFY(afterDomesticClose.hkQuotesDesired);
        QVERIFY(afterDomesticClose.quotesDesired);
        const auto hktClose = schedule.stateAt(QDateTime(day, QTime(16, 0)), false);
        QVERIFY(!hktClose.cnQuotesDesired);
        QVERIFY(!hktClose.hkQuotesDesired);
        QVERIFY(!hktClose.quotesDesired);
        const auto weekendForced = schedule.stateAt(QDateTime(QDate(2026, 8, 29), QTime(10, 0)), true);
        QVERIFY(weekendForced.cnQuotesDesired);
        QVERIFY(weekendForced.hkQuotesDesired);
    }
};

QTEST_MAIN(DomainTests)
#include "test_domain.moc"
