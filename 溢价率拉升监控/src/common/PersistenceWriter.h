#pragma once

#include <QByteArray>
#include <QDir>
#include <QFile>
#include <QObject>

namespace premium {

class PersistenceWriter : public QObject {
    Q_OBJECT
public:
    explicit PersistenceWriter(QString dataDirectory, QObject *parent = nullptr);

public Q_SLOTS:
    void appendRaw(const QByteArray &jsonLine, const QDate &partitionDate);
    void appendNormalized(const QByteArray &jsonLine, const QDate &partitionDate);
    void appendSignal(const QByteArray &jsonLine);
    void prune();
    void close();

Q_SIGNALS:
    void writeCompleted();
    void writeError(const QString &message);
    void storageStateChanged(bool enabled, qint64 availableBytes);

private:
    bool append(QFile &file, const QString &prefix, const QByteArray &line, const QDate &date, bool compressed);
    bool ensureFile(QFile &file, const QString &prefix, const QDate &date, bool compressed);
    QByteArray compressFrame(const QByteArray &line) const;
    qint64 availableBytes() const;

    QDir directory_;
    QFile rawFile_;
    QFile normalizedFile_;
    QFile signalFile_;
    QDate rawDate_;
    QDate normalizedDate_;
    bool storageEnabled_ = true;
};

} // namespace premium
