#include "modules/upload/UploadEngine.h"
#include "modules/upload/UploadCollectors.h"

#include <QFile>
#include <QJsonDocument>
#include <QSignalSpy>
#include <QSqlDatabase>
#include <QSqlQuery>
#include <QTemporaryDir>
#include <QTest>
#include <QTimeZone>

using machome::upload::ScheduleDecision;
using machome::upload::UploadEngine;
using machome::upload::UploadJobDefinition;
using machome::upload::UploadSchedule;

namespace {

class FakeUploadTransport final : public machome::upload::IUploadHttpTransport {
public:
    QList<machome::upload::UploadHttpResponse> responses;
    QList<QUrl> urls;
    QList<QByteArray> methods;
    int requests = 0;
    machome::upload::UploadHttpResponse request(const QNetworkRequest &request,
                                                 const QByteArray &method,
                                                 const QByteArray &,
                                                 int) override
    {
        urls.append(request.url());
        methods.append(method);
        const int index = std::min(requests++, int(responses.size()) - 1);
        return index >= 0 ? responses.at(index) : machome::upload::UploadHttpResponse{};
    }
};

QDateTime shanghai(int year, int month, int day, int hour, int minute,
                   int second = 0)
{
    return QDateTime(QDate(year, month, day), QTime(hour, minute, second),
                     QTimeZone("Asia/Shanghai")).toUTC();
}

UploadJobDefinition job(const QString &id)
{
    for (const auto &candidate : UploadSchedule::productionJobs()) {
        if (candidate.id == id) return candidate;
    }
    return {};
}

QJsonObject fixture()
{
    QFile file(QStringLiteral(MACHOME_TEST_SOURCE_DIR)
               + QStringLiteral("/../fixtures/upload/basket_valuation_v1.json"));
    if (!file.open(QIODevice::ReadOnly)) return {};
    return QJsonDocument::fromJson(file.readAll()).object();
}

hub::ModuleContext context(const QString &root)
{
    return {QStringLiteral("upload"), root,
            QJsonObject{{QStringLiteral("sink_mode"), QStringLiteral("record_only")},
                        {QStringLiteral("test_mode"), true},
                        {QStringLiteral("ibkr"), QJsonObject{{QStringLiteral("enabled"), false}}}},
            true};
}

} // namespace

class UploadEngineTest final : public QObject {
    Q_OBJECT

private slots:
    void productionInventoryIsComplete()
    {
        const auto jobs = UploadSchedule::productionJobs();
        QCOMPARE(jobs.size(), 25);
        const QSet<QString> required{
            QStringLiteral("sina-public"), QStringLiteral("xop-family"),
            QStringLiteral("nasdaq"), QStringLiteral("sp500"),
            QStringLiteral("nikkei225"), QStringLiteral("germany"),
            QStringLiteral("silver"), QStringLiteral("china-internet"),
            QStringLiteral("164824"), QStringLiteral("159605"),
            QStringLiteral("513350"), QStringLiteral("intraday-rebuild"),
            QStringLiteral("purchase-status"), QStringLiteral("pcf-prefetch"),
            QStringLiteral("safe-central-parity"), QStringLiteral("cn-close"),
            QStringLiteral("jp-close"), QStringLiteral("hk-close"),
            QStringLiteral("eu-close"), QStringLiteral("us-commodity-close"),
            QStringLiteral("us-close"), QStringLiteral("eastmoney-nav"),
            QStringLiteral("daily-calibration"), QStringLiteral("effective-ratio-fit"),
            QStringLiteral("share-history")};
        QSet<QString> actual;
        for (const auto &value : jobs) actual.insert(value.id);
        QCOMPARE(actual, required);

        const QJsonArray pcfs = UploadEngine::productionPcfDefinitions();
        QCOMPARE(pcfs.size(), 28);
        QSet<QString> pcfSymbols;
        for (const auto &value : pcfs) {
            const QJsonObject definition = value.toObject();
            const QString symbol = definition.value(QStringLiteral("symbol")).toString();
            QVERIFY2(!pcfSymbols.contains(symbol), qPrintable(symbol));
            pcfSymbols.insert(symbol);
            QVERIFY(definition.value(QStringLiteral("redemption_unit")).toDouble() > 0);
            QVERIFY(QUrl(definition.value(QStringLiteral("url")).toString()).isValid());
        }
        QVERIFY(pcfSymbols.contains(QStringLiteral("SZ159518")));
        QVERIFY(pcfSymbols.contains(QStringLiteral("SH513350")));
        QVERIFY(pcfSymbols.contains(QStringLiteral("SH513100")));
        QVERIFY(pcfSymbols.contains(QStringLiteral("SH513500")));
        QVERIFY(pcfSymbols.contains(QStringLiteral("SH513000")));
        QVERIFY(pcfSymbols.contains(QStringLiteral("SH513030")));
        QVERIFY(pcfSymbols.contains(QStringLiteral("SH513220")));
    }

    void uploaderScheduleMatchesExactBoundaries()
    {
        const auto xop = job(QStringLiteral("xop-family"));
        QVERIFY(!UploadSchedule::evaluate(xop, shanghai(2026, 9, 4, 8, 59, 59)).active);
        auto decision = UploadSchedule::evaluate(xop, shanghai(2026, 9, 4, 9, 0));
        QVERIFY(decision.active);
        QVERIFY(decision.due);
        QVERIFY(UploadSchedule::evaluate(xop, shanghai(2026, 9, 4, 14, 59, 59)).active);
        QVERIFY(!UploadSchedule::evaluate(xop, shanghai(2026, 9, 4, 15, 0)).active);
        QVERIFY(!UploadSchedule::evaluate(xop, shanghai(2026, 9, 5, 10, 0)).active);

        const auto nikkei = job(QStringLiteral("nikkei225"));
        QVERIFY(UploadSchedule::evaluate(nikkei, shanghai(2026, 9, 4, 14, 59, 59)).active);
        QVERIFY(!UploadSchedule::evaluate(nikkei, shanghai(2026, 9, 4, 15, 0)).active);

        const auto silver = job(QStringLiteral("silver"));
        QCOMPARE(silver.intervalSeconds, 10);
        QVERIFY(!UploadSchedule::evaluate(silver, shanghai(2026, 9, 4, 9, 14, 59)).active);
        QVERIFY(UploadSchedule::evaluate(silver, shanghai(2026, 9, 4, 9, 15)).active);
        QVERIFY(!UploadSchedule::evaluate(silver, shanghai(2026, 9, 4, 10, 15)).active);
        QVERIFY(UploadSchedule::evaluate(silver, shanghai(2026, 9, 4, 10, 30)).active);
        QVERIFY(UploadSchedule::evaluate(silver, shanghai(2026, 9, 4, 13, 30)).active);

        const auto sina = job(QStringLiteral("sina-public"));
        QCOMPARE(sina.intervalSeconds, 5);
        QVERIFY(UploadSchedule::evaluate(sina, shanghai(2026, 9, 5, 0, 0)).active);
        QCOMPARE(UploadSchedule::evaluate(sina, shanghai(2026, 9, 5, 0, 0)).reason,
                 QStringLiteral("always_on"));
    }

    void dailyScheduleIsIdempotentAcrossResidenceAndWeekend()
    {
        const auto nav = job(QStringLiteral("eastmoney-nav"));
        auto before = UploadSchedule::evaluate(nav, shanghai(2026, 9, 4, 22, 29, 59));
        QVERIFY(!before.due);
        QCOMPARE(before.nextRunUtc, shanghai(2026, 9, 4, 22, 30));
        auto due = UploadSchedule::evaluate(nav, shanghai(2026, 9, 4, 22, 30));
        QVERIFY(due.due);
        QCOMPARE(due.idempotencyKey, QStringLiteral("2026-09-04"));
        auto duplicate = UploadSchedule::evaluate(
            nav, shanghai(2026, 9, 4, 23, 0), shanghai(2026, 9, 4, 22, 30));
        QVERIFY(!duplicate.due);
        const auto weekend = UploadSchedule::evaluate(nav, shanghai(2026, 9, 5, 22, 30));
        QVERIFY(weekend.active);
        QVERIFY(weekend.due);
        QCOMPARE(weekend.idempotencyKey, QStringLiteral("2026-09-05"));
        QCOMPARE(weekend.nextRunUtc, shanghai(2026, 9, 6, 22, 30));
    }

    void safeDailyRetryWindowMatchesBaseline()
    {
        const auto safe = job(QStringLiteral("safe-central-parity"));
        QCOMPARE(safe.retryIntervalSeconds, 60);
        QCOMPARE(safe.retryUntilExclusive, QTime(10, 31));

        const auto before = UploadSchedule::evaluate(
            safe, shanghai(2026, 9, 4, 9, 29, 59));
        QVERIFY(!before.due);

        const QDateTime firstAttempt = shanghai(2026, 9, 4, 9, 30);
        QVERIFY(UploadSchedule::evaluate(safe, firstAttempt).due);
        const auto early = UploadSchedule::evaluate(
            safe, shanghai(2026, 9, 4, 9, 30, 59), firstAttempt);
        QVERIFY(!early.due);
        QCOMPARE(early.reason, QStringLiteral("waiting_retry_interval"));
        const auto retry = UploadSchedule::evaluate(
            safe, shanghai(2026, 9, 4, 9, 31), firstAttempt);
        QVERIFY(retry.due);
        QCOMPARE(retry.reason, QStringLiteral("retry_due"));

        const auto finalMinute = UploadSchedule::evaluate(
            safe, shanghai(2026, 9, 4, 10, 30, 59),
            shanghai(2026, 9, 4, 10, 29, 59));
        QVERIFY(finalMinute.due);
        const auto closed = UploadSchedule::evaluate(
            safe, shanghai(2026, 9, 4, 10, 31),
            shanghai(2026, 9, 4, 10, 30));
        QVERIFY(!closed.due);
        QCOMPARE(closed.reason, QStringLiteral("retry_window_closed"));

        const auto completed = UploadSchedule::evaluate(
            safe, shanghai(2026, 9, 4, 10, 0), firstAttempt,
            shanghai(2026, 9, 4, 9, 30, 30));
        QVERIFY(!completed.due);
        QCOMPARE(completed.reason, QStringLiteral("already_succeeded"));

        const QDateTime saturdayAttempt = shanghai(2026, 9, 5, 9, 30);
        QVERIFY(UploadSchedule::evaluate(safe, saturdayAttempt).due);
        const auto noWeekendEnsureRetry = UploadSchedule::evaluate(
            safe, shanghai(2026, 9, 5, 9, 31), saturdayAttempt);
        QVERIFY(!noWeekendEnsureRetry.due);
        QCOMPARE(noWeekendEnsureRetry.reason,
                 QStringLiteral("retry_window_closed"));
    }

