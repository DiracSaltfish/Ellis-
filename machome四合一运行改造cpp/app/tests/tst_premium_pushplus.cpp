#include "modules/premium/engine/server/PushPlusNotifier.h"

#include <QDateTime>
#include <QFile>
#include <QHostAddress>
#include <QJsonDocument>
#include <QSignalSpy>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTemporaryDir>
#include <QtTest>

using machome::premium::engine::PushPlusConfig;
using machome::premium::engine::PushPlusNotifier;

namespace {
QJsonObject testSignal(const QString &symbol, const QString &time, int sequence)
{
    return {{QStringLiteral("type"), QStringLiteral("signal")},
            {QStringLiteral("source_session"), QStringLiteral("fixture-session")},
            {QStringLiteral("symbol"), symbol},
            {QStringLiteral("name"), symbol.left(6)},
            {QStringLiteral("occurred_at"), time},
            {QStringLiteral("signal_seq"), sequence},
            {QStringLiteral("model"), QStringLiteral("radar")},
            {QStringLiteral("premium_ppm"), 6332},
            {QStringLiteral("bid1_price_e6"), 1621000}};
}
} // namespace

class PremiumPushPlusTests final : public QObject {
    Q_OBJECT
private Q_SLOTS:
    void enablementAndIdentityMatchBaseline()
    {
        QTemporaryDir directory;
        const QString environmentPath = directory.filePath(QStringLiteral("pushplus.env"));
        QFile environment(environmentPath);
        QVERIFY(environment.open(QIODevice::WriteOnly | QIODevice::Text));
        environment.write("PUSHPLUS_TOKEN=fixture-token\nPUSHPLUS_MONITOR_ENABLED=false\n");
        environment.close();
        const QJsonObject application{{QStringLiteral("pushplus"),
            QJsonObject{{QStringLiteral("enabled"), true},
                        {QStringLiteral("env_file"), environmentPath},
                        {QStringLiteral("state_file"), directory.filePath(QStringLiteral("state.json"))}}}};
        QString error;
        const PushPlusConfig config = PushPlusNotifier::fromApplicationConfig(
            application, directory.path(), &error);
        QVERIFY2(error.isEmpty(), qPrintable(error));
        QVERIFY(config.enabled);
        QCOMPARE(config.token, QStringLiteral("fixture-token"));

        const QJsonObject first = testSignal(
            QStringLiteral("520700.SH"), QStringLiteral("2026-09-01T10:00:00.000+08:00"), 1);
        const QJsonObject second = testSignal(
            QStringLiteral("520990.SH"), QStringLiteral("2026-09-01T10:00:00.000+08:00"), 1);
        const QJsonObject third = testSignal(
            QStringLiteral("520700.SH"), QStringLiteral("2026-09-01T10:00:01.000+08:00"), 1);
        QVERIFY(PushPlusNotifier::eventKey(first) != PushPlusNotifier::eventKey(second));
        QVERIFY(PushPlusNotifier::eventKey(first) != PushPlusNotifier::eventKey(third));
    }

    void aggregationAndLocalAckMatchBaseline()
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
                for (QByteArray header : requestBytes.left(headerEnd).split('\n')) {
                    header = header.trimmed();
                    if (header.toLower().startsWith("content-length:"))
                        contentLength = header.mid(header.indexOf(':') + 1).trimmed().toInt();
                }
                if (contentLength < 0
                    || requestBytes.size() - headerEnd - 4 < contentLength) return;
                requestBody = requestBytes.mid(headerEnd + 4, contentLength);
                responded = true;
                const QByteArray body = "{\"code\":200,\"msg\":\"ok\"}";
                socket->write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                              + QByteArray::number(body.size())
                              + "\r\nConnection: close\r\n\r\n" + body);
                socket->disconnectFromHost();
            });
        });

        QTemporaryDir directory;
        PushPlusConfig config;
        config.enabled = true;
        config.token = QStringLiteral("fixture-token");
        config.endpoint = QStringLiteral("http://127.0.0.1:%1/send").arg(server.serverPort());
        config.stateFile = directory.filePath(QStringLiteral("state.json"));
        config.batchWindowMs = 25;
        config.requestTimeoutMs = 1000;
        PushPlusNotifier notifier(config);
        QSignalSpy status(&notifier, &PushPlusNotifier::statusChanged);
        notifier.start();
        notifier.enqueueSignal(testSignal(
            QStringLiteral("520700.SH"), QDateTime::currentDateTime().toString(Qt::ISODateWithMs), 1));
        notifier.enqueueSignal(testSignal(
            QStringLiteral("520990.SH"), QDateTime::currentDateTime().addMSecs(1).toString(Qt::ISODateWithMs), 1));
        QTRY_VERIFY_WITH_TIMEOUT(!requestBody.isEmpty(), 2000);
        QTRY_VERIFY_WITH_TIMEOUT(!status.isEmpty()
            && status.last().at(0).toJsonObject().value(QStringLiteral("sent_signals")).toInt() == 2,
            2000);
        const QJsonObject payload = QJsonDocument::fromJson(requestBody).object();
        QCOMPARE(payload.value(QStringLiteral("token")).toString(), QStringLiteral("fixture-token"));
        QCOMPARE(payload.value(QStringLiteral("template")).toString(), QStringLiteral("markdown"));
        QVERIFY(payload.value(QStringLiteral("content")).toString().contains(QStringLiteral("520700.SH")));
        QVERIFY(payload.value(QStringLiteral("content")).toString().contains(QStringLiteral("520990.SH")));
        notifier.stop();
    }
};

QTEST_GUILESS_MAIN(PremiumPushPlusTests)
#include "tst_premium_pushplus.moc"
