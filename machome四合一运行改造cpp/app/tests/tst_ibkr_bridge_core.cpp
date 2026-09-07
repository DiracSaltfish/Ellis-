#include "modules/ibkr/IbkrBridgeCore.h"

#include <QFile>
#include <QJsonDocument>
#include <QTemporaryDir>
#include <QtTest>

#include <limits>

class IbkrBridgeCoreTest final : public QObject {
    Q_OBJECT

private slots:
    void loadsStrictConfig();
    void rejectsRemoteTwsAndDuplicateIds();
    void rejectsUnknownFieldsAndInvalidTypes();
    void matchesStandaloneConnectionWindow();
    void supportsCrossMidnightConnectionWindow();
    void normalizesQuotesAndTracksFreshness();
    void validatesAndLeasesDynamicSubscriptions();
};

namespace {
QJsonObject validConfig(const QString &root)
{
    return {
        {QStringLiteral("schema_version"), 1},
        {QStringLiteral("socket_path"), root + QStringLiteral("/runtime/ibkr.sock")},
        {QStringLiteral("health_file"), root + QStringLiteral("/health/ibkr.json")},
        {QStringLiteral("tws"), QJsonObject{
             {QStringLiteral("host"), QStringLiteral("127.0.0.1")},
             {QStringLiteral("port"), 7497},
             {QStringLiteral("client_id"), 41},
             {QStringLiteral("market_data_type"), 1},
             {QStringLiteral("heartbeat_interval_ms"), 5'000},
             {QStringLiteral("heartbeat_stale_ms"), 20'000}}},
        {QStringLiteral("connection_schedule"), QJsonObject{
             {QStringLiteral("enabled"), true},
             {QStringLiteral("timezone"), QStringLiteral("Asia/Shanghai")},
             {QStringLiteral("weekdays"), QJsonArray{1, 2, 3, 4, 5}},
             {QStringLiteral("start_time"), QStringLiteral("09:00")},
             {QStringLiteral("stop_time"), QStringLiteral("15:06")}}},
        {QStringLiteral("subscriptions"), QJsonArray{
             QJsonObject{
                 {QStringLiteral("id"), QStringLiteral("XOP.SMART")},
                 {QStringLiteral("symbol"), QStringLiteral("xop")},
                 {QStringLiteral("security_type"), QStringLiteral("stk")},
                 {QStringLiteral("exchange"), QStringLiteral("smart")},
                 {QStringLiteral("primary_exchange"), QStringLiteral("arca")},
                 {QStringLiteral("currency"), QStringLiteral("usd")},
                 {QStringLiteral("generic_ticks"), QStringLiteral("236")}}}},
    };
}

bool writeJson(const QString &path, const QJsonObject &object)
{
    QFile file(path);
    return file.open(QIODevice::WriteOnly)
        && file.write(QJsonDocument(object).toJson(QJsonDocument::Compact)) > 0;
}
}

void IbkrBridgeCoreTest::loadsStrictConfig()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = directory.filePath(QStringLiteral("config.json"));
    QVERIFY(writeJson(path, validConfig(directory.path())));

    machome::ibkr::BridgeConfig config;
    QString error;
    QVERIFY2(machome::ibkr::BridgeConfig::loadFile(path, &config, &error), qPrintable(error));
    QCOMPARE(config.twsPort, 7497);
    QCOMPARE(config.clientId, 41);
    QCOMPARE(config.subscriptions.size(), 1);
    QCOMPARE(config.subscriptions.first().symbol, QStringLiteral("XOP"));
    QCOMPARE(config.subscriptions.first().primaryExchange, QStringLiteral("ARCA"));
    QVERIFY(config.connectionSchedule.enabled);
    QCOMPARE(config.connectionSchedule.stop, QTime(15, 6));
    QCOMPARE(config.safeSummary().value(QStringLiteral("protocol")).toString(),
             QStringLiteral("machome.ibkr.quote.v1"));
}

void IbkrBridgeCoreTest::rejectsRemoteTwsAndDuplicateIds()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    QString error;
    machome::ibkr::BridgeConfig config;

    QJsonObject remote = validConfig(directory.path());
    QJsonObject tws = remote.value(QStringLiteral("tws")).toObject();
    tws.insert(QStringLiteral("host"), QStringLiteral("192.168.1.2"));
    remote.insert(QStringLiteral("tws"), tws);
    const QString remotePath = directory.filePath(QStringLiteral("remote.json"));
    QVERIFY(writeJson(remotePath, remote));
    QVERIFY(!machome::ibkr::BridgeConfig::loadFile(remotePath, &config, &error));
    QVERIFY(error.contains(QStringLiteral("allowed_private_hosts")));

    QJsonObject approvedRemote = remote;
    tws.insert(QStringLiteral("allowed_private_hosts"),
               QJsonArray{QStringLiteral("192.168.1.2")});
    approvedRemote.insert(QStringLiteral("tws"), tws);
    const QString approvedRemotePath = directory.filePath(QStringLiteral("approved-remote.json"));
    QVERIFY(writeJson(approvedRemotePath, approvedRemote));
    QVERIFY2(machome::ibkr::BridgeConfig::loadFile(approvedRemotePath, &config, &error),
             qPrintable(error));
    QCOMPARE(config.twsHost, QStringLiteral("192.168.1.2"));

    QJsonObject publicRemote = approvedRemote;
    tws.insert(QStringLiteral("host"), QStringLiteral("8.8.8.8"));
    tws.insert(QStringLiteral("allowed_private_hosts"), QJsonArray{QStringLiteral("8.8.8.8")});
    publicRemote.insert(QStringLiteral("tws"), tws);
    const QString publicRemotePath = directory.filePath(QStringLiteral("public-remote.json"));
    QVERIFY(writeJson(publicRemotePath, publicRemote));
    QVERIFY(!machome::ibkr::BridgeConfig::loadFile(publicRemotePath, &config, &error));
    QVERIFY(error.contains(QStringLiteral("私网")));

    QJsonObject duplicate = validConfig(directory.path());
    QJsonArray subscriptions = duplicate.value(QStringLiteral("subscriptions")).toArray();
    subscriptions.append(subscriptions.first());
    duplicate.insert(QStringLiteral("subscriptions"), subscriptions);
    const QString duplicatePath = directory.filePath(QStringLiteral("duplicate.json"));
    QVERIFY(writeJson(duplicatePath, duplicate));
    QVERIFY(!machome::ibkr::BridgeConfig::loadFile(duplicatePath, &config, &error));
    QVERIFY(error.contains(QStringLiteral("重复")));
}

void IbkrBridgeCoreTest::rejectsUnknownFieldsAndInvalidTypes()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    QString error;
    machome::ibkr::BridgeConfig config;

    QJsonObject unknown = validConfig(directory.path());
    unknown.insert(QStringLiteral("typo_field"), true);
    const QString unknownPath = directory.filePath(QStringLiteral("unknown.json"));
    QVERIFY(writeJson(unknownPath, unknown));
    QVERIFY(!machome::ibkr::BridgeConfig::loadFile(unknownPath, &config, &error));
    QVERIFY(error.contains(QStringLiteral("未知字段")));

    QJsonObject badLimits = validConfig(directory.path());
    badLimits.insert(QStringLiteral("limits"), QStringLiteral("unbounded"));
    const QString badLimitsPath = directory.filePath(QStringLiteral("bad-limits.json"));
    QVERIFY(writeJson(badLimitsPath, badLimits));
    QVERIFY(!machome::ibkr::BridgeConfig::loadFile(badLimitsPath, &config, &error));
    QVERIFY(error.contains(QStringLiteral("limits")));

    QJsonObject badSchedule = validConfig(directory.path());
    QJsonObject schedule = badSchedule.value(QStringLiteral("connection_schedule")).toObject();
    schedule.insert(QStringLiteral("weekdays"), QJsonArray{1, 1});
    badSchedule.insert(QStringLiteral("connection_schedule"), schedule);
    const QString badSchedulePath = directory.filePath(QStringLiteral("bad-schedule.json"));
    QVERIFY(writeJson(badSchedulePath, badSchedule));
    QVERIFY(!machome::ibkr::BridgeConfig::loadFile(badSchedulePath, &config, &error));
    QVERIFY(error.contains(QStringLiteral("weekdays")));
}

void IbkrBridgeCoreTest::matchesStandaloneConnectionWindow()
{
    machome::ibkr::ConnectionSchedule schedule;
    schedule.enabled = true;
    const QTimeZone shanghai(QByteArrayLiteral("Asia/Shanghai"));

    const auto before = schedule.evaluate(
        QDateTime(QDate(2026, 9, 4), QTime(8, 59, 59), shanghai));
    QVERIFY(!before.active);
    QCOMPARE(before.nextTransition.time(), QTime(9, 0));
    QVERIFY(schedule.evaluate(
        QDateTime(QDate(2026, 9, 4), QTime(9, 0), shanghai)).active);
    QVERIFY(schedule.evaluate(
        QDateTime(QDate(2026, 9, 4), QTime(15, 5, 59), shanghai)).active);

    const auto closed = schedule.evaluate(
        QDateTime(QDate(2026, 9, 4), QTime(15, 6), shanghai));
    QVERIFY(!closed.active);
    QCOMPARE(closed.nextTransition.date(), QDate(2026, 9, 7));
    QCOMPARE(closed.nextTransition.time(), QTime(9, 0));
    QVERIFY(!schedule.evaluate(
        QDateTime(QDate(2026, 9, 5), QTime(12, 0), shanghai)).active);
}

void IbkrBridgeCoreTest::supportsCrossMidnightConnectionWindow()
{
    machome::ibkr::ConnectionSchedule schedule;
    schedule.enabled = true;
    schedule.start = QTime(21, 0);
    schedule.stop = QTime(2, 30);
    const QTimeZone shanghai(QByteArrayLiteral("Asia/Shanghai"));
    QVERIFY(schedule.evaluate(
        QDateTime(QDate(2026, 9, 4), QTime(21, 0), shanghai)).active);
    QVERIFY(schedule.evaluate(
        QDateTime(QDate(2026, 9, 5), QTime(2, 29, 59), shanghai)).active);
    QVERIFY(!schedule.evaluate(
        QDateTime(QDate(2026, 9, 5), QTime(2, 30), shanghai)).active);
}

void IbkrBridgeCoreTest::normalizesQuotesAndTracksFreshness()
{
    machome::ibkr::ContractSpec contract;
    contract.id = QStringLiteral("XOP.SMART");
    contract.symbol = QStringLiteral("XOP");
    contract.securityType = QStringLiteral("STK");
    contract.exchange = QStringLiteral("SMART");
    contract.currency = QStringLiteral("USD");

    machome::ibkr::QuoteBook book;
    book.reset({contract}, 100);
    QCOMPARE(book.tickerIdFor(contract.id), 100);
    QVERIFY(book.updatePrice(100, machome::ibkr::QuoteBook::PriceField::Bid, 123.45));
    QVERIFY(book.updateSize(100, machome::ibkr::QuoteBook::SizeField::Bid, QStringLiteral("100.5")));
    QVERIFY(!book.updatePrice(100, machome::ibkr::QuoteBook::PriceField::Ask,
                              std::numeric_limits<double>::infinity()));
    QVERIFY(!book.updateSize(100, machome::ibkr::QuoteBook::SizeField::Ask,
                             QStringLiteral("-1")));
    QVERIFY(book.updateMarketDataType(100, 1));

    const QJsonObject quote = book.quote(contract.id, 60'000);
    QCOMPARE(quote.value(QStringLiteral("bid")).toString(), QStringLiteral("123.45"));
    QCOMPARE(quote.value(QStringLiteral("bid_size")).toString(), QStringLiteral("100.5"));
    QVERIFY(quote.value(QStringLiteral("ask")).isNull());
    QVERIFY(quote.value(QStringLiteral("fresh")).toBool());
    QCOMPARE(book.updateCount(), 2);
}

void IbkrBridgeCoreTest::validatesAndLeasesDynamicSubscriptions()
{
    QJsonObject raw{
        {QStringLiteral("symbol"), QStringLiteral("aapl")},
        {QStringLiteral("security_type"), QStringLiteral("stk")},
        {QStringLiteral("exchange"), QStringLiteral("smart")},
        {QStringLiteral("primary_exchange"), QStringLiteral("nasdaq")},
        {QStringLiteral("currency"), QStringLiteral("usd")},
        {QStringLiteral("generic_ticks"), QStringLiteral("")},
    };
    machome::ibkr::ContractSpec contract;
    QString error;
    QVERIFY2(machome::ibkr::ContractSpec::fromJson(
                 raw, QStringLiteral("STK:USD:AAPL:SMART"), &contract, &error),
             qPrintable(error));
    QCOMPARE(contract.symbol, QStringLiteral("AAPL"));
    QCOMPARE(contract.primaryExchange, QStringLiteral("NASDAQ"));

    machome::ibkr::QuoteBook book;
    book.reset({});
    QCOMPARE(book.registerSubscription(contract, 1'000'000),
             machome::ibkr::QuoteBook::RegisterResult::Added);
    QCOMPARE(book.registerSubscription(contract, 1'000'001),
             machome::ibkr::QuoteBook::RegisterResult::Existing);
    QCOMPARE(book.subscriptionCount(), 1);

    auto conflicting = contract;
    conflicting.exchange = QStringLiteral("OVERNIGHT");
    QCOMPARE(book.registerSubscription(conflicting, 1'000'002),
             machome::ibkr::QuoteBook::RegisterResult::Conflict);
    int tickerId = -1;
    machome::ibkr::ContractSpec removed;
    QVERIFY(book.removeSubscription(contract.id, &tickerId, &removed));
    QCOMPARE(tickerId, 1'000'000);
    QVERIFY(removed.marketDataEquivalent(contract));
    QCOMPARE(book.subscriptionCount(), 0);

    raw.insert(QStringLiteral("unexpected"), true);
    QVERIFY(!machome::ibkr::ContractSpec::fromJson(
        raw, QStringLiteral("BAD"), &contract, &error));
    QVERIFY(error.contains(QStringLiteral("未知字段")));
}

QTEST_MAIN(IbkrBridgeCoreTest)
#include "tst_ibkr_bridge_core.moc"