    void pcfPrefetchRetriesEveryMinuteUntilPrivateWindow()
    {
        const auto pcf = job(QStringLiteral("pcf-prefetch"));
        QCOMPARE(pcf.runAt, QTime(8, 30));
        QCOMPARE(pcf.retryIntervalSeconds, 60);
        QCOMPARE(pcf.retryUntilExclusive, QTime(15, 0));
        const QDateTime first = shanghai(2026, 9, 4, 8, 30);
        QVERIFY(UploadSchedule::evaluate(pcf, first).due);
        QVERIFY(!UploadSchedule::evaluate(
                    pcf, shanghai(2026, 9, 4, 8, 30, 59), first).due);
        QVERIFY(UploadSchedule::evaluate(
                    pcf, shanghai(2026, 9, 4, 8, 31), first).due);
        const auto closed = UploadSchedule::evaluate(
            pcf, shanghai(2026, 9, 4, 15, 0),
            shanghai(2026, 9, 4, 14, 59));
        QVERIFY(!closed.due);
        QCOMPARE(closed.reason, QStringLiteral("retry_window_closed"));
        QVERIFY(!UploadSchedule::evaluate(
                    pcf, shanghai(2026, 9, 5, 8, 30)).due);
    }

    void operatingModeControlsOnlyManualTimeGateAndResetsOnInitialize()
    {
        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(temporary.isValid());
        const QString root = temporary.path() + QStringLiteral("/MachomeHub/data/upload");
        UploadEngine engine;
        QSignalSpy commands(&engine, &UploadEngine::commandFinished);
        engine.initialize(context(root));
        QCOMPARE(engine.snapshot().value(QStringLiteral("operating_mode")).toString(),
                 QStringLiteral("work"));
        engine.setNowForTest(shanghai(2026, 9, 5, 12, 0));
        engine.start();

        engine.submitCommand(QStringLiteral("upload_run_job"),
            {{QStringLiteral("job_id"), QStringLiteral("daily-calibration")},
             {QStringLiteral("idempotency_key"), QStringLiteral("work-blocked")}},
            QStringLiteral("work-blocked"));
        QVERIFY(!commands.takeLast().at(1).toBool());
        QCOMPARE(commands.isEmpty(), true);

        engine.submitCommand(QStringLiteral("set_operating_mode"),
                             {{QStringLiteral("mode"), QStringLiteral("weekend_test")}},
                             QStringLiteral("mode-weekend"));
        const QList<QVariant> switched = commands.takeLast();
        QVERIFY(switched.at(1).toBool());
        QCOMPARE(switched.at(3).toJsonObject()
                     .value(QStringLiteral("automatic_schedule_bypass")).toBool(true),
                 false);
        QCOMPARE(engine.snapshot().value(QStringLiteral("operating_mode")).toString(),
                 QStringLiteral("weekend_test"));
        engine.submitCommand(QStringLiteral("upload_run_job"),
            {{QStringLiteral("job_id"), QStringLiteral("daily-calibration")},
             {QStringLiteral("idempotency_key"), QStringLiteral("weekend-manual")}},
            QStringLiteral("weekend-manual"));
        QVERIFY(commands.takeLast().at(1).toBool());

        engine.stop();
        engine.submitCommand(QStringLiteral("set_operating_mode"),
                             {{QStringLiteral("mode"), QStringLiteral("work")}},
                             QStringLiteral("mode-work-stopped"));
        QVERIFY(commands.takeLast().at(1).toBool());
        QVERIFY(!engine.snapshot().value(QStringLiteral("running")).toBool());
        engine.submitCommand(QStringLiteral("set_operating_mode"),
                             {{QStringLiteral("mode"), QStringLiteral("unsafe")}},
                             QStringLiteral("mode-invalid"));
        QVERIFY(!commands.takeLast().at(1).toBool());
        QCOMPARE(engine.snapshot().value(QStringLiteral("operating_mode")).toString(),
                 QStringLiteral("work"));

        QTemporaryDir second(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(second.isValid());
        UploadEngine restarted;
        restarted.initialize(context(second.path()
                                     + QStringLiteral("/MachomeHub/data/upload")));
        QCOMPARE(restarted.snapshot().value(QStringLiteral("operating_mode")).toString(),
                 QStringLiteral("work"));
    }

    void weekendIbkrConfigurationDisablesOnlySchedule()
    {
        const QJsonObject original{
            {QStringLiteral("connection_schedule"), QJsonObject{
                 {QStringLiteral("enabled"), true},
                 {QStringLiteral("timezone"), QStringLiteral("Asia/Shanghai")},
                 {QStringLiteral("start"), QStringLiteral("09:00")},
                 {QStringLiteral("stop"), QStringLiteral("15:06")},
                 {QStringLiteral("weekdays"), QJsonArray{1, 2, 3, 4, 5}}}},
            {QStringLiteral("subscriptions"), QJsonArray{
                 QJsonObject{{QStringLiteral("symbol"), QStringLiteral("XOP")}}}},
            {QStringLiteral("read_only"), true}};
        QString error;
        QCOMPARE(UploadEngine::ibkrConfigurationForOperatingMode(
                     original, QStringLiteral("work"), &error), original);
        const QJsonObject weekend = UploadEngine::ibkrConfigurationForOperatingMode(
            original, QStringLiteral("weekend_test"), &error);
        QVERIFY2(!weekend.isEmpty(), qPrintable(error));
        QVERIFY(!weekend.value(QStringLiteral("connection_schedule")).toObject()
                     .value(QStringLiteral("enabled")).toBool(true));
        QCOMPARE(weekend.value(QStringLiteral("connection_schedule")).toObject()
                     .value(QStringLiteral("stop")).toString(),
                 QStringLiteral("15:06"));
        QCOMPARE(weekend.value(QStringLiteral("subscriptions")),
                 original.value(QStringLiteral("subscriptions")));
        QCOMPARE(weekend.value(QStringLiteral("read_only")).toBool(), true);
        QVERIFY(UploadEngine::ibkrConfigurationForOperatingMode(
                    original, QStringLiteral("production"), &error).isEmpty());
        QVERIFY(!error.isEmpty());
    }

    void goldenBasketValuationMatches()
    {
        const QJsonObject value = fixture();
        QVERIFY(!value.isEmpty());
        QString error;
        const QJsonObject actual = UploadEngine::calculateBasketValuation(
            value.value(QStringLiteral("input")).toObject(), &error);
        QVERIFY2(!actual.isEmpty(), qPrintable(error));
        const QJsonObject expected = value.value(QStringLiteral("expected")).toObject();
        QCOMPARE(actual.value(QStringLiteral("basket_bid_nav")).toDouble(),
                 expected.value(QStringLiteral("basket_bid_nav")).toDouble());
        QCOMPARE(actual.value(QStringLiteral("basket_ask_nav")).toDouble(),
                 expected.value(QStringLiteral("basket_ask_nav")).toDouble());
        QVERIFY(qAbs(actual.value(QStringLiteral("spread")).toDouble()
                     - expected.value(QStringLiteral("spread")).toDouble()) < 1e-12);
        QVERIFY(actual.value(QStringLiteral("actionable")).toBool());
    }

    void privateModelGoldenBranchesMatchBaseline()
    {
        const QString now = QStringLiteral("2026-09-04T10:00:00+08:00");
        const QJsonObject domestic{{QStringLiteral("bid"), 1.99},
                                   {QStringLiteral("ask"), 2.01},
                                   {QStringLiteral("observed_at"), now}};
        const QJsonObject reference{{QStringLiteral("bid"), 40.0},
                                    {QStringLiteral("ask"), 40.1},
                                    {QStringLiteral("observed_at"), now},
                                    {QStringLiteral("market_data_type"), QStringLiteral("Live")}};
        QJsonObject xop{{QStringLiteral("symbol"), QStringLiteral("SZ159518")},
                        {QStringLiteral("model_version"), QStringLiteral("private.total-basket.xop-cfets-pcf.v1")},
                        {QStringLiteral("valuation_kind"), QStringLiteral("xop_proxy")},
                        {QStringLiteral("source"), QStringLiteral("sanitized-golden")},
                        {QStringLiteral("generated_at"), now}, {QStringLiteral("as_of"), now},
                        {QStringLiteral("domestic"), domestic},
                        {QStringLiteral("reference"), reference},
                        {QStringLiteral("fx"), QJsonObject{{QStringLiteral("rate"), 7.2}}},
                        {QStringLiteral("pcf"), QJsonObject{
                             {QStringLiteral("trading_day"), QStringLiteral("2026-09-04")},
                             {QStringLiteral("creation_redemption_unit"), 1000000.0},
                             {QStringLiteral("estimate_cash_component_cny"), 100000.0},
                             {QStringLiteral("redemption"), QStringLiteral("Y")}}}};
        QString error;
        QJsonObject actual = UploadEngine::calculateBasketValuation(xop, &error);
        QVERIFY2(!actual.isEmpty(), qPrintable(error));
        QVERIFY(qAbs(actual.value(QStringLiteral("basket_bid_nav")).toDouble()
                     - 0.386848) < 1e-12);
        QVERIFY(qAbs(actual.value(QStringLiteral("basket_ask_nav")).toDouble()
                     - 0.38756512) < 1e-12);
        QVERIFY(actual.value(QStringLiteral("actionable")).toBool());

        QJsonObject nq = xop;
        nq.insert(QStringLiteral("symbol"), QStringLiteral("SH513100"));
        nq.insert(QStringLiteral("model_version"), QStringLiteral("private.total-basket.nq-cfets-pcf.pre-scan.v1"));
        nq.insert(QStringLiteral("valuation_kind"), QStringLiteral("nq_proxy"));
        QJsonObject nqPcf = nq.value(QStringLiteral("pcf")).toObject();
        nqPcf.insert(QStringLiteral("xop_equivalent_shares"), 0.25);
        nq.insert(QStringLiteral("pcf"), nqPcf);
        actual = UploadEngine::calculateBasketValuation(nq, &error);
        QVERIFY2(!actual.isEmpty(), qPrintable(error));
        QCOMPARE(actual.value(QStringLiteral("contract_multiplier")).toDouble(), 20.0);
        QVERIFY(!actual.value(QStringLiteral("actionable")).toBool());

        const QList<QJsonObject> otherProxyKinds{
            QJsonObject{{QStringLiteral("kind"), QStringLiteral("es_proxy")},
                        {QStringLiteral("symbol"), QStringLiteral("SH513500")},
                        {QStringLiteral("multiplier"), 50.0}, {QStringLiteral("unit"), 1000000.0}},
            QJsonObject{{QStringLiteral("kind"), QStringLiteral("n225m_proxy")},
                        {QStringLiteral("symbol"), QStringLiteral("SH513000")},
                        {QStringLiteral("multiplier"), 100.0}, {QStringLiteral("unit"), 500000.0}},
            QJsonObject{{QStringLiteral("kind"), QStringLiteral("dax_proxy")},
                        {QStringLiteral("symbol"), QStringLiteral("SH513030")},
                        {QStringLiteral("multiplier"), 5.0}, {QStringLiteral("unit"), 500000.0}}};
        for (const QJsonObject &definition : otherProxyKinds) {
            const QString kind = definition.value(QStringLiteral("kind")).toString();
            QJsonObject proxy = nq;
            proxy.insert(QStringLiteral("symbol"), definition.value(QStringLiteral("symbol")));
            QJsonObject proxyPcf = proxy.value(QStringLiteral("pcf")).toObject();
            proxyPcf.insert(QStringLiteral("creation_redemption_unit"),
                            definition.value(QStringLiteral("unit")));
            proxy.insert(QStringLiteral("pcf"), proxyPcf);
            proxy.insert(QStringLiteral("valuation_kind"), kind);
            proxy.insert(QStringLiteral("model_version"),
                         kind == QStringLiteral("es_proxy")
                             ? QStringLiteral("private.total-basket.es-cfets-pcf.pre-scan.v1")
                         : kind == QStringLiteral("n225m_proxy")
                             ? QStringLiteral("private.total-basket.n225m-cfets-pcf.pre-scan.v1")
                             : QStringLiteral("private.total-basket.fdxm-cfets-pcf.xetra-1735-anchor.pre-scan.v2"));
            actual = UploadEngine::calculateBasketValuation(proxy, &error);
            QVERIFY2(!actual.isEmpty(), qPrintable(error));
            QCOMPARE(actual.value(QStringLiteral("contract_multiplier")).toDouble(),
                     definition.value(QStringLiteral("multiplier")).toDouble());
            QVERIFY(!actual.value(QStringLiteral("actionable")).toBool());
        }

        QJsonObject fixed513350 = xop;
        fixed513350.insert(QStringLiteral("symbol"), QStringLiteral("SH513350"));
        fixed513350.insert(QStringLiteral("model_version"),
                          QStringLiteral("private.total-basket.xop-cfets-pcf.sh513350.v1"));
        actual = UploadEngine::calculateBasketValuation(fixed513350, &error);
        QVERIFY2(!actual.isEmpty(), qPrintable(error));
        QCOMPARE(actual.value(QStringLiteral("reference_equivalent")).toDouble(), 1046.0);

        QJsonObject lof{{QStringLiteral("symbol"), QStringLiteral("SZ162411")},
                        {QStringLiteral("model_version"), QStringLiteral("private.weighted-nav.xop-safe.us-close.v1")},
                        {QStringLiteral("valuation_kind"), QStringLiteral("lof_weighted_anchor")},
                        {QStringLiteral("source"), QStringLiteral("sanitized-golden")},
                        {QStringLiteral("generated_at"), now}, {QStringLiteral("as_of"), now},
                        {QStringLiteral("domestic"), domestic},
                        {QStringLiteral("reference"), QJsonObject{
                             {QStringLiteral("bid"), 44.0}, {QStringLiteral("ask"), 44.2},
                             {QStringLiteral("observed_at"), now},
                             {QStringLiteral("market_data_type"), QStringLiteral("Live")}}},
                        {QStringLiteral("lof"), QJsonObject{
                             {QStringLiteral("base_nav"), 1.0},
                             {QStringLiteral("base_reference"), QJsonObject{{QStringLiteral("price"), 40.0}}},
                             {QStringLiteral("base_fx"), QJsonObject{{QStringLiteral("rate"), 7.0}}},
                             {QStringLiteral("current_fx"), QJsonObject{{QStringLiteral("rate"), 7.2}}},
                             {QStringLiteral("effective_ratio"), QJsonObject{{QStringLiteral("value"), 0.955}}}}}};
        actual = UploadEngine::calculateBasketValuation(lof, &error);
        QVERIFY2(!actual.isEmpty(), qPrintable(error));
        const double expectedLofBid = 0.045 + 0.955 * (44.0 / 40.0) * (7.2 / 7.0);
        QVERIFY(qAbs(actual.value(QStringLiteral("basket_bid_nav")).toDouble()
                     - expectedLofBid) < 1e-12);

        QJsonArray anchors;
        anchors.append(QJsonObject{{QStringLiteral("key"), QStringLiteral("jp_close")},
                                   {QStringLiteral("weight"), 0.25}, {QStringLiteral("price"), 50.0}});
        anchors.append(QJsonObject{{QStringLiteral("key"), QStringLiteral("hk_close")},
                                   {QStringLiteral("weight"), 0.25}, {QStringLiteral("price"), 52.0}});
        anchors.append(QJsonObject{{QStringLiteral("key"), QStringLiteral("eu_close")},
                                   {QStringLiteral("weight"), 0.25}, {QStringLiteral("price"), 54.0}});
        anchors.append(QJsonObject{{QStringLiteral("key"), QStringLiteral("us_close")},
                                   {QStringLiteral("weight"), 0.25}, {QStringLiteral("price"), 56.0}});
        QJsonObject india{{QStringLiteral("symbol"), QStringLiteral("SZ164824")},
                          {QStringLiteral("model_version"), QStringLiteral("private.t2-multimarket.inda-safe.v1")},
                          {QStringLiteral("valuation_kind"), QStringLiteral("india_t2_multimarket")},
                          {QStringLiteral("source"), QStringLiteral("sanitized-golden")},
                          {QStringLiteral("generated_at"), now}, {QStringLiteral("as_of"), now},
                          {QStringLiteral("domestic"), domestic},
                          {QStringLiteral("reference"), QJsonObject{
                               {QStringLiteral("bid"), 54.0}, {QStringLiteral("ask"), 54.2},
                               {QStringLiteral("observed_at"), now},
                               {QStringLiteral("market_data_type"), QStringLiteral("Live")}}},
                          {QStringLiteral("india"), QJsonObject{
                               {QStringLiteral("base_nav"), 1.2},
                               {QStringLiteral("investment_ratio"), 0.8937},
                               {QStringLiteral("static_ratio"), 0.1063},
                               {QStringLiteral("base_fx"), QJsonObject{{QStringLiteral("rate"), 7.0}}},
                               {QStringLiteral("current_fx"), QJsonObject{{QStringLiteral("rate"), 7.1}}},
                               {QStringLiteral("anchors"), anchors}}}};
        actual = UploadEngine::calculateBasketValuation(india, &error);
        QVERIFY2(!actual.isEmpty(), qPrintable(error));
        QCOMPARE(actual.value(QStringLiteral("anchor_price")).toDouble(), 53.0);
        const double expectedIndiaBid = 1.2 * 0.1063
            + 1.2 * 0.8937 * (54.0 / 53.0) * (7.1 / 7.0);
        QVERIFY(qAbs(actual.value(QStringLiteral("basket_bid_nav")).toDouble()
                     - expectedIndiaBid) < 1e-12);

        QJsonObject silver{{QStringLiteral("symbol"), QStringLiteral("SZ161226")},
                           {QStringLiteral("model_version"), QStringLiteral("private.cn-future.ag-settlement.v1")},
                           {QStringLiteral("valuation_kind"), QStringLiteral("silver_settlement")},
                           {QStringLiteral("source"), QStringLiteral("sanitized-golden")},
                           {QStringLiteral("generated_at"), now}, {QStringLiteral("as_of"), now},
                           {QStringLiteral("domestic"), domestic},
                           {QStringLiteral("silver"), QJsonObject{
                                {QStringLiteral("base_nav"), 1.5},
                                {QStringLiteral("base_nav_date"), QStringLiteral("2026-09-03")},
                                {QStringLiteral("trading_day"), QStringLiteral("2026-09-04")},
                                {QStringLiteral("contract"), QStringLiteral("AG2610")},
                                {QStringLiteral("contract_selection_version"), QStringLiteral("shfe-ag-even-month-roll-day10.v1")},
                                {QStringLiteral("previous_settlement"), 8000.0},
                                {QStringLiteral("previous_settlement_date"), QStringLiteral("2026-09-03")},
                                {QStringLiteral("futures_price"), 8080.0},
                                {QStringLiteral("intraday_average"), 8040.0},
                                {QStringLiteral("observed_at"), now},
                                {QStringLiteral("source"), QStringLiteral("sanitized-shfe")}}}};
        actual = UploadEngine::calculateBasketValuation(silver, &error);
        QVERIFY2(!actual.isEmpty(), qPrintable(error));
        QVERIFY(qAbs(actual.value(QStringLiteral("settlement_nav")).toDouble() - 1.5075) < 1e-12);
        QVERIFY(qAbs(actual.value(QStringLiteral("trading_nav")).toDouble() - 1.515) < 1e-12);
        QVERIFY(!actual.value(QStringLiteral("actionable")).toBool());
    }

    void staleAndNonLiveModelsRemainDisplayOnly()
    {
        const QString generated = QStringLiteral("2026-09-04T10:00:30+08:00");
        const QString observed = QStringLiteral("2026-09-04T09:59:00+08:00");
        QJsonObject input{{QStringLiteral("symbol"), QStringLiteral("SZ159518")},
                          {QStringLiteral("model_version"), QStringLiteral("private.total-basket.xop-cfets-pcf.v1")},
                          {QStringLiteral("valuation_kind"), QStringLiteral("xop_proxy")},
                          {QStringLiteral("source"), QStringLiteral("sanitized")},
                          {QStringLiteral("generated_at"), generated},
                          {QStringLiteral("as_of"), generated},
                          {QStringLiteral("domestic"), QJsonObject{{QStringLiteral("bid"), 1.0}, {QStringLiteral("ask"), 1.1}, {QStringLiteral("observed_at"), observed}}},
                          {QStringLiteral("reference"), QJsonObject{{QStringLiteral("bid"), 40.0}, {QStringLiteral("ask"), 40.1}, {QStringLiteral("observed_at"), observed}, {QStringLiteral("market_data_type"), QStringLiteral("Delayed")}}},
                          {QStringLiteral("fx"), QJsonObject{{QStringLiteral("rate"), 7.2}}},
                          {QStringLiteral("pcf"), QJsonObject{{QStringLiteral("trading_day"), QStringLiteral("2026-09-03")}, {QStringLiteral("creation_redemption_unit"), 1000000.0}, {QStringLiteral("estimate_cash_component_cny"), 10000.0}, {QStringLiteral("redemption"), QStringLiteral("N")}}}};
        QString error;
        const QJsonObject actual = UploadEngine::calculateBasketValuation(input, &error);
        QVERIFY2(!actual.isEmpty(), qPrintable(error));
        QVERIFY(!actual.value(QStringLiteral("actionable")).toBool());
        QVERIFY(actual.value(QStringLiteral("warnings")).toArray().size() >= 4);
    }

    void acceptsOriginalWorkerWireContractWithoutLegacyRuntime()
    {
        const QString now = QStringLiteral("2026-09-04T10:00:00+08:00");
        QJsonObject xop{{QStringLiteral("schema_version"), 1},
                        {QStringLiteral("symbol"), QStringLiteral("SZ159518")},
                        {QStringLiteral("model_version"), QStringLiteral("private.total-basket.xop-cfets-pcf.v1")},
                        {QStringLiteral("source"), QStringLiteral("mac-home-private-xop-family-uploader")},
                        {QStringLiteral("generated_at"), now},
                        {QStringLiteral("ib"), QJsonObject{
                             {QStringLiteral("symbol"), QStringLiteral("XOP")},
                             {QStringLiteral("bid"), 40.0}, {QStringLiteral("ask"), 40.1},
                             {QStringLiteral("observed_at"), now},
                             {QStringLiteral("stream_checked_at"), now},
                             {QStringLiteral("market_data_type"), QStringLiteral("Live")}}},
                        {QStringLiteral("fx"), QJsonObject{
                             {QStringLiteral("pair"), QStringLiteral("USD/CNY")},
                             {QStringLiteral("rate"), 7.2},
                             {QStringLiteral("trading_day"), QStringLiteral("2026-09-04")},
                             {QStringLiteral("observed_at"), now}}},
                        {QStringLiteral("pcf"), QJsonObject{
                             {QStringLiteral("trading_day"), QStringLiteral("2026-09-04")},
                             {QStringLiteral("creation_redemption_unit"), 1000000.0},
                             {QStringLiteral("estimate_cash_component_cny"), 100000.0},
                             {QStringLiteral("redemption"), QStringLiteral("Y")}}}};
        QString error;
        QJsonObject actual = UploadEngine::calculateBasketValuation(xop, &error);
        QVERIFY2(!actual.isEmpty(), qPrintable(error));
        QCOMPARE(actual.value(QStringLiteral("valuation_kind")).toString(), QStringLiteral("xop_proxy"));
        QVERIFY(qAbs(actual.value(QStringLiteral("basket_bid_nav")).toDouble() - 0.386848) < 1e-12);
        QVERIFY(!actual.value(QStringLiteral("actionable")).toBool());

        QJsonArray components{
            QJsonObject{{QStringLiteral("market"), QStringLiteral("US")},
                        {QStringLiteral("symbol"), QStringLiteral("A")},
                        {QStringLiteral("currency"), QStringLiteral("USD")},
                        {QStringLiteral("quantity"), 10.0}},
            QJsonObject{{QStringLiteral("market"), QStringLiteral("HK")},
                        {QStringLiteral("symbol"), QStringLiteral("0700")},
                        {QStringLiteral("currency"), QStringLiteral("HKD")},
                        {QStringLiteral("quantity"), 20.0}}};
        QJsonArray marketQuotes{
            QJsonObject{{QStringLiteral("market"), QStringLiteral("US")},
                        {QStringLiteral("symbol"), QStringLiteral("A")},
                        {QStringLiteral("bid"), 10.0}, {QStringLiteral("ask"), 10.1}},
            QJsonObject{{QStringLiteral("market"), QStringLiteral("HK")},
                        {QStringLiteral("symbol"), QStringLiteral("0700")},
                        {QStringLiteral("bid"), 20.0}, {QStringLiteral("ask"), 20.2}}};
        QJsonArray rates{
            QJsonObject{{QStringLiteral("pair"), QStringLiteral("USD/CNY")}, {QStringLiteral("rate"), 7.2}},
            QJsonObject{{QStringLiteral("pair"), QStringLiteral("HKD/CNY")}, {QStringLiteral("rate"), 0.92}}};
        QJsonObject cash{{QStringLiteral("schema_version"), 1},
                         {QStringLiteral("symbol"), QStringLiteral("SZ159605")},
                         {QStringLiteral("model_version"), QStringLiteral("private.full-cash-substitution.multi-market-pcf.v1")},
                         {QStringLiteral("source"), QStringLiteral("mac-home-private-159605-uploader")},
                         {QStringLiteral("generated_at"), now},
                         {QStringLiteral("pcf"), QJsonObject{
                              {QStringLiteral("trading_day"), QStringLiteral("2026-09-04")},
                              {QStringLiteral("creation_redemption_unit"), 1000000.0},
                              {QStringLiteral("estimate_cash_component_cny"), 1000.0},
                              {QStringLiteral("creation"), QStringLiteral("Y")},
                              {QStringLiteral("redemption"), QStringLiteral("Y")},
                              {QStringLiteral("components"), components}}},
                         {QStringLiteral("fx_rates"), rates},
                         {QStringLiteral("market_quotes"), marketQuotes}};
        actual = UploadEngine::calculateBasketValuation(cash, &error);
        QVERIFY2(!actual.isEmpty(), qPrintable(error));
        QCOMPARE(actual.value(QStringLiteral("valuation_kind")).toString(),
                 QStringLiteral("full_cash_substitution_pcf"));
        QVERIFY(qAbs(actual.value(QStringLiteral("basket_bid_nav")).toDouble()
                     - (1000.0 + 10.0 * 10.0 * 7.2 + 20.0 * 20.0 * 0.92) / 1000000.0) < 1e-12);
        QVERIFY(!actual.value(QStringLiteral("actionable")).toBool());
    }

    void malformedAndStaleInputsFailClosed()
    {
        QString error;
        QVERIFY(!UploadEngine::validateQuote(
            {{QStringLiteral("symbol"), QStringLiteral("XOP")},
             {QStringLiteral("price"), -1},
             {QStringLiteral("source"), QStringLiteral("fixture")},
             {QStringLiteral("observed_at"), QStringLiteral("2026-09-04T09:37:00Z")}},
            &error));
        QVERIFY(!error.isEmpty());
        error.clear();
        QVERIFY(!UploadEngine::validateQuote(
            {{QStringLiteral("symbol"), QStringLiteral("XOP")},
             {QStringLiteral("price"), 10}, {QStringLiteral("bid"), 10.2},
             {QStringLiteral("ask"), 10.1},
             {QStringLiteral("source"), QStringLiteral("fixture")},
             {QStringLiteral("observed_at"), QStringLiteral("2026-09-04T09:37:00Z")}},
            &error));
        QJsonObject input = fixture().value(QStringLiteral("input")).toObject();
        input.insert(QStringLiteral("fx_rates"), QJsonObject{});
        QVERIFY(UploadEngine::calculateBasketValuation(input, &error).isEmpty());
    }

    void nativeIbkrFramesNormalizeAndFailClosed()
    {
        const QJsonObject item{
            {QStringLiteral("contract"), QJsonObject{
                 {QStringLiteral("id"), QStringLiteral("xop-smart-usd")},
                 {QStringLiteral("symbol"), QStringLiteral("XOP")}}},
            {QStringLiteral("sequence"), 19},
            {QStringLiteral("market_data_type"), 1},
            {QStringLiteral("bid"), QStringLiteral("142.10")},
            {QStringLiteral("ask"), QStringLiteral("142.12")},
            {QStringLiteral("last"), QJsonValue::Null},
            {QStringLiteral("close"), QStringLiteral("141.90")},
            {QStringLiteral("received_at"), QStringLiteral("2026-09-04T02:00:00.000Z")},
            {QStringLiteral("fresh"), true}};
        QString error;
        const QJsonObject quote = UploadEngine::quoteFromIbkrItem(item, &error);
        QVERIFY2(!quote.isEmpty(), qPrintable(error));
        QCOMPARE(quote.value(QStringLiteral("symbol")).toString(), QStringLiteral("XOP"));
        QCOMPARE(quote.value(QStringLiteral("price")).toDouble(), 142.11);
        QCOMPARE(quote.value(QStringLiteral("market_data_type")).toString(),
                 QStringLiteral("Live"));
        QVERIFY(UploadEngine::validateQuote(quote, &error));

        QJsonObject crossed = item;
        crossed.insert(QStringLiteral("bid"), QStringLiteral("142.13"));
        QVERIFY(UploadEngine::quoteFromIbkrItem(crossed, &error).isEmpty());
        QJsonObject partial = item;
        partial.insert(QStringLiteral("ask"), QJsonValue::Null);
        QVERIFY(UploadEngine::quoteFromIbkrItem(partial, &error).isEmpty());
        QJsonObject malformed = item;
        malformed.insert(QStringLiteral("received_at"), QStringLiteral("not-a-time"));
        QVERIFY(UploadEngine::quoteFromIbkrItem(malformed, &error).isEmpty());
    }

    void nativeCollectorParsersMatchGoldenAndRejectDamage()
    {
        QString error;
        const QByteArray purchase = R"(var db={datas:[["159518","X","X","X","X","开放申购","","","","1000元","",""]]};)";
        const auto purchases = machome::upload::UploadCollectors::parseEastmoneyPurchaseStatus(
            purchase, {{QStringLiteral("159518"), QStringLiteral("SZ159518")}},
            QStringLiteral("2026-09-04"), &error);
        QCOMPARE(purchases.size(), 1);
        QCOMPARE(purchases.first().toObject().value("daily_limit_yuan").toDouble(), 1000.0);
        QVERIFY(machome::upload::UploadCollectors::parseEastmoneyPurchaseStatus(
            QByteArray("datas:[broken"), {}, QStringLiteral("2026-09-04"), &error).isEmpty());

        const QByteArray nav = R"({"ErrCode":0,"Data":{"LSJZList":[{"FSRQ":"2026-09-03","DWJZ":"1.2345"}]}})";
        QCOMPARE(machome::upload::UploadCollectors::parseEastmoneyNetValues(
                     nav, QStringLiteral("SZ159518"), &error).size(), 1);
        QVERIFY(machome::upload::UploadCollectors::parseEastmoneyNetValues(
                    QByteArray(R"({"ErrCode":1,"Data":{}})"),
                    QStringLiteral("SZ159518"), &error).isEmpty());
        const QByteArray szse = R"([{"metadata":{"pagecount":1},"data":[{"size_date":"2026-09-03","fund_code":"159518","current_size":"12,345.6"}],"error":""}])";
        QCOMPARE(machome::upload::UploadCollectors::parseSzseShares(szse, &error).size(), 1);
        QVERIFY(machome::upload::UploadCollectors::parseSzseShares(
                    QByteArray("not-json"), &error).isEmpty());
        const QByteArray sse = R"({"result":[{"STAT_DATE":"2026-09-03","SEC_CODE":"513100","TOT_VOL":"54321"}]})";
        QCOMPARE(machome::upload::UploadCollectors::parseSseShares(sse, QStringLiteral("ETF"), &error).size(), 1);
        QVERIFY(machome::upload::UploadCollectors::parseSseShares(
                    QByteArray(R"({"result":[{"STAT_DATE":"bad","SEC_CODE":"513100","TOT_VOL":"54321"}]})"),
                    QStringLiteral("ETF"), &error).isEmpty());

        QFile safe(QStringLiteral(MACHOME_TEST_SOURCE_DIR)
                   + QStringLiteral("/../fixtures/upload/safe_central_parity_golden.xls"));
        QVERIFY(safe.open(QIODevice::ReadOnly));
        const auto rates = machome::upload::UploadCollectors::parseSafeXls(safe.readAll(), &error);
        QCOMPARE(rates.size(), 4);
        QVERIFY(machome::upload::UploadCollectors::parseSafeXls(
            QByteArray::fromHex("d0cf11e0a1b11ae1"), &error).isEmpty());

        const QByteArray pcf = R"(<PCFFile><SecurityID>159518</SecurityID><TradingDay>2026-09-04</TradingDay><PreTradingDay>2026-09-03</PreTradingDay><Creation>Y</Creation><Redemption>Y</Redemption><CreationRedemptionUnit>1000000</CreationRedemptionUnit><EstimateCashComponent>100</EstimateCashComponent><NAVperCU>1000000</NAVperCU><TotalRecordNum>1</TotalRecordNum><Component><UnderlyingSecurityIDSource>9999</UnderlyingSecurityIDSource><UnderlyingSecurityID>XOP</UnderlyingSecurityID><UnderlyingSymbol>XOP</UnderlyingSymbol><ComponentShare>996</ComponentShare></Component></PCFFile>)";
        const QJsonObject definition{{"symbol","SZ159518"},{"security_id","159518"},
            {"exchange","SZSE"},{"market","US"},{"currency","USD"},
            {"redemption_unit",1000000.0}};
        const auto parsed = machome::upload::UploadCollectors::parsePcf(
            pcf, definition, QStringLiteral("https://fixture.invalid/pcf.xml"),
            QStringLiteral("2026-09-04"), &error);
        QVERIFY2(!parsed.isEmpty(), qPrintable(error));
        QCOMPARE(parsed.value("pcf").toObject().value("components").toArray().size(), 1);
        QVERIFY(machome::upload::UploadCollectors::parsePcf(
            QByteArray("<broken>"), definition, QStringLiteral("https://fixture.invalid"),
            QStringLiteral("2026-09-04"), &error).isEmpty());
    }

    void liveCollectorRetriesAndPersistsThroughInjectedTransport()
    {
        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(temporary.isValid());
        auto fake = std::make_unique<FakeUploadTransport>();
        FakeUploadTransport *observer = fake.get();
        fake->responses = {
            {503, QByteArray("busy"), QByteArray("text/plain"), {}},
            {0, {}, {}, QStringLiteral("disconnect")},
            {200, QByteArray(R"(x={datas:[["159518","","","","","开放申购","","","","5000元","",""]]};)"), QByteArray("text/plain"), {}}};
        auto value = context(temporary.path() + QStringLiteral("/MachomeHub/data/upload"));
        value.settings.insert("live_reads_enabled", true);
        value.settings.insert("collectors", QJsonObject{{"purchase-status", QJsonObject{
            {"enabled",true},{"url","https://fixture.invalid/purchase"},{"timeout_ms",1000},
            {"attempts",3},{"symbols",QJsonArray{"SZ159518"}}}}});
        UploadEngine engine; engine.setHttpTransportForTest(std::move(fake));
        QSignalSpy commands(&engine, &UploadEngine::commandFinished);
        engine.initialize(value); engine.setNowForTest(shanghai(2026,9,4,7,0));
        engine.submitCommand("set_operating_mode", {{"mode","weekend_test"}}, "mode");
        QVERIFY(commands.takeLast().at(1).toBool());
        engine.start();
        engine.submitCommand("upload_run_job", {{"job_id","purchase-status"},
            {"idempotency_key","purchase-live-golden"}}, "collect");
        QVERIFY(commands.takeLast().at(1).toBool());
        QCOMPARE(observer->requests, 3);
        const QString connection = QStringLiteral("collector-check");
        QSqlDatabase db=QSqlDatabase::addDatabase("QSQLITE",connection);db.setDatabaseName(engine.databasePath());QVERIFY(db.open());QSqlQuery q(db);QVERIFY(q.exec("SELECT count(*) FROM purchase_status"));QVERIFY(q.next());QCOMPARE(q.value(0).toInt(),1);db.close();db={};QSqlDatabase::removeDatabase(connection);engine.stop();
    }

    void existingSafeParitySuppressesRetryAfterRestart()
    {
        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(temporary.isValid());
        auto fake = std::make_unique<FakeUploadTransport>();
        FakeUploadTransport *observer = fake.get();
        fake->responses = {{503, QByteArray("must-not-run"), QByteArray("text/plain"), {}}};
        auto value = context(temporary.path() + QStringLiteral("/MachomeHub/data/upload"));
        value.settings.insert("live_reads_enabled", true);
        value.settings.insert("collectors", QJsonObject{{"safe-central-parity", QJsonObject{
            {"enabled",true},{"url","https://fixture.invalid/safe.xls"},
            {"timeout_ms",1000},{"attempts",3},{"lookback_days",10}}}});
        UploadEngine engine;
        engine.setHttpTransportForTest(std::move(fake));
        engine.initialize(value);

        const QString connection = QStringLiteral("safe-restart-check");
        QSqlDatabase db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connection);
        db.setDatabaseName(engine.databasePath());
        QVERIFY(db.open());
        QSqlQuery insert(db);
        insert.prepare(QStringLiteral(
            "INSERT INTO fx_rates(pair,rate_date,rate,source,payload_json) VALUES(?,?,?,?,?)"));
        insert.addBindValue(QStringLiteral("USDCNY"));
        insert.addBindValue(QStringLiteral("2026-09-04"));
        insert.addBindValue(7.1);
        insert.addBindValue(QStringLiteral("SAFE"));
        insert.addBindValue(QStringLiteral("{}"));
        QVERIFY(insert.exec());
        db.close();
        db = {};
        QSqlDatabase::removeDatabase(connection);

        engine.setNowForTest(shanghai(2026, 9, 4, 9, 30));
        engine.start();
        QCOMPARE(observer->requests, 0);
        bool found = false;
        for (const auto &item : engine.snapshot().value(QStringLiteral("jobs")).toArray()) {
            const QJsonObject row = item.toObject();
            if (row.value(QStringLiteral("id")).toString()
                == QStringLiteral("safe-central-parity")) {
                found = true;
                QCOMPARE(row.value(QStringLiteral("stage")).toString(),
                         QStringLiteral("already_satisfied"));
            }
        }
        QVERIFY(found);
        engine.stop();
    }

