#include "modules/qmt/QmtCanonicalJson.h"
#include "modules/qmt/QmtClient.h"

#include <QFile>
#include <QJsonDocument>
#include <QTest>

namespace machome::qmt {

class QmtClientTestPeer {
public:
    static bool validateFullMeta(QmtClient &client, contract::SnapshotKind kind,
                                 const QJsonObject &message, const QJsonArray &data,
                                 const QJsonObject &extra, qint64 *snapshotId,
                                 qint64 *sequence) {
        return client.validateFullMeta(kind, message, data, extra, snapshotId, sequence);
    }

    static void handleOrders(QmtClient &client, const QJsonObject &message) {
        client.handleOrders(message);
    }

    static bool ordersSynced(const QmtClient &client) { return client.ordersSynced_; }
    static qint64 ordersSnapshotId(const QmtClient &client) {
        return client.ordersSnapshotId_;
    }
    static qint64 ordersSequence(const QmtClient &client) { return client.ordersSequence_; }
    static void seedReadyPending(QmtClient &client) {
        client.welcomeReceived_ = true;
        client.positionsSynced_ = true;
        client.ordersSynced_ = true;
        client.pendingOrderCommands_.insert(
            QStringLiteral("ETF-test-P-159513-seeded"),
            QmtClient::PendingOrder{QStringLiteral("hub-command-seeded"), {}, true});
    }
    static qsizetype pendingCount(const QmtClient &client) {
        return client.pendingOrderCommands_.size();
    }
    static bool positionsSynced(const QmtClient &client) { return client.positionsSynced_; }
};

} // namespace machome::qmt

class QmtClientContractTest final : public QObject {
    Q_OBJECT
private:
    static QJsonArray vectors() {
        QFile file(QStringLiteral(MACHOME_TEST_SOURCE_DIR "/tests/qmt_contract_vectors.json"));
        if (!file.open(QIODevice::ReadOnly)) return {};
        QJsonParseError error;
        const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &error);
        if (error.error != QJsonParseError::NoError || !document.isObject()) return {};
        return document.object().value(QStringLiteral("vectors")).toArray();
    }

    static QJsonObject vectorNamed(const QString &name) {
        for (const QJsonValue &value : vectors()) {
            const QJsonObject object = value.toObject();
            if (object.value(QStringLiteral("name")).toString() == name) return object;
        }
        return {};
    }

    static machome::qmt::contract::SnapshotKind kind(const QJsonObject &vector) {
        return vector.value(QStringLiteral("kind")).toString() == QStringLiteral("orders")
            ? machome::qmt::contract::SnapshotKind::Orders
            : machome::qmt::contract::SnapshotKind::Positions;
    }

