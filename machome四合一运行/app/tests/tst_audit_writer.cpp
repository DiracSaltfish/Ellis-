#include "agent/AuditWriter.h"

#include <QFileInfo>
#include <QJsonObject>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QThread>
#include <QTimer>
#include <QtTest>

using hub::AuditWriter;

class AuditWriterTests final : public QObject {
    Q_OBJECT
private slots:
    void appendFirstCrashRecoveryIsIdempotent();
    void slowCriticalStorageDoesNotBlockCallerThread();
};

void AuditWriterTests::appendFirstCrashRecoveryIsIdempotent() {
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString databasePath = directory.filePath(QStringLiteral("audit.db"));
    const QString spoolPath = databasePath + QStringLiteral(".critical-emergency.jsonl");
    const QJsonObject event{
        {QStringLiteral("event_id"), QStringLiteral("event-stable-0001")},
        {QStringLiteral("timestamp"), QStringLiteral("2026-09-04T00:00:00.000Z")},
        {QStringLiteral("module_id"), QStringLiteral("redemption")},
        {QStringLiteral("type"), QStringLiteral("event")},
        {QStringLiteral("event_kind"), QStringLiteral("redemption.order")},
        {QStringLiteral("payload"), QJsonObject{{QStringLiteral("message"), QStringLiteral("已接受")}}},
    };

    // close() before the queued consumer runs simulates a process exiting
    // immediately after accepting a critical event. The event must already be
    // stable in the append-first FIFO.
    {
        AuditWriter writer(databasePath);
        writer.initialize();
        QVERIFY(writer.isReady());
        QString error;
        QVERIFY2(writer.enqueueCritical(event, &error), qPrintable(error));
        QVERIFY(QFileInfo::exists(spoolPath));
        writer.close();
    }

    {
        AuditWriter writer(databasePath);
        QSignalSpy recorded(&writer, &AuditWriter::criticalRecorded);
        writer.initialize();
        QVERIFY(writer.isReady());
        QTRY_COMPARE_WITH_TIMEOUT(recorded.size(), 1, 3000);
        const QJsonObject first = recorded.first().first().toJsonObject();
        QVERIFY(first.value(QStringLiteral("audit_event_id")).toInteger() > 0);
        QTRY_VERIFY_WITH_TIMEOUT(!QFileInfo::exists(spoolPath), 3000);

        // Replaying the same source event after a crash boundary must not add a
        // second SQLite row or emit a duplicate live notification.
        QString error;
        QVERIFY2(writer.enqueueCritical(event, &error), qPrintable(error));
        QTRY_VERIFY_WITH_TIMEOUT(!QFileInfo::exists(spoolPath), 3000);
        QCOMPARE(recorded.size(), 1);
        writer.close();
    }
}

void AuditWriterTests::slowCriticalStorageDoesNotBlockCallerThread() {
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString databasePath = directory.filePath(QStringLiteral("audit.db"));
    auto *writer = new AuditWriter(databasePath, nullptr, 400);
    QThread auditThread;
    writer->moveToThread(&auditThread);
    connect(&auditThread, &QThread::finished, writer, &QObject::deleteLater);
    auditThread.start();

    bool ready = false;
    QVERIFY(QMetaObject::invokeMethod(writer, [writer, &ready] {
        writer->initialize();
        ready = writer->isReady();
    }, Qt::BlockingQueuedConnection));
    QVERIFY(ready);
    QSignalSpy recorded(writer, &AuditWriter::criticalRecorded);
    QVERIFY(recorded.isValid());
    int callerTicks = 0;
    QTimer callerTimer;
    callerTimer.setInterval(20);
    connect(&callerTimer, &QTimer::timeout, this, [&callerTicks] { ++callerTicks; });
    callerTimer.start();

    const QJsonObject event{
        {QStringLiteral("event_id"), QStringLiteral("slow-disk-event-0001")},
        {QStringLiteral("timestamp"), QStringLiteral("2026-09-04T00:00:00.000Z")},
        {QStringLiteral("module_id"), QStringLiteral("upload")},
        {QStringLiteral("type"), QStringLiteral("event")},
        {QStringLiteral("event_kind"), QStringLiteral("upload.error")},
    };
    QElapsedTimer submission;
    submission.start();
    QString error;
    QVERIFY2(writer->enqueueCriticalAsync(event, &error), qPrintable(error));
    QVERIFY2(submission.elapsed() < 100,
             "critical submission performed storage I/O on the caller thread");
    QTRY_VERIFY_WITH_TIMEOUT(callerTicks >= 5, 250);
    QCOMPARE(recorded.size(), 0);
    QTRY_COMPARE_WITH_TIMEOUT(recorded.size(), 1, 3000);

    QVERIFY(QMetaObject::invokeMethod(writer, &AuditWriter::close,
                                      Qt::BlockingQueuedConnection));
    auditThread.quit();
    QVERIFY(auditThread.wait(3000));
}

QTEST_GUILESS_MAIN(AuditWriterTests)
#include "tst_audit_writer.moc"