    void everyLiveCollectorUsesInjectedHttpPersistsAndPcfFallsBack()
    {
        QFile safe(QStringLiteral(MACHOME_TEST_SOURCE_DIR)
                   + QStringLiteral("/../fixtures/upload/safe_central_parity_golden.xls"));
        QVERIFY(safe.open(QIODevice::ReadOnly));
        const QByteArray safeBody = safe.readAll();
        const QByteArray previousPcf = R"(<PCFFile><SecurityID>159518</SecurityID><TradingDay>2026-09-03</TradingDay><PreTradingDay>2026-09-02</PreTradingDay><Creation>Y</Creation><Redemption>Y</Redemption><CreationRedemptionUnit>1000000</CreationRedemptionUnit><EstimateCashComponent>-12.5</EstimateCashComponent><NAVperCU>1000000</NAVperCU><TotalRecordNum>1</TotalRecordNum><Component><UnderlyingSecurityIDSource>9999</UnderlyingSecurityIDSource><UnderlyingSecurityID>XOP</UnderlyingSecurityID><UnderlyingSymbol>XOP</UnderlyingSymbol><ComponentShare>996</ComponentShare></Component></PCFFile>)";

        auto fake = std::make_unique<FakeUploadTransport>();
        FakeUploadTransport *observer = fake.get();
        fake->responses = {
            {200, QByteArray(R"({"ErrCode":0,"Data":{"LSJZList":[{"FSRQ":"2026-09-03","DWJZ":"1.2345"}]}})"), "application/json", {}},
            {200, safeBody, "application/x-download;charset=UTF-8", {}},
            {200, QByteArray(R"([{"metadata":{"pageno":1,"pagecount":1},"data":[{"size_date":"2026-09-04","fund_code":"159518","current_size":"12,345.6"}],"error":""}])"), "application/json", {}},
            {200, QByteArray(R"({"pageHelp":{"pageNo":1,"pageCount":1},"result":[{"STAT_DATE":"2026-09-04","SEC_CODE":"513100","TOT_VOL":"54321"}]})"), "application/json", {}},
            {200, QByteArray(R"({"pageHelp":{"pageNo":1,"pageCount":1},"result":[{"TRADE_DATE":"20260904","FUND_CODE":"501001","INTERNAL_VOL":"406.87"}]})"), "application/json", {}},
            {404, QByteArray("not yet"), "text/plain", {}},
            {200, previousPcf, "application/xml", {}}};

        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(temporary.isValid());
        auto value = context(temporary.path() + QStringLiteral("/MachomeHub/data/upload"));
        value.settings.insert("live_reads_enabled", true);
        value.settings.insert("collectors", QJsonObject{
            {"eastmoney-nav", QJsonObject{{"enabled",true},{"url","https://fixture.invalid/nav"},{"attempts",3},{"symbols",QJsonArray{"SZ159518"}}}},
            {"safe-central-parity", QJsonObject{{"enabled",true},{"url","https://fixture.invalid/safe.xls"},{"attempts",3},{"lookback_days",10}}},
            {"share-history", QJsonObject{{"enabled",true},{"szse_url","https://fixture.invalid/szse"},{"sse_etf_url","https://fixture.invalid/sse"},{"sse_lof_url","https://fixture.invalid/sse"},{"attempts",3},{"lookback_days",1},{"request_interval_ms",0},{"symbols",QJsonArray{"SZ159518"}}}},
            {"pcf-prefetch", QJsonObject{
                {"enabled",true},{"attempts",3},{"lookback_days",3},
                {"funds",QJsonArray{QJsonObject{
                    {"symbol","SZ159518"},{"security_id","159518"},
                    {"exchange","SZSE"},{"market","US"},{"currency","USD"},
                    {"redemption_unit",1000000.0},
                    {"url","https://fixture.invalid/pcf_{yyyymmdd}.xml"}}}}}}
        });
        UploadEngine engine; engine.setHttpTransportForTest(std::move(fake));
        QSignalSpy commands(&engine, &UploadEngine::commandFinished);
        engine.initialize(value); engine.setNowForTest(shanghai(2026,9,4,5,0));
        engine.submitCommand("set_operating_mode", {{"mode","weekend_test"}}, "mode");
        QVERIFY(commands.takeLast().at(1).toBool());
        engine.start();
        for (const QString &id : {QStringLiteral("eastmoney-nav"),
                                  QStringLiteral("safe-central-parity"),
                                  QStringLiteral("share-history"),
                                  QStringLiteral("pcf-prefetch")}) {
            engine.submitCommand("upload_run_job", {{"job_id",id},{"idempotency_key","all-live-"+id}}, id);
            QVERIFY2(!commands.isEmpty(), qPrintable(id));
            const QList<QVariant> result = commands.takeLast();
            QVERIFY2(result.at(1).toBool(), qPrintable(result.at(2).toString()));
        }
        QCOMPARE(observer->requests, 7);
        QCOMPARE(observer->methods.at(1), QByteArray("POST"));
        QVERIFY(observer->urls.at(0).query().contains("fundCode=159518"));
        QVERIFY(observer->urls.at(2).query().contains("txtDm=159518"));
        QVERIFY(observer->urls.at(3).query().contains("pageHelp.pageSize=2000"));
        QVERIFY2(observer->urls.at(6).path().endsWith("pcf_20260903.xml"),
                 qPrintable(observer->urls.at(6).toString()));

        const QString connection = QStringLiteral("all-collector-check");
        QSqlDatabase db=QSqlDatabase::addDatabase("QSQLITE",connection);
        db.setDatabaseName(engine.databasePath());QVERIFY(db.open());QSqlQuery q(db);
        auto count=[&](const QString &table)->int{if(!q.exec("SELECT count(*) FROM "+table)||!q.next())return -1;return q.value(0).toInt();};
        QCOMPARE(count("net_values"),1);QCOMPARE(count("fx_rates"),4);QCOMPARE(count("share_history"),3);QCOMPARE(count("pcf_cache"),1);
        QVERIFY(q.exec("SELECT trading_day FROM pcf_cache WHERE symbol='SZ159518'"));QVERIFY(q.next());QCOMPARE(q.value(0).toString(),QStringLiteral("2026-09-03"));
        db.close();db={};QSqlDatabase::removeDatabase(connection);engine.stop();
    }

