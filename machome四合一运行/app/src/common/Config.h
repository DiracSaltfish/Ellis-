#pragma once

#include <QJsonObject>
#include <QList>
#include <QString>

namespace hub {

struct ExpectedArtifact {
    QString path;
    QString sha256;
};

struct LaunchdUnit {
    QString id;
    QString label;
    QString plistPath;
    QString expectedProgram;
    QStringList expectedArguments;
    QString artifactSha256;
    QList<ExpectedArtifact> expectedArtifacts;
    int startDelayMs = 0;
    bool required = true;
};

struct ManagedProcess {
    QString id;
    QString program;
    QStringList arguments;
    QString workingDirectory;
    int startOrder = 0;
    int stopOrder = 0;
    int startDelayMs = 0;
    bool required = true;
};

struct ModuleConfig {
    QString id;
    QString displayName;
    QString adapter;
    bool enabled = true;
    bool controlEnabled = false;
    QString ownership = QStringLiteral("shadow");
    QStringList allowedActions;
    QStringList approvalRequiredActions;
    QList<LaunchdUnit> launchdUnits;
    QList<ManagedProcess> managedProcesses;
    QJsonObject settings;
};

struct AppConfig {
    int schemaVersion = 1;
    QString socketPath;
    QString auditDatabase;
    int frameLimitBytes = 1024 * 1024;
    QString sourcePath;
    QString configHash;
    QList<ModuleConfig> modules;

    static AppConfig load(const QString &path, QString *error = nullptr);
    static QString defaultConfigPath();
    static QString expandPath(const QString &path);
    bool isValid(QString *error = nullptr) const;
    const ModuleConfig *module(const QString &id) const;
};

} // namespace hub

Q_DECLARE_METATYPE(hub::ModuleConfig)
