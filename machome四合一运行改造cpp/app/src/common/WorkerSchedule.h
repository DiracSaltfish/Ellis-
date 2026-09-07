#pragma once

#include <QDateTime>
#include <QJsonObject>
#include <QSet>
#include <QStringList>
#include <QTime>
#include <QTimeZone>
#include <QVector>

namespace hub {

// Explicit, deployment-supplied worker monitoring windows.  The schedule is
// intentionally data-driven: the Hub must not infer market hours from stale
// health files.
class WorkerSchedule final {
public:
    static constexpr auto Contract = "newnavnav-upload-health-monitor-v1";

    bool configure(const QJsonObject &object, QString *error = nullptr);
    bool isConfigured() const;
    QStringList configuredSources() const;
    QStringList expectedSourcesAt(const QDateTime &utc) const;
    bool sourceExpectedAt(const QString &source, const QDateTime &utc) const;

private:
    struct Window {
        QTime start;
        QTime end;
    };
    struct Source {
        QString name;
        QVector<Window> windows;
    };

    bool configured_ = false;
    QTimeZone timezone_;
    QSet<int> weekdays_;
    QVector<Source> sources_;
};

} // namespace hub
