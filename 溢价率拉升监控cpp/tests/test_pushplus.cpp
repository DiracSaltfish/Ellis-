#include "server/PushPlusNotifier.h"

#include <QFile>
#include <QJsonDocument>
#include <QSignalSpy>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTemporaryDir>
#include <QTest>

using premium::PushPlusConfig;
using premium::PushPlusNotifier;

namespace {

QJsonObject signal(const QString &symbol, const QString &time, int sequence)
{
    return {{QStringLiteral("type"), QStringLiteral("signal")},
            {QStringLiteral("source_session"), QStringLiteral("session-a")},
            {QStringLiteral("symbol"), symbol},
            {QStringLiteral("name"), symbol == QStringLiteral("520700.SH")
                                             ? QStringLiteral("万家中证红利ETF")
                                             : QStringLiteral("广发上证科创板ETF")},
            {QStringLiteral("occurred_at"), time},
            {QStringLiteral("signal_seq"), sequence},
            {QStringLiteral("model"), QStringLiteral("radar")},
            {QStringLiteral("premium_ppm"), 6332},
            {QStringLiteral("bid1_price_e6"), 1621000}};
}

} // namespace

class PushPlusNotifierTest final : public QObject {
    Q_OBJECT
private Q_SLOTS:
    void dedicatedEnableDoesNotReuseHealthMonitorSwitch()
    {
        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        const QString environmentPath = directory.filePath(QStringLiteral("pushplus.env"));
        QFile environment(environmentPath);
        QVERIFY(environment.open(QIODevice::WriteOnly | QIODevice::Text));
        environment.write("PUSHPLUS_TOKEN=unit-test-token\nPUSHPLUS_MONITOR_ENABLED=false\n");
        environment.close();

        const QJsonObject application{{QStringLiteral("pushplus"),
                                       QJsonObject{{QStringLiteral("enabled"), true},
                                                   {QStringLiteral("env_file"), environmentPath},
                                                   {QStringLiteral("state_file"), directory.filePath(QStringLiteral("state.json"))}}}};
        QString error;
        const PushPlusConfig config = PushPlusNotifier::fromApplicationConfig(application, directory.path(), &error);
        QVERIFY2(error.isEmpty(), qPrintable(error));
        QVERIFY(config.enabled);
        QCOMPARE(config.token, QStringLiteral("unit-test-token"));
    }

    void eventIdentityIncludesSymbolAndOccurrence()
    {
        const QJsonObject first = signal(QStringLiteral("520700.SH"), QStringLiteral("2026-09-01T10:00:00.000+08:00"), 1);
        const QJsonObject second = signal(QStringLiteral("520990.SH"), QStringLiteral("2026-09-01T10:00:00.000+08:00"), 1);
        const QJsonObject third = signal(QStringLiteral("520700.SH"), QStringLiteral("2026-09-01T10:00:01.000+08:00"), 1);
        QVERIFY(PushPlusNotifier::eventKey(first) != PushPlusNotifier::eventKey(second));
        QVERIFY(PushPlusNotifier::eventKey(first) != PushPlusNotifier::eventKey(third));
    }

    void messageBuilderAggregatesSignals()
    {
        const auto message = PushPlusNotifier::buildMessage(
            {signal(QStringLiteral("520700.SH"), QStringLiteral("2026-09-01T10:00:00.000+08:00"), 1),
             signal(QStringLiteral("520990.SH"), QStringLiteral("2026-09-01T10:00:05.000+08:00"), 1)});
        QCOMPARE(message.signalCount, 2);
        QVERIFY(message.title.contains(QStringLiteral("2 条信号")));
        QVERIFY(message.content.contains(QStringLiteral("520700.SH")));
        QVERIFY(message.content.contains(QStringLiteral("520990.SH")));
        QVERIFY(message.content.contains(QStringLiteral("0.6332%")));
    }

    void batchesTwoSignalsIntoOneHttpRequest()
    {
        QTcpServer server;
        QVERIFY(server.listen(QHostAddress::LocalHost, 0));
        QByteArray requestBytes;
        QByteArray requestBody;
        bool responded = false;
        connect(&server, &QTcpServer::newConnection, this, [&] {
            QTcpSocket *socket = server.nextPendingConnection();
            connect(socket, &QTcpSocket::readyRead, socket, [&, socket] {
                requestBytes += socket->readAll();
                const qsizetype headerEnd = requestBytes.indexOf("\r\n\r\n");
                if (headerEnd < 0 || responded) return;
                int contentLength = -1;
                const QList<QByteArray> headers = requestBytes.left(headerEnd).split('\n');
                for (QByteArray header : headers) {
                    header = header.trimmed();
                    if (header.toLower().startsWith("content-length:"))
                        contentLength = header.mid(header.indexOf(':') + 1).trimmed().toInt();
                }
                if (contentLength < 0 || requestBytes.size() - headerEnd - 4 < contentLength) return;
                requestBody = requestBytes.mid(headerEnd + 4, contentLength);
                responded = true;
                const QByteArray responseBody = "{\"code\":200,\"msg\":\"ok\"}";
                const QByteArray response =
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                    + QByteArray::number(responseBody.size()) + "\r\nConnection: close\r\n\r\n" + responseBody;
                socket->write(response);
                socket->disconnectFromHost();
            });
        });

        QTemporaryDir directory;
        QVERIFY(directory.isValid());
        PushPlusConfig config;
        config.enabled = true;
        config.token = QStringLiteral("unit-test-token");
        config.endpoint = QStringLiteral("http://127.0.0.1:%1/send").arg(server.serverPort());
        config.stateFile = directory.filePath(QStringLiteral("state.json"));
        config.batchWindowMs = 25;
        config.requestTimeoutMs = 1'000;
        PushPlusNotifier notifier(config);
        QSignalSpy statusSpy(&notifier, &PushPlusNotifier::statusChanged);
        notifier.start();
        notifier.enqueueSignal(signal(QStringLiteral("520700.SH"), QDateTime::currentDateTime().toString(Qt::ISODateWithMs), 1));
        notifier.enqueueSignal(signal(QStringLiteral("520990.SH"), QDateTime::currentDateTime().addMSecs(1).toString(Qt::ISODateWithMs), 1));

        QTRY_VERIFY_WITH_TIMEOUT(!requestBody.isEmpty(), 2'000);
        QTRY_VERIFY_WITH_TIMEOUT(!statusSpy.isEmpty()
                                 && statusSpy.last().front().toJsonObject().value(QStringLiteral("sent_signals")).toInt() == 2,
                                 2'000);
        const QJsonObject payload = QJsonDocument::fromJson(requestBody).object();
        QCOMPARE(payload.value(QStringLiteral("token")).toString(), QStringLiteral("unit-test-token"));
        QCOMPARE(payload.value(QStringLiteral("template")).toString(), QStringLiteral("markdown"));
        QVERIFY(payload.value(QStringLiteral("content")).toString().contains(QStringLiteral("520700.SH")));
        QVERIFY(payload.value(QStringLiteral("content")).toString().contains(QStringLiteral("520990.SH")));
        notifier.stop();
    }
};

QTEST_GUILESS_MAIN(PushPlusNotifierTest)

#include "test_pushplus.moc"
