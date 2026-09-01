#pragma once

#include "client/ClientSettings.h"

#include <QByteArray>
#include <QHash>
#include <QJsonObject>
#include <QMainWindow>
#include <QPointer>
#include <QSet>
#include <QTimer>
#include <QWebSocket>

class QLabel;
class QAudioSink;
class QBuffer;
class QSystemTrayIcon;
class QTableWidget;
class QTableWidgetItem;
class QTabWidget;

namespace premium {

class DetailDialog;

class MonitorWindow final : public QMainWindow {
    Q_OBJECT
public:
    MonitorWindow(ClientSettings settings, QString settingsPath,
                  bool tradingEnabled = true, QWidget *parent = nullptr);
    ~MonitorWindow() override;

    Q_INVOKABLE void processMessage(const QString &message);

private:
    void buildUi();
    void connectSummary();
    void connectDetail();
    void processDetailMessage(const QString &message);
    void flushPendingSummaries();
    void showSettings();
    void playAlertSound(const QString &preset, int repeatCount);
    void playNextAlertSound();
    void updateSummary(const QJsonObject &object);
    void processSignal(const QJsonObject &object);
    void openDetail(const QString &symbol, const QString &name);
    int findRow(const QString &symbol) const;
    int fixedInsertRow(const QString &symbol) const;
    int findSignalRow(const QString &symbol) const;
    void updateSignalRow(const QJsonObject &signal, const QString &text,
                         const QString &eventKey, bool alreadyRead);
    void refreshSignalQuote(const QString &symbol);
    void removeSignalRow(const QString &symbol);
    void clearAllSignalRows();
    void markSignalRead(const QString &symbol);
    QString signalCachePath() const;
    void loadSignalList();
    void saveSignalList();
    QUrl endpoint(const QString &path) const;

    ClientSettings settings_;
    QString settingsPath_;
    QUrl serverBase_;
    QList<QmtClient::Profile> profiles_;
    QWebSocket summary_;
    QWebSocket detail_;
    QLabel *connection_ = nullptr;
    QLabel *serverState_ = nullptr;
    QLabel *replayBanner_ = nullptr;
    QTabWidget *listTabs_ = nullptr;
    QTableWidget *table_ = nullptr;
    QTableWidget *signalTable_ = nullptr;
    QSystemTrayIcon *tray_ = nullptr;
    QHash<QString, QJsonObject> snapshots_;
    QHash<QString, QJsonObject> pendingSummaries_;
    QHash<QString, QTableWidgetItem *> symbolItems_;
    QHash<QString, QTableWidgetItem *> signalItems_;
    QHash<QString, QJsonObject> latestSignals_;
    QHash<QString, QJsonObject> dismissedSignals_;
    QHash<QString, QPointer<DetailDialog>> details_;
    QSet<QString> seenSignals_;
    QTimer summaryFlushTimer_;
    QTimer soundTimer_;
    QAudioSink *audioSink_ = nullptr;
    QBuffer *audioBuffer_ = nullptr;
    QByteArray audioData_;
    QString soundPreset_ = QStringLiteral("classic");
    QString alertSoundPreset_ = QStringLiteral("classic");
    int alertSoundRepeat_ = 1;
    int soundRemaining_ = 0;
    int soundDurationMs_ = 180;
    QString lastSummaryError_;
    bool synchronized_ = false;
    bool syncBatching_ = false;
    bool replay_ = false;
    bool tradingEnabled_ = true;
    bool soundEnabled_ = true;
    bool popupEnabled_ = true;
};

} // namespace premium
