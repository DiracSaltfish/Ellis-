#include "common/WorkerSchedule.h"

#include <QJsonArray>
#include <QTest>
#include <QTimeZone>

namespace {

QJsonObject window(const char *start, const char *end)
{
    return {{QStringLiteral("start"), QLatin1String(start)},
            {QStringLiteral("end"), QLatin1String(end)}};
}

QJsonObject source(const char *name, std::initializer_list<QJsonObject> windows)
{
    QJsonArray array;
    for (const QJsonObject &item : windows) array.append(item);
    return {{QStringLiteral("source"), QLatin1String(name)},
            {QStringLiteral("windows"), array}};
}

QJsonObject productionSchedule()
{
    return {
        {QStringLiteral("contract"), QLatin1String(hub::WorkerSchedule::Contract)},
        {QStringLiteral("timezone"), QStringLiteral("Asia/Shanghai")},
        {QStringLiteral("weekdays"), QJsonArray{1, 2, 3, 4, 5}},
        {QStringLiteral("sources"), QJsonArray{
            source("home-mac", {window("09:20", "11:30"), window("13:01", "14:57")}),
            source("xop", {window("09:37", "15:00")}),
            source("nikkei", {window("09:37", "14:40")}),
            source("silver", {window("09:37", "10:15"), window("10:31", "11:30"),
                                window("13:31", "15:00")}),
        }}
    };
}

QDateTime shanghai(int year, int month, int day, int hour, int minute)
{
    return QDateTime(QDate(year, month, day), QTime(hour, minute),
                     QTimeZone("Asia/Shanghai")).toUTC();
}

} // namespace

class WorkerScheduleTest final : public QObject {
    Q_OBJECT
private slots:
    void matchesBaselineBoundaries()
    {
        hub::WorkerSchedule schedule;
        QString error;
        QVERIFY2(schedule.configure(productionSchedule(), &error), qPrintable(error));
        QCOMPARE(schedule.expectedSourcesAt(shanghai(2026, 9, 4, 9, 14)), QStringList{});
        QCOMPARE(schedule.expectedSourcesAt(shanghai(2026, 9, 4, 9, 20)),
                 QStringList({QStringLiteral("home-mac")}));
        QCOMPARE(schedule.expectedSourcesAt(shanghai(2026, 9, 4, 14, 39)),
                 QStringList({QStringLiteral("home-mac"), QStringLiteral("xop"),
                              QStringLiteral("nikkei"), QStringLiteral("silver")}));
        QCOMPARE(schedule.expectedSourcesAt(shanghai(2026, 9, 4, 14, 40)),
                 QStringList({QStringLiteral("home-mac"), QStringLiteral("xop"),
                              QStringLiteral("silver")}));
        QCOMPARE(schedule.expectedSourcesAt(shanghai(2026, 9, 4, 14, 57)),
                 QStringList({QStringLiteral("xop"), QStringLiteral("silver")}));
        QCOMPARE(schedule.expectedSourcesAt(shanghai(2026, 9, 4, 15, 0)), QStringList{});
        QCOMPARE(schedule.expectedSourcesAt(shanghai(2026, 9, 5, 10, 0)), QStringList{});
    }

    void rejectsOverlappingWindows()
    {
        QJsonObject value = productionSchedule();
        QJsonArray sources = value.value(QStringLiteral("sources")).toArray();
        sources.replace(0, source("home-mac", {window("09:20", "11:30"),
                                                window("11:00", "14:57")}));
        value.insert(QStringLiteral("sources"), sources);
        hub::WorkerSchedule schedule;
        QString error;
        QVERIFY(!schedule.configure(value, &error));
        QVERIFY(error.contains(QStringLiteral("重叠")));
    }
};

QTEST_GUILESS_MAIN(WorkerScheduleTest)
#include "tst_worker_schedule.moc"