    void httpFourHundredDoesNotRetryAndPcfCountMismatchFailsClosed()
    {
        auto fake = std::make_unique<FakeUploadTransport>();
        FakeUploadTransport *observer = fake.get();
        fake->responses = {{404,QByteArray("missing"),QByteArray("text/plain"),{}}};
        machome::upload::UploadCollectors collectors(std::move(fake));
        QString error;
        collectors.fetch(QUrl("https://fixture.invalid/missing"),"GET",{}, {},1000,5,&error);
        QCOMPARE(observer->requests,1);QCOMPARE(error,QStringLiteral("HTTP 404"));

        const QByteArray mismatch = R"(<PCFFile><SecurityID>159518</SecurityID><TradingDay>2026-09-04</TradingDay><Creation>Y</Creation><Redemption>Y</Redemption><CreationRedemptionUnit>1000000</CreationRedemptionUnit><EstimateCashComponent>1</EstimateCashComponent><NAVperCU>100</NAVperCU><TotalRecordNum>2</TotalRecordNum><Component><UnderlyingSecurityIDSource>9999</UnderlyingSecurityIDSource><UnderlyingSecurityID>XOP</UnderlyingSecurityID><ComponentShare>1</ComponentShare></Component></PCFFile>)";
        const QJsonObject definition{{"symbol","SZ159518"},{"security_id","159518"},
            {"exchange","SZSE"},{"market","US"},{"currency","USD"},{"redemption_unit",1000000.0}};
        QVERIFY(machome::upload::UploadCollectors::parsePcf(
            mismatch,definition,"https://fixture.invalid/pcf.xml","2026-09-04",&error).isEmpty());
        QVERIFY(error.contains(QStringLiteral("不符")));
    }

