#include "common/Config.h"
#include "common/FrameCodec.h"
#include "common/JsonUtil.h"

#include <QJsonObject>
#include <QtTest>

namespace {
hub::AppConfig validConfig() {
    hub::AppConfig config;
    config.socketPath = QStringLiteral("/private/tmp/hub-test.sock");
    config.auditDatabase = QStringLiteral("/private/tmp/hub-test.db");
    const QList<QPair<QString, QString>> definitions{
        {QStringLiteral("upload"), QStringLiteral("upload")},
        {QStringLiteral("premium"), QStringLiteral("premium")},
        {QStringLiteral("webull"), QStringLiteral("webull")},
        {QStringLiteral("redemption"), QStringLiteral("realtime")}};
    for (const auto &[id, adapter] : definitions) {
        hub::ModuleConfig module;
        module.id = id;
        module.displayName = id;
        module.adapter = adapter;
        module.allowedActions = {QStringLiteral("refresh")};
        if (id == QStringLiteral("upload")) {
            module.settings = {
                {QStringLiteral("web_base_url"), QStringLiteral("http://127.0.0.1:18080")},
                {QStringLiteral("health_endpoint"), QStringLiteral("/api/v1/health")},
                {QStringLiteral("api_contract"), QStringLiteral("newnavnav-web-v1")},
                {QStringLiteral("workers_expected"), false}};
        } else if (id == QStringLiteral("premium")) {
            module.settings = {
                {QStringLiteral("host"), QStringLiteral("127.0.0.1")},
                {QStringLiteral("summary_port"), 18421},
                {QStringLiteral("l1_port"), 19196},
                {QStringLiteral("summary_protocol"), QStringLiteral("ws-json-v1")},
                {QStringLiteral("l1_protocol"), QStringLiteral("qmt-l1-ndjson-v1")}};
        } else if (id == QStringLiteral("webull")) {
            module.settings = {
                {QStringLiteral("api_base_url"), QStringLiteral("http://127.0.0.1:18765/v2")},
                {QStringLiteral("api_protocol"), 2},
                {QStringLiteral("stream_url"), QStringLiteral("ws://127.0.0.1:18765/v2/stream")}};
        } else {
            module.settings = {
                {QStringLiteral("api_base_url"), QStringLiteral("http://127.0.0.1:16787")},
                {QStringLiteral("stream_url"), QStringLiteral("ws://127.0.0.1:16787/ws/v1/changes")},
                {QStringLiteral("required_protocol"), 1}};
        }
        config.modules.push_back(module);
    }
    return config;
}
} // namespace

class CommonTests final : public QObject {
    Q_OBJECT
private slots:
    void frameRoundTrip() {
        const QJsonObject first{{QStringLiteral("type"), QStringLiteral("hello")},
                                {QStringLiteral("sequence"), 1}};
        const QJsonObject second{{QStringLiteral("type"), QStringLiteral("snapshot")},
                                 {QStringLiteral("ok"), true}};
        const QByteArray bytes = hub::FrameCodec::encode(first) + hub::FrameCodec::encode(second);
        QByteArray buffer;
        QList<QJsonObject> messages;
        QString error;
        QVERIFY(hub::FrameCodec::consume(buffer, bytes.left(3), &messages, &error));
        QCOMPARE(messages.size(), 0);
        QVERIFY(hub::FrameCodec::consume(buffer, bytes.mid(3), &messages, &error));
        QCOMPARE(messages.size(), 2);
        QCOMPARE(messages[0], first);
        QCOMPARE(messages[1], second);
        QVERIFY(buffer.isEmpty());
    }

    void rejectsOversizedFrame() {
        QByteArray bytes;
        bytes.append(char(0x00));
        bytes.append(char(0x20));
        bytes.append(char(0x00));
        bytes.append(char(0x00));
        QByteArray buffer;
        QList<QJsonObject> messages;
        QString error;
        QVERIFY(!hub::FrameCodec::consume(buffer, bytes, &messages, &error, 64 * 1024));
        QVERIFY(!error.isEmpty());
    }

    void acceptsManyFramesAboveAggregateLimit() {
        const QJsonObject message{{QStringLiteral("type"), QStringLiteral("event")},
                                  {QStringLiteral("value"), QStringLiteral("1234567890")}};
        QByteArray bytes;
        for (int index = 0; index < 8; ++index) bytes += hub::FrameCodec::encode(message);
        QVERIFY(bytes.size() > 64);
        QByteArray buffer;
        QList<QJsonObject> messages;
        QString error;
        QVERIFY2(hub::FrameCodec::consume(buffer, bytes, &messages, &error, 64), qPrintable(error));
        QCOMPARE(messages.size(), 8);
        QVERIFY(buffer.isEmpty());
    }

