#pragma once

#include <QByteArray>
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QNetworkRequest>
#include <QString>

#include <memory>

namespace machome::upload {

struct UploadHttpResponse {
    int statusCode = 0;
    QByteArray body;
    QByteArray contentType;
    QString error;
};

class IUploadHttpTransport {
public:
    virtual ~IUploadHttpTransport() = default;
    virtual UploadHttpResponse request(const QNetworkRequest &request,
                                       const QByteArray &method,
                                       const QByteArray &body,
                                       int timeoutMs) = 0;
};

class QtUploadHttpTransport final : public IUploadHttpTransport {
public:
    UploadHttpResponse request(const QNetworkRequest &request,
                               const QByteArray &method,
                               const QByteArray &body,
                               int timeoutMs) override;
};

class UploadCollectors final {
public:
    explicit UploadCollectors(std::unique_ptr<IUploadHttpTransport> transport = {});
    void setTransport(std::unique_ptr<IUploadHttpTransport> transport);

    UploadHttpResponse fetch(const QUrl &url, const QByteArray &method = "GET",
                             const QByteArray &body = {},
                             const QHash<QByteArray, QByteArray> &headers = {},
                             int timeoutMs = 15000, int attempts = 3,
                             QString *error = nullptr);

    static QJsonArray parseEastmoneyPurchaseStatus(const QByteArray &body,
                                                    const QHash<QString, QString> &wanted,
                                                    const QString &date,
                                                    QString *error = nullptr);
    static QJsonArray parseEastmoneyNetValues(const QByteArray &body,
                                              const QString &symbol,
                                              QString *error = nullptr);
    static QJsonArray parseSzseShares(const QByteArray &body,
                                     QString *error = nullptr);
    static QJsonArray parseSseShares(const QByteArray &body, const QString &fundType,
                                    QString *error = nullptr);
    static QJsonArray parseSafeXls(const QByteArray &body,
                                  QString *error = nullptr);
    static QJsonObject parsePcf(const QByteArray &body,
                                const QJsonObject &definition,
                                const QString &sourceUrl,
                                const QString &expectedDay,
                                QString *error = nullptr);

private:
    std::unique_ptr<IUploadHttpTransport> transport_;
};

} // namespace machome::upload
