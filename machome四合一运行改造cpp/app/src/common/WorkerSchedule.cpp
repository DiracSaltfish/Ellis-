#include "common/WorkerSchedule.h"

#include <QJsonArray>
#include <QSet>

#include <algorithm>
#include <cmath>

namespace hub {
namespace {

QTime parseClock(const QJsonValue &value)
{
    if (!value.isString() || value.toString() != value.toString().trimmed()) {
        return {};
    }
    QTime result = QTime::fromString(value.toString(), QStringLiteral("HH:mm:ss"));
    if (!result.isValid()) {
        result = QTime::fromString(value.toString(), QStringLiteral("HH:mm"));
    }
    return result;
}

} // namespace

bool WorkerSchedule::configure(const QJsonObject &object, QString *error)
{
    configured_ = false;
    timezone_ = {};
    weekdays_.clear();
    sources_.clear();

    const QString contract = object.value(QStringLiteral("contract")).toString();
    if (contract != QLatin1String(Contract)) {
        if (error) *error = QStringLiteral("worker_monitor_schedule.contract 必须是 %1")
                                .arg(QLatin1String(Contract));
        return false;
    }
    const QByteArray timezoneId = object.value(QStringLiteral("timezone")).toString().toUtf8();
    timezone_ = QTimeZone(timezoneId);
    if (!timezone_.isValid()) {
        if (error) *error = QStringLiteral("worker_monitor_schedule.timezone 无效");
        return false;
    }

    const QJsonArray weekdays = object.value(QStringLiteral("weekdays")).toArray();
    for (const QJsonValue &value : weekdays) {
        const double number = value.toDouble(-1);
        const int day = static_cast<int>(number);
        if (!value.isDouble() || !std::isfinite(number) || std::floor(number) != number
            || day < 1 || day > 7 || weekdays_.contains(day)) {
            if (error) *error = QStringLiteral("worker_monitor_schedule.weekdays 必须是无重复的 1..7 整数");
            return false;
        }
        weekdays_.insert(day);
    }
    if (weekdays_.isEmpty()) {
        if (error) *error = QStringLiteral("worker_monitor_schedule.weekdays 不得为空");
        return false;
    }

    QSet<QString> names;
    const QJsonArray sources = object.value(QStringLiteral("sources")).toArray();
    for (const QJsonValue &value : sources) {
        if (!value.isObject()) {
            if (error) *error = QStringLiteral("worker_monitor_schedule.sources 项必须是对象");
            return false;
        }
        const QJsonObject sourceObject = value.toObject();
        Source source;
        source.name = sourceObject.value(QStringLiteral("source")).toString().trimmed();
        if (source.name.isEmpty() || source.name.size() > 128 || names.contains(source.name)) {
            if (error) *error = QStringLiteral("worker_monitor_schedule source 为空、重复或过长");
            return false;
        }
        names.insert(source.name);

        const QJsonArray windows = sourceObject.value(QStringLiteral("windows")).toArray();
        for (const QJsonValue &windowValue : windows) {
            const QJsonObject windowObject = windowValue.toObject();
            Window window{parseClock(windowObject.value(QStringLiteral("start"))),
                          parseClock(windowObject.value(QStringLiteral("end")))};
            if (!windowValue.isObject() || !window.start.isValid() || !window.end.isValid()
                || window.start >= window.end) {
                if (error) *error = QStringLiteral("worker_monitor_schedule window 必须是同日 start < end");
                return false;
            }
            source.windows.append(window);
        }
        if (source.windows.isEmpty()) {
            if (error) *error = QStringLiteral("worker_monitor_schedule 每个 source 至少需要一个 window");
            return false;
        }
        std::sort(source.windows.begin(), source.windows.end(), [](const Window &a, const Window &b) {
            return a.start < b.start;
        });
        for (qsizetype i = 1; i < source.windows.size(); ++i) {
            if (source.windows.at(i).start < source.windows.at(i - 1).end) {
                if (error) *error = QStringLiteral("worker_monitor_schedule 同一 source 的 window 不得重叠");
                return false;
            }
        }
        sources_.append(source);
    }
    if (sources_.isEmpty()) {
        if (error) *error = QStringLiteral("worker_monitor_schedule.sources 不得为空");
        return false;
    }
    configured_ = true;
    return true;
}

bool WorkerSchedule::isConfigured() const
{
    return configured_;
}

QStringList WorkerSchedule::configuredSources() const
{
    QStringList result;
    for (const Source &source : sources_) result.append(source.name);
    return result;
}

QStringList WorkerSchedule::expectedSourcesAt(const QDateTime &utc) const
{
    QStringList result;
    if (!configured_ || !utc.isValid()) return result;
    const QDateTime local = utc.toTimeZone(timezone_);
    if (!weekdays_.contains(local.date().dayOfWeek())) return result;
    const QTime clock = local.time();
    for (const Source &source : sources_) {
        for (const Window &window : source.windows) {
            if (clock >= window.start && clock < window.end) {
                result.append(source.name);
                break;
            }
        }
    }
    return result;
}

bool WorkerSchedule::sourceExpectedAt(const QString &source, const QDateTime &utc) const
{
    return expectedSourcesAt(utc).contains(source);
}

} // namespace hub