    void liveHttpBadContentFailsWithoutPersistence()
    {
        auto fake=std::make_unique<FakeUploadTransport>();
        fake->responses={{200,QByteArray("<html>upstream error</html>"),QByteArray("text/html"),{}}};
        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));QVERIFY(temporary.isValid());
        auto value=context(temporary.path()+QStringLiteral("/MachomeHub/data/upload"));value.settings.insert("live_reads_enabled",true);value.settings.insert("collectors",QJsonObject{{"eastmoney-nav",QJsonObject{{"enabled",true},{"url","https://fixture.invalid/nav"},{"attempts",1},{"symbols",QJsonArray{"SZ159518"}}}}});
        UploadEngine engine;engine.setHttpTransportForTest(std::move(fake));QSignalSpy commands(&engine,&UploadEngine::commandFinished);engine.initialize(value);engine.setNowForTest(shanghai(2026,9,4,5,0));engine.submitCommand("set_operating_mode",{{"mode","weekend_test"}},"mode");QVERIFY(commands.takeLast().at(1).toBool());engine.start();engine.submitCommand("upload_run_job",{{"job_id","eastmoney-nav"},{"idempotency_key","bad-live-nav"}},"bad-live-nav");QVERIFY(!commands.takeLast().at(1).toBool());
        const QString connection="bad-content-check";QSqlDatabase db=QSqlDatabase::addDatabase("QSQLITE",connection);db.setDatabaseName(engine.databasePath());QVERIFY(db.open());QSqlQuery q(db);QVERIFY(q.exec("SELECT count(*) FROM net_values"));QVERIFY(q.next());QCOMPARE(q.value(0).toInt(),0);db.close();db={};QSqlDatabase::removeDatabase(connection);engine.stop();
    }

    void closeJobsPersistEligibleNativeQuotes()
    {
        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(temporary.isValid());
        UploadEngine engine;
        QSignalSpy commands(&engine, &UploadEngine::commandFinished);
        engine.initialize(context(temporary.path() + QStringLiteral("/MachomeHub/data/upload")));
        engine.setNowForTest(shanghai(2026, 9, 4, 15, 10));
        engine.start();
        engine.submitCommand(QStringLiteral("upload_ingest_quote"),
            {{QStringLiteral("quote"), QJsonObject{
                 {QStringLiteral("symbol"), QStringLiteral("SH513100")},
                 {QStringLiteral("price"), 1.234},
                 {QStringLiteral("source"), QStringLiteral("sina")},
                 {QStringLiteral("observed_at"), QStringLiteral("2026-09-04T15:00:00+08:00")}}}},
            QStringLiteral("cn-quote"));
        QVERIFY(commands.takeLast().at(1).toBool());
        engine.submitCommand(QStringLiteral("upload_run_job"),
            {{QStringLiteral("job_id"), QStringLiteral("cn-close")},
             {QStringLiteral("idempotency_key"), QStringLiteral("2026-09-04-close-test")}},
            QStringLiteral("cn-close"));
        QVERIFY(commands.takeLast().at(1).toBool());
        const QJsonArray prices = engine.snapshot().value(QStringLiteral("history"))
            .toObject().value(QStringLiteral("daily_prices")).toArray();
        QCOMPARE(prices.size(), 1);
        QCOMPARE(prices.first().toObject().value(QStringLiteral("symbol")).toString(),
                 QStringLiteral("SH513100"));
        QCOMPARE(prices.first().toObject().value(QStringLiteral("date")).toString(),
                 QStringLiteral("2026-09-04"));
        engine.stop();
    }

    void repositoryCommandsAreRecordOnlyAndAckIsDigestBound()
    {
        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(temporary.isValid());
        const QString root = temporary.path() + QStringLiteral("/MachomeHub/data/upload");
        UploadEngine engine;
        QSignalSpy commands(&engine, &UploadEngine::commandFinished);
        engine.initialize(context(root));
        engine.setNowForTest(shanghai(2026, 9, 4, 9, 37));
        engine.start();
        QVERIFY(engine.snapshot().value(QStringLiteral("ready")).toBool());
        QVERIFY(engine.isRecordOnly());
        QVERIFY(engine.databasePath().startsWith(root));

        engine.submitCommand(QStringLiteral("upload_ingest_valuation"),
            {{QStringLiteral("input"), fixture().value(QStringLiteral("input"))}},
            QStringLiteral("valuation-1"));
        QCOMPARE(commands.count(), 1);
        QVERIFY(commands.takeFirst().at(1).toBool());

        engine.submitCommand(QStringLiteral("upload_ingest_dataset"),
            {{QStringLiteral("dataset"), QJsonObject{
                 {QStringLiteral("kind"), QStringLiteral("share_history")},
                 {QStringLiteral("symbol"), QStringLiteral("SZ159518")},
                 {QStringLiteral("date"), QStringLiteral("2026-09-03")},
                 {QStringLiteral("shares_10k"), 12345.0},
                 {QStringLiteral("source"), QStringLiteral("sanitized-szse")}}}},
            QStringLiteral("dataset-1"));
        QVERIFY(commands.takeFirst().at(1).toBool());
        QCOMPARE(engine.snapshot().value(QStringLiteral("history")).toObject()
                     .value(QStringLiteral("share_history")).toArray().size(), 1);

        engine.submitCommand(QStringLiteral("upload_run_job"),
            {{QStringLiteral("job_id"), QStringLiteral("intraday-rebuild")},
             {QStringLiteral("idempotency_key"), QStringLiteral("manual-fixture-1")}},
            QStringLiteral("run-1"));
        QCOMPARE(commands.count(), 1);
        const auto result = commands.takeFirst();
        QVERIFY(result.at(1).toBool());
        const QJsonObject details = result.at(3).toJsonObject();
        QCOMPARE(details.value(QStringLiteral("sink_mode")).toString(),
                 QStringLiteral("record_only"));
        const QString digest = details.value(QStringLiteral("payload_sha256")).toString();
        QCOMPARE(digest.size(), 64);

        const QJsonArray records = engine.snapshot()
            .value(QStringLiteral("upload_records")).toArray();
        QVERIFY(!records.isEmpty());
        QString recordId;
        for (const auto &value : records) {
            const QJsonObject record = value.toObject();
            if (record.value(QStringLiteral("job_id")).toString() == QStringLiteral("intraday-rebuild")
                && record.value(QStringLiteral("idempotency_key")).toString()
                    == QStringLiteral("manual-fixture-1")) {
                recordId = record.value(QStringLiteral("record_id")).toString();
                break;
            }
        }
        QVERIFY(!recordId.isEmpty());
        engine.submitCommand(QStringLiteral("upload_ack"),
            {{QStringLiteral("record_id"), recordId},
             {QStringLiteral("sha256"), QString(64, u'0')}}, QStringLiteral("ack-bad"));
        QVERIFY(!commands.takeFirst().at(1).toBool());
        engine.submitCommand(QStringLiteral("upload_ack"),
            {{QStringLiteral("record_id"), recordId},
             {QStringLiteral("sha256"), digest}}, QStringLiteral("ack-good"));
        QVERIFY(commands.takeFirst().at(1).toBool());
        engine.stop();
    }

    void recordOnlyBatchKeepsProductionWireAndValidatesAck()
    {
        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(temporary.isValid());
        const QString root = temporary.path() + QStringLiteral("/MachomeHub/data/upload");
        const QString now = QStringLiteral("2026-09-04T10:00:00+08:00");
        const QJsonObject input{
            {QStringLiteral("schema_version"), 1},
            {QStringLiteral("symbol"), QStringLiteral("SZ159518")},
            {QStringLiteral("model_version"), QStringLiteral("private.total-basket.xop-cfets-pcf.v1")},
            {QStringLiteral("source"), QStringLiteral("mac-home-private-xop-family-uploader")},
            {QStringLiteral("generated_at"), now},
            {QStringLiteral("ib"), QJsonObject{{QStringLiteral("symbol"), QStringLiteral("XOP")},
                 {QStringLiteral("bid"), 40.0}, {QStringLiteral("ask"), 40.1},
                 {QStringLiteral("observed_at"), now}, {QStringLiteral("stream_checked_at"), now},
                 {QStringLiteral("market_data_type"), QStringLiteral("Live")},
                 {QStringLiteral("source"), QStringLiteral("machome-native-ibkr-bridge")}}},
            {QStringLiteral("fx"), QJsonObject{{QStringLiteral("pair"), QStringLiteral("USD/CNY")},
                 {QStringLiteral("rate"), 7.2}, {QStringLiteral("trading_day"), QStringLiteral("2026-09-04")},
                 {QStringLiteral("quote_time"), QStringLiteral("10:00")},
                 {QStringLiteral("source"), QStringLiteral("CFETS_REFERENCE_RATE")},
                 {QStringLiteral("fetched_at"), now}}},
            {QStringLiteral("pcf"), QJsonObject{{QStringLiteral("trading_day"), QStringLiteral("2026-09-04")},
                 {QStringLiteral("security_id"), QStringLiteral("159518")},
                 {QStringLiteral("creation_redemption_unit"), 1000000.0},
                 {QStringLiteral("estimate_cash_component_cny"), 100000.0},
                 {QStringLiteral("component_count"), 51},
                 {QStringLiteral("source_url"), QStringLiteral("https://fixture.invalid/pcf")},
                 {QStringLiteral("sha256"), QString(64, u'a')},
                 {QStringLiteral("redemption"), QStringLiteral("Y")}}}};
        UploadEngine engine;
        QSignalSpy commands(&engine, &UploadEngine::commandFinished);
        engine.initialize(context(root));
        engine.setNowForTest(shanghai(2026, 9, 4, 10, 0));
        engine.start();
        engine.submitCommand(QStringLiteral("upload_ingest_valuation"),
                             {{QStringLiteral("input"), input}}, QStringLiteral("ingest"));
        QVERIFY(commands.takeLast().at(1).toBool());
        engine.submitCommand(QStringLiteral("upload_run_job"),
            {{QStringLiteral("job_id"), QStringLiteral("xop-family")},
             {QStringLiteral("idempotency_key"), QStringLiteral("xop-golden-batch")}},
            QStringLiteral("run"));
        const auto run = commands.takeLast();
        QVERIFY(run.at(1).toBool());
        const QString digest = run.at(3).toJsonObject().value(QStringLiteral("payload_sha256")).toString();
        QString recordId;
        for (const auto &item : engine.snapshot().value(QStringLiteral("upload_records")).toArray()) {
            const QJsonObject record = item.toObject();
            if (record.value(QStringLiteral("idempotency_key")).toString()
                == QStringLiteral("xop-golden-batch"))
                recordId = record.value(QStringLiteral("record_id")).toString();
        }
        QVERIFY(!recordId.isEmpty());

        const QString connection = QStringLiteral("upload-wire-%1").arg(recordId);
        QSqlDatabase db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connection);
        db.setDatabaseName(engine.databasePath());
        QVERIFY(db.open());
        QSqlQuery query(db);
        query.prepare(QStringLiteral("SELECT payload_json FROM upload_records WHERE record_id=?"));
        query.addBindValue(recordId);
        QVERIFY(query.exec());
        QVERIFY(query.next());
        const QJsonObject payload = QJsonDocument::fromJson(query.value(0).toByteArray()).object();
        const QJsonObject request = payload.value(QStringLiteral("request")).toObject();
        QCOMPARE(request.value(QStringLiteral("path")).toString(),
                 QStringLiteral("/api/v1/private/inputs/batch"));
        QCOMPARE(request.value(QStringLiteral("content_encoding")).toString(), QStringLiteral("gzip"));
        const QJsonObject body = request.value(QStringLiteral("body")).toObject();
        QCOMPARE(body.value(QStringLiteral("batch_id")).toString(), QStringLiteral("xop-golden-batch"));
        QVERIFY(body.value(QStringLiteral("inputs")).toArray().first().toObject()
                    .contains(QStringLiteral("ib")));
        db.close();
        db = {};
        QSqlDatabase::removeDatabase(connection);

        engine.submitCommand(QStringLiteral("upload_ack"),
            {{QStringLiteral("record_id"), recordId}, {QStringLiteral("sha256"), digest},
             {QStringLiteral("response"), QJsonObject{{QStringLiteral("batch_id"), QStringLiteral("wrong")},
                  {QStringLiteral("accepted"), QJsonArray{QStringLiteral("SZ159518")}},
                  {QStringLiteral("rejected"), QJsonObject{}}}}}, QStringLiteral("ack-wrong"));
        QVERIFY(!commands.takeLast().at(1).toBool());
        engine.submitCommand(QStringLiteral("upload_ack"),
            {{QStringLiteral("record_id"), recordId}, {QStringLiteral("sha256"), digest},
             {QStringLiteral("response"), QJsonObject{{QStringLiteral("batch_id"), QStringLiteral("xop-golden-batch")},
                  {QStringLiteral("accepted"), QJsonArray{QStringLiteral("SZ159518")}},
                  {QStringLiteral("rejected"), QJsonObject{}}}}}, QStringLiteral("ack-good"));
        QVERIFY(commands.takeLast().at(1).toBool());

        engine.submitCommand(QStringLiteral("upload_ingest_quote"),
            {{QStringLiteral("quote"), QJsonObject{
                 {QStringLiteral("symbol"), QStringLiteral("SZ159518")},
                 {QStringLiteral("price"), 1.5}, {QStringLiteral("bid"), 1.49},
                 {QStringLiteral("ask"), 1.51}, {QStringLiteral("source"), QStringLiteral("sina")},
                 {QStringLiteral("observed_at"), now}}}}, QStringLiteral("quote"));
        QVERIFY(commands.takeLast().at(1).toBool());
        engine.submitCommand(QStringLiteral("upload_run_job"),
            {{QStringLiteral("job_id"), QStringLiteral("sina-public")},
             {QStringLiteral("idempotency_key"), QStringLiteral("sina-golden-batch")}},
            QStringLiteral("sina-run"));
        const QJsonObject sinaDetails = commands.takeLast().at(3).toJsonObject();
        const QString sinaDigest = sinaDetails.value(QStringLiteral("payload_sha256")).toString();
        QString sinaRecord;
        for (const auto &item : engine.snapshot().value(QStringLiteral("upload_records")).toArray()) {
            const QJsonObject record = item.toObject();
            if (record.value(QStringLiteral("idempotency_key")).toString()
                == QStringLiteral("sina-golden-batch"))
                sinaRecord = record.value(QStringLiteral("record_id")).toString();
        }
        QVERIFY(!sinaRecord.isEmpty());
        engine.submitCommand(QStringLiteral("upload_ack"),
            {{QStringLiteral("record_id"), sinaRecord}, {QStringLiteral("sha256"), sinaDigest},
             {QStringLiteral("response"), QJsonObject{{QStringLiteral("accepted"), 0},
                  {QStringLiteral("enabled"), true}}}}, QStringLiteral("sina-ack-wrong"));
        QVERIFY(!commands.takeLast().at(1).toBool());
        engine.submitCommand(QStringLiteral("upload_ack"),
            {{QStringLiteral("record_id"), sinaRecord}, {QStringLiteral("sha256"), sinaDigest},
             {QStringLiteral("response"), QJsonObject{{QStringLiteral("accepted"), 1},
                  {QStringLiteral("enabled"), true}}}}, QStringLiteral("sina-ack-good"));
        QVERIFY(commands.takeLast().at(1).toBool());
        engine.stop();
    }

    void legacyStartupCatchupIsIdempotentAndPreservesLaterDailyRun()
    {
        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(temporary.isValid());
        auto fake = std::make_unique<FakeUploadTransport>();
        FakeUploadTransport *observer = fake.get();
        const QByteArray nav = QByteArray(
            R"({"ErrCode":0,"Data":{"LSJZList":[{"FSRQ":"2026-09-03","DWJZ":"1.2345"}]}})");
        fake->responses = {{200, nav, QByteArray("application/json"), {}},
                           {200, nav, QByteArray("application/json"), {}}};
        auto value = context(temporary.path() + QStringLiteral("/MachomeHub/data/upload"));
        value.settings.insert(QStringLiteral("startup_catchup_enabled"), true);
        value.settings.insert(QStringLiteral("live_reads_enabled"), true);
        value.settings.insert(QStringLiteral("collectors"), QJsonObject{
            {QStringLiteral("eastmoney-nav"), QJsonObject{
                 {QStringLiteral("enabled"), true},
                 {QStringLiteral("url"), QStringLiteral("https://fixture.invalid/nav")},
                 {QStringLiteral("attempts"), 1},
                 {QStringLiteral("symbols"), QJsonArray{QStringLiteral("SZ159518")}}}}});

        UploadEngine engine;
        engine.setHttpTransportForTest(std::move(fake));
        engine.initialize(value);
        engine.setNowForTest(shanghai(2026, 9, 4, 7, 0));
        engine.start();
        QCOMPARE(observer->requests, 1);
        engine.stop();
        engine.start();
        QCOMPARE(observer->requests, 1);

        engine.setNowForTest(shanghai(2026, 9, 4, 22, 30));
        engine.evaluateSchedulesForTest();
        QCOMPARE(observer->requests, 2);
        int startupRuns = 0;
        int scheduledRuns = 0;
        for (const auto &item : engine.snapshot().value(QStringLiteral("job_runs")).toArray()) {
            const QJsonObject run = item.toObject();
            if (run.value(QStringLiteral("job_id")).toString()
                != QStringLiteral("eastmoney-nav"))
                continue;
            const QString key = run.value(QStringLiteral("idempotency_key")).toString();
            if (key == QStringLiteral("startup:2026-09-04")) ++startupRuns;
            if (key == QStringLiteral("2026-09-04")) ++scheduledRuns;
        }
        QCOMPARE(startupRuns, 1);
        QCOMPARE(scheduledRuns, 1);
        engine.stop();
    }

    void freshPurchaseCacheSuppressesStartupScheduledFetch()
    {
        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(temporary.isValid());
        auto fake = std::make_unique<FakeUploadTransport>();
        FakeUploadTransport *observer = fake.get();
        fake->responses = {{500, QByteArray("must-not-run"), QByteArray("text/plain"), {}}};
        auto value = context(temporary.path() + QStringLiteral("/MachomeHub/data/upload"));
        value.settings.insert(QStringLiteral("startup_catchup_enabled"), true);
        value.settings.insert(QStringLiteral("live_reads_enabled"), true);
        value.settings.insert(QStringLiteral("collectors"), QJsonObject{
            {QStringLiteral("purchase-status"), QJsonObject{
                 {QStringLiteral("enabled"), true},
                 {QStringLiteral("url"), QStringLiteral("https://fixture.invalid/purchase")},
                 {QStringLiteral("attempts"), 1},
                 {QStringLiteral("symbols"), QJsonArray{QStringLiteral("SZ159518")}}}}});
        UploadEngine engine;
        engine.setHttpTransportForTest(std::move(fake));
        engine.initialize(value);

        const QString connection = QStringLiteral("purchase-fresh-check");
        QSqlDatabase db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connection);
        db.setDatabaseName(engine.databasePath());
        QVERIFY(db.open());
        QSqlQuery insert(db);
        insert.prepare(QStringLiteral(
            "INSERT INTO purchase_status(symbol,status_date,status,daily_limit_yuan,source,payload_json) "
            "VALUES(?,?,?,?,?,?)"));
        insert.addBindValue(QStringLiteral("SZ159518"));
        insert.addBindValue(QStringLiteral("2026-09-04"));
        insert.addBindValue(QStringLiteral("open"));
        insert.addBindValue(5000.0);
        insert.addBindValue(QStringLiteral("sanitized-cache"));
        insert.addBindValue(QStringLiteral("{}"));
        QVERIFY(insert.exec());
        db.close();
        db = {};
        QSqlDatabase::removeDatabase(connection);

        engine.setNowForTest(shanghai(2026, 9, 4, 8, 5));
        engine.start();
        QCOMPARE(observer->requests, 0);
        bool satisfied = false;
        for (const auto &item : engine.snapshot().value(QStringLiteral("jobs")).toArray()) {
            const QJsonObject row = item.toObject();
            if (row.value(QStringLiteral("id")).toString()
                    == QStringLiteral("purchase-status")) {
                satisfied = row.value(QStringLiteral("stage")).toString()
                    == QStringLiteral("already_satisfied");
            }
        }
        QVERIFY(satisfied);
        engine.stop();
    }

    void calendarCloseTimerRunsButWeekendPersistenceIsSkipped()
    {
        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(temporary.isValid());
        UploadEngine engine;
        QSignalSpy commands(&engine, &UploadEngine::commandFinished);
        engine.initialize(context(temporary.path()
                                  + QStringLiteral("/MachomeHub/data/upload")));
        engine.setNowForTest(shanghai(2026, 9, 5, 15, 10));
        engine.start();
        engine.submitCommand(QStringLiteral("upload_ingest_quote"),
            {{QStringLiteral("quote"), QJsonObject{
                 {QStringLiteral("symbol"), QStringLiteral("SH513100")},
                 {QStringLiteral("price"), 1.25},
                 {QStringLiteral("bid"), 1.24},
                 {QStringLiteral("ask"), 1.26},
                 {QStringLiteral("source"), QStringLiteral("sina")},
                 {QStringLiteral("observed_at"),
                  QStringLiteral("2026-09-05T15:10:00+08:00")}}}},
            QStringLiteral("weekend-quote"));
        QVERIFY(commands.takeLast().at(1).toBool());
        engine.submitCommand(QStringLiteral("upload_run_job"),
            {{QStringLiteral("job_id"), QStringLiteral("cn-close")},
             {QStringLiteral("idempotency_key"), QStringLiteral("weekend-close-manual")}},
            QStringLiteral("weekend-close"));
        QVERIFY(commands.takeLast().at(1).toBool());
        QCOMPARE(engine.snapshot().value(QStringLiteral("history")).toObject()
                     .value(QStringLiteral("daily_prices")).toArray().size(), 0);
        engine.stop();
    }

    void invalidExternalHelperFailsClosedWithoutChildProcess()
    {
        QTemporaryDir temporary(QStringLiteral("/private/tmp/machome-upload-test-XXXXXX"));
        QVERIFY(temporary.isValid());
        const QString root = temporary.path() + QStringLiteral("/MachomeHub/data/upload");
        auto value = context(root);
        value.settings.insert(QStringLiteral("ibkr"),
            QJsonObject{{QStringLiteral("enabled"), true},
                        {QStringLiteral("program"), QStringLiteral("/bin/true")},
                        {QStringLiteral("config"), root + QStringLiteral("/outside.json")}});
        UploadEngine engine;
        engine.initialize(value);
        engine.start();
        const QJsonObject status = engine.snapshot();
        QCOMPARE(status.value(QStringLiteral("state")).toString(), QStringLiteral("degraded"));
        QVERIFY(!status.value(QStringLiteral("last_error")).toString().isEmpty());
        QVERIFY(!status.value(QStringLiteral("ibkr")).toObject()
                     .value(QStringLiteral("running")).toBool());
#if MACHOME_NATIVE_IBKR_BRIDGE_AVAILABLE
        QVERIFY(status.value(QStringLiteral("ibkr")).toObject()
                    .value(QStringLiteral("compiled")).toBool());
#else
        QVERIFY(!status.value(QStringLiteral("ibkr")).toObject()
                     .value(QStringLiteral("compiled")).toBool(true));
        QVERIFY(status.value(QStringLiteral("last_error")).toString()
                    .contains(QStringLiteral("未包含 IBKR C++ SDK helper")));
#endif
        engine.stop();
    }
};

QTEST_GUILESS_MAIN(UploadEngineTest)
#include "tst_upload_engine.moc"