private slots:
    void pythonCanonicalVectorsMatchByteForByte() {
        const QJsonArray all = vectors();
        QVERIFY2(!all.isEmpty(), "QMT contract vectors could not be loaded");
        for (const QJsonValue &value : all) {
            const QJsonObject vector = value.toObject();
            QString error;
            const QByteArray canonical = machome::qmt::contract::canonicalSnapshotJson(
                kind(vector), vector.value(QStringLiteral("data")).toArray(),
                vector.value(QStringLiteral("extra")).toObject(), &error);
            QVERIFY2(error.isEmpty(), qPrintable(vector.value(QStringLiteral("name")).toString()
                                                  + QStringLiteral(": ") + error));
            QCOMPARE(canonical, vector.value(QStringLiteral("canonical")).toString().toUtf8());
            QCOMPARE(machome::qmt::contract::snapshotChecksum(
                         kind(vector), vector.value(QStringLiteral("data")).toArray(),
                         vector.value(QStringLiteral("extra")).toObject(), &error),
                     vector.value(QStringLiteral("sha1")).toString().toLatin1());
        }
    }

    void snapshotIdIsStrictNonNegativeJsonInteger() {
        qint64 value = -1;
        QVERIFY(machome::qmt::contract::strictNonNegativeInteger(QJsonValue(0), &value));
        QCOMPARE(value, 0);
        QVERIFY(machome::qmt::contract::strictNonNegativeInteger(QJsonValue(42), &value));
        QCOMPARE(value, 42);
        QVERIFY(!machome::qmt::contract::strictNonNegativeInteger(
            QJsonValue(QStringLiteral("42")), &value));
        QVERIFY(!machome::qmt::contract::strictNonNegativeInteger(QJsonValue(true), &value));
        QVERIFY(!machome::qmt::contract::strictNonNegativeInteger(QJsonValue(-1), &value));
        QVERIFY(!machome::qmt::contract::strictNonNegativeInteger(QJsonValue(1.5), &value));
        QVERIFY(!machome::qmt::contract::strictNonNegativeInteger(
            QJsonValue(9'007'199'254'740'992.0), &value));
    }

    void clientAcceptsNumericFullSnapshotAndComparesNumericDelta() {
        using machome::qmt::QmtClient;
        using machome::qmt::QmtClientConfig;
        using machome::qmt::QmtClientTestPeer;
        using machome::qmt::contract::SnapshotKind;

        const QJsonObject vector = vectorNamed(
            QStringLiteral("production_orders_unicode_split_and_zero_floats"));
        QVERIFY(!vector.isEmpty());
        const QJsonArray orders = vector.value(QStringLiteral("data")).toArray();
        QmtClient client(QmtClientConfig{QStringLiteral("QMT-test"),
                                         QStringLiteral("127.0.0.1"), 1});

        QJsonObject full{{QStringLiteral("type"), QStringLiteral("orders_data")},
                         {QStringLiteral("sync_mode"), QStringLiteral("full")},
                         {QStringLiteral("snapshot_id"), 7},
                         {QStringLiteral("seq"), 1},
                         {QStringLiteral("count"), orders.size()},
                         {QStringLiteral("checksum"), vector.value(QStringLiteral("sha1"))},
                         {QStringLiteral("data"), orders}};
        qint64 snapshotId = -1;
        qint64 sequence = -1;
        QVERIFY(QmtClientTestPeer::validateFullMeta(client, SnapshotKind::Orders,
                                                   full, orders, {},
                                                   &snapshotId, &sequence));
        QCOMPARE(snapshotId, 7);
        QCOMPARE(sequence, 1);
        QmtClientTestPeer::handleOrders(client, full);
        QVERIFY(QmtClientTestPeer::ordersSynced(client));
        QCOMPARE(QmtClientTestPeer::ordersSnapshotId(client), 7);

        QJsonArray remaining{orders.at(0)};
        QString checksumError;
        QJsonObject delta{{QStringLiteral("type"), QStringLiteral("orders_data")},
                          {QStringLiteral("sync_mode"), QStringLiteral("delta")},
                          {QStringLiteral("snapshot_id"), 7},
                          {QStringLiteral("seq"), 2},
                          {QStringLiteral("count"), 1},
                          {QStringLiteral("checksum"),
                           QString::fromLatin1(machome::qmt::contract::snapshotChecksum(
                               SnapshotKind::Orders, remaining, {}, &checksumError))},
                          {QStringLiteral("upserts"), QJsonArray{}},
                          {QStringLiteral("remove_ids"), QJsonArray{QStringLiteral("10001")}}};
        QVERIFY(checksumError.isEmpty());
        QmtClientTestPeer::handleOrders(client, delta);
        QVERIFY(QmtClientTestPeer::ordersSynced(client));
        QCOMPARE(QmtClientTestPeer::ordersSnapshotId(client), 7);
        QCOMPARE(QmtClientTestPeer::ordersSequence(client), 2);

        delta.insert(QStringLiteral("snapshot_id"), QStringLiteral("7"));
        delta.insert(QStringLiteral("seq"), 3);
        QmtClientTestPeer::handleOrders(client, delta);
        QVERIFY(!QmtClientTestPeer::ordersSynced(client));
    }

    void clientAcceptsSnapshotIdZeroWithoutStringCoercion() {
        using machome::qmt::QmtClient;
        using machome::qmt::QmtClientConfig;
        using machome::qmt::QmtClientTestPeer;
        using machome::qmt::contract::SnapshotKind;

        const QJsonObject vector = vectorNamed(
            QStringLiteral("production_empty_positions_zero_cash"));
        QVERIFY(!vector.isEmpty());
        const QJsonArray data = vector.value(QStringLiteral("data")).toArray();
        const QJsonObject extra = vector.value(QStringLiteral("extra")).toObject();
        QJsonObject message{{QStringLiteral("snapshot_id"), 0},
                            {QStringLiteral("seq"), 0},
                            {QStringLiteral("count"), 0},
                            {QStringLiteral("checksum"), vector.value(QStringLiteral("sha1"))}};
        QmtClient client(QmtClientConfig{QStringLiteral("QMT-test"),
                                         QStringLiteral("127.0.0.1"), 1});
        qint64 snapshotId = -1;
        qint64 sequence = -1;
        QVERIFY(QmtClientTestPeer::validateFullMeta(client, SnapshotKind::Positions,
                                                   message, data, extra,
                                                   &snapshotId, &sequence));
        QCOMPARE(snapshotId, 0);
        message.insert(QStringLiteral("snapshot_id"), QStringLiteral("0"));
        QVERIFY(!QmtClientTestPeer::validateFullMeta(client, SnapshotKind::Positions,
                                                    message, data, extra,
                                                    &snapshotId, &sequence));
    }

    void reconciliationClearsOldPendingAndRequiresFreshFullSync() {
        using machome::qmt::QmtClient;
        using machome::qmt::QmtClientConfig;
        using machome::qmt::QmtClientTestPeer;
        QmtClient client(QmtClientConfig{QStringLiteral("QMT-test"),
                                         QStringLiteral("127.0.0.1"), 1});
        QmtClientTestPeer::seedReadyPending(client);
        QCOMPARE(QmtClientTestPeer::pendingCount(client), 1);
        QVERIFY(QmtClientTestPeer::ordersSynced(client));
        QVERIFY(QmtClientTestPeer::positionsSynced(client));

        client.reconcilePendingOrders();
        QCOMPARE(QmtClientTestPeer::pendingCount(client), 0);
        QVERIFY(!QmtClientTestPeer::ordersSynced(client));
        QVERIFY(!QmtClientTestPeer::positionsSynced(client));
        QVERIFY(!client.isReady());
        QVERIFY(!client.snapshot().value(QStringLiteral("last_result")).toObject()
                     .value(QStringLiteral("pending")).toBool(true));
    }
};

QTEST_MAIN(QmtClientContractTest)
#include "tst_qmt_contract.moc"