    void redactsNestedSecrets() {
        const QJsonObject input{{QStringLiteral("token"), QStringLiteral("do-not-leak")},
                                {QStringLiteral("nested"), QJsonObject{{QStringLiteral("password"), QStringLiteral("x")},
                                                                        {QStringLiteral("safe"), 7}}}};
        const auto output = hub::redacted(input);
        QCOMPARE(output.value(QStringLiteral("token")).toString(), QStringLiteral("<redacted>"));
        QCOMPARE(output.value(QStringLiteral("nested")).toObject().value(QStringLiteral("safe")).toInt(), 7);
    }

    void validatesExactFourModuleMapping() {
        auto config = validConfig();
        QString error;
        QVERIFY2(config.isValid(&error), qPrintable(error));
        config.modules.last().adapter = QStringLiteral("premium");
        QVERIFY(!config.isValid(&error));
        QVERIFY(error.contains(QStringLiteral("固定 adapter")));
        config = validConfig();
        config.modules.removeLast();
        QVERIFY(!config.isValid(&error));
        QVERIFY(error.contains(QStringLiteral("恰好")));
    }

    void rejectsUnsafeOwnerAndUnapprovedRisk() {
        auto config = validConfig();
        auto &upload = config.modules[0];
        upload.ownership = QStringLiteral("owner");
        upload.controlEnabled = true;
        upload.allowedActions.append(QStringLiteral("start_service"));
        hub::ManagedProcess process;
        process.id = QStringLiteral("unsafe");
        process.program = QStringLiteral("/bin/true");
        upload.managedProcesses.append(process);
        QString error;
        QVERIFY(!config.isValid(&error));
        QVERIFY(error.contains(QStringLiteral("launchd_units")));

        config = validConfig();
        auto &redemption = config.modules[3];
        redemption.ownership = QStringLiteral("logic");
        redemption.controlEnabled = true;
        redemption.allowedActions.append(QStringLiteral("redemption_qmt_order"));
        QVERIFY(!config.isValid(&error));
        QVERIFY(error.contains(QStringLiteral("二阶段审批")));
    }

    void rejectsAdapterEndpointFallbacks_data() {
        QTest::addColumn<int>("moduleIndex");
        QTest::addColumn<QString>("setting");
        QTest::addColumn<QJsonValue>("invalidValue");

        QTest::newRow("upload-missing-url") << 0 << QStringLiteral("web_base_url") << QJsonValue();
        QTest::newRow("upload-remote-host") << 0 << QStringLiteral("web_base_url")
                                             << QJsonValue(QStringLiteral("http://10.0.0.5:8080"));
        QTest::newRow("upload-no-port") << 0 << QStringLiteral("web_base_url")
                                         << QJsonValue(QStringLiteral("http://127.0.0.1"));
        QTest::newRow("premium-zero-port") << 1 << QStringLiteral("summary_port") << QJsonValue(0);
        QTest::newRow("premium-wrapped-port") << 1 << QStringLiteral("l1_port") << QJsonValue(65536);
        QTest::newRow("premium-protocol") << 1 << QStringLiteral("l1_protocol")
                                           << QJsonValue(QStringLiteral("unknown"));
        QTest::newRow("webull-wrong-path") << 2 << QStringLiteral("api_base_url")
                                            << QJsonValue(QStringLiteral("http://127.0.0.1:18765/v1"));
        QTest::newRow("webull-cross-origin-stream") << 2 << QStringLiteral("stream_url")
                                                     << QJsonValue(QStringLiteral("ws://127.0.0.1:18766/v2/stream"));
        QTest::newRow("realtime-missing-port") << 3 << QStringLiteral("api_base_url")
                                                << QJsonValue(QStringLiteral("http://127.0.0.1"));
        QTest::newRow("realtime-unsupported-protocol") << 3 << QStringLiteral("required_protocol")
                                                        << QJsonValue(2);
    }

    void rejectsAdapterEndpointFallbacks() {
        QFETCH(int, moduleIndex);
        QFETCH(QString, setting);
        QFETCH(QJsonValue, invalidValue);
        auto config = validConfig();
        auto settings = config.modules[moduleIndex].settings;
        if (invalidValue.isUndefined()) settings.remove(setting);
        else settings.insert(setting, invalidValue);
        config.modules[moduleIndex].settings = settings;
        QString error;
        QVERIFY2(!config.isValid(&error), "非法 adapter 配置不得被默认端点掩盖");
        QVERIFY2(!error.isEmpty(), "必须返回可操作的 fail-closed 配置错误");
    }
};

QTEST_GUILESS_MAIN(CommonTests)
#include "tst_common.moc"
