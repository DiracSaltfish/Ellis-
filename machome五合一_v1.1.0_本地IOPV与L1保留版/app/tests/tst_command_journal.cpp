#include "agent/CommandJournal.h"

#include <QJsonObject>
#include <QTemporaryDir>
#include <QtTest>

using hub::CommandJournal;

class CommandJournalTests final : public QObject {
    Q_OBJECT
private slots:
    void durableReplayAndRecovery();
};

void CommandJournalTests::durableReplayAndRecovery() {
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = directory.filePath(QStringLiteral("audit.db"));
    const QByteArray fingerprint("stable-fingerprint");
    const QJsonObject request{{QStringLiteral("command_id"), QStringLiteral("command-0001")},
                              {QStringLiteral("module_id"), QStringLiteral("redemption")},
                              {QStringLiteral("action"), QStringLiteral("redemption_qmt_order")}};
    const QJsonObject accepted{{QStringLiteral("state"), QStringLiteral("accepted")},
                               {QStringLiteral("control_revision"), 7}};
    {
        CommandJournal journal;
        QString error;
        QVERIFY2(journal.initialize(path, &error), qPrintable(error));
        QVERIFY2(journal.reserve(QStringLiteral("command-0001"), fingerprint, request, accepted, &error),
                 qPrintable(error));
    }
    {
        CommandJournal journal;
        QString error;
        QVERIFY2(journal.initialize(path, &error), qPrintable(error));
        QSet<QString> unresolved;
        QVERIFY2(journal.recoverInterrupted(&unresolved, &error), qPrintable(error));
        QVERIFY(unresolved.contains(QStringLiteral("redemption")));
        CommandJournal::Entry entry;
        QVERIFY2(journal.lookup(QStringLiteral("command-0001"), &entry, &error), qPrintable(error));
        QCOMPARE(entry.fingerprint, fingerprint);
        QCOMPARE(entry.result.value(QStringLiteral("state")).toString(), QStringLiteral("timed_out"));
        QVERIFY(entry.unresolved);
        QVERIFY2(journal.resolveModule(QStringLiteral("redemption"), &error), qPrintable(error));
        QVERIFY2(journal.lookup(QStringLiteral("command-0001"), &entry, &error), qPrintable(error));
        QVERIFY(entry.resolved);
    }
}

QTEST_GUILESS_MAIN(CommandJournalTests)
#include "tst_command_journal.moc"
