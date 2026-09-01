#pragma once

#include <QDateTime>
#include <QJsonObject>
#include <QString>
#include <QStringList>
#include <array>

namespace premium {

constexpr qint64 PriceScale = 1'000'000;
constexpr qint64 VolumeScale = 100;
constexpr qint64 AmountScale = 100'000;
constexpr qint64 RatioPpmScale = 1'000'000;

struct QuoteSnapshot {
    QString symbol;
    QString code;
    QString market;
    QString name;
    QString tradingPhase;
    QString sourceSession;
    QString mappingVersion;
    qint64 origTime = 0;
    qint64 receiveWallNs = 0;
    qint64 publishWallNs = 0;
    qint64 lastPriceE6 = 0;
    qint64 nominalPriceE6 = 0;
    qint64 referencePriceE6 = 0;
    qint64 preClosePriceE6 = 0;
    qint64 openPriceE6 = 0;
    qint64 highPriceE6 = 0;
    qint64 lowPriceE6 = 0;
    qint64 iopvE6 = 0;
    qint64 highLimitE6 = 0;
    qint64 lowLimitE6 = 0;
    qint64 totalVolumeE2 = 0;
    qint64 totalAmountE5 = 0;
    qint64 numTrades = 0;
    qint64 levelCount = 10;
    std::array<qint64, 10> bidPricesE6{};
    std::array<qint64, 10> askPricesE6{};
    std::array<qint64, 10> bidVolumesE2{};
    std::array<qint64, 10> askVolumesE2{};
    qint64 sellPremiumPpm = 0;
    qint64 displayPremiumPpm = 0;
    qint64 changePpm = 0;
    qint64 bidRise150sPpm = 0;
    qint64 bidRise300sPpm = 0;
    qint64 bidRise30sPpm = 0;
    qint64 bidRise60sPpm = 0;
    qint64 bidRise90sPpm = 0;
    qint64 momentum3mPpm = 0;
    qint64 momentum5mPpm = 0;
    qint64 adaptive3mPpm = 0;
    qint64 adaptive5mPpm = 0;
    qint64 minuteRangePpm = 0;
    qint64 minuteRangeBasePpm = 0;
    bool sourceReady = false;
    bool replay = false;
    bool iopvStatic = false;
    bool numericMappingVerified = false;
    QStringList qualityIssues;

    [[nodiscard]] QJsonObject toSummaryJson() const;
    [[nodiscard]] QJsonObject toDetailJson() const;
    [[nodiscard]] QJsonObject toLegacyBookJson() const;
};

struct SignalEvent {
    quint64 sequence = 0;
    QString symbol;
    QDateTime occurredAt;
    qint64 premiumPpm = 0;
    qint64 rise30sPpm = 0;
    qint64 rise300sPpm = 0;
    qint64 bidRise150sPpm = 0;
    qint64 bidRise300sPpm = 0;
    qint64 bidRise30sPpm = 0;
    qint64 bidRise60sPpm = 0;
    qint64 bidRise90sPpm = 0;
    qint64 momentum3mPpm = 0;
    qint64 momentum5mPpm = 0;
    qint64 adaptive3mPpm = 0;
    qint64 adaptive5mPpm = 0;
    qint64 minuteRangePpm = 0;
    qint64 minuteRangeBasePpm = 0;
    QString model;
    QString reason;
    bool repeat = false;
    bool replay = false;

    [[nodiscard]] QJsonObject toJson(bool backfill = false) const;
};

[[nodiscard]] QString normalizeSymbol(QString value);
[[nodiscard]] QString inferMarketSuffix(const QString &sixDigitCode);
[[nodiscard]] double scaledPrice(qint64 valueE6);
[[nodiscard]] double scaledVolume(qint64 valueE2);
[[nodiscard]] double ppmToPercent(qint64 ppm);
[[nodiscard]] qint64 calculateRatioPpm(qint64 numeratorE6, qint64 denominatorE6);
[[nodiscard]] qint64 exchangeTimeToEpochMs(qint64 rawOrigTime);

} // namespace premium
