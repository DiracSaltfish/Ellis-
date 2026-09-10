#pragma once

#include <QDate>
#include <QDateTime>
#include <QJsonArray>
#include <QJsonObject>
#include <QString>

namespace machome::redemption {

struct ScheduleDecision {
    QString phase;
    bool businessDay = false;
    bool pcfWindow = false;
    bool resetDue = false;
    bool windLaunchDue = false;
    bool warmupDue = false;
    bool monitoringDesired = false;
    bool shutdownDue = false;
    QDateTime localNow;
    QDateTime nextTransitionUtc;
};

class RedemptionCore final {
public:
    static QString normalizeSymbol(const QString &value, QString *error = nullptr);
    static ScheduleDecision evaluateSchedule(const QDateTime &utcNow);
    static QJsonObject canonicalCapture(const QJsonObject &payload,
                                        QString *error = nullptr);
    static QJsonArray changeDetails(const QJsonObject &previous,
                                    const QJsonObject &current);
    static QJsonObject classifyIntradayOpportunity(
        const QJsonObject *previous, const QJsonObject &current,
        const QJsonObject &pcf, const QDate &referenceDay);
    static QJsonObject normalizePcf(const QJsonObject &payload,
                                    const QString &symbol,
                                    const QDate &requestedDay,
                                    QString *error = nullptr);
};

} // namespace machome::redemption
