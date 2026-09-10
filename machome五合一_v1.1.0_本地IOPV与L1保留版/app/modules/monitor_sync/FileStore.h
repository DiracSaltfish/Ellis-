#pragma once
#include <QJsonObject>
#include <QString>
namespace machome::sync {
class FileStore {
public:
    explicit FileStore(QString root);
    QJsonObject dispatch(const QJsonObject &request);
    static QStringList dayFiles();
    static QStringList sharedFiles();
    static void validateSnapshot(const QJsonObject &snapshot);
    static QJsonObject readJson(const QString &path);
    static void writeJson(const QString &path, const QJsonObject &object);
    static QString digest(const QByteArray &bytes);
private:
    QString root_;
    QJsonObject head(const QString &base);
    QJsonObject loadSnapshot(const QString &base, const QJsonObject &head, const QString &day);
};
}
