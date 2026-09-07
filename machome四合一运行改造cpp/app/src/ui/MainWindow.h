#pragma once

#include "common/Config.h"

#include <QHash>
#include <QJsonObject>
#include <QMainWindow>
#include <QPointer>
#include <QThread>

class QLabel;
class QListWidget;
class QComboBox;
class QPushButton;
class QStackedWidget;
class QSystemTrayIcon;
class QTableWidget;

namespace hub {

class HubClient;
class ModulePage;

class MainWindow final : public QMainWindow {
    Q_OBJECT
public:
    enum class StartMode { Normal, OfflinePreview };
    MainWindow(AppConfig config, QString configPath, QString agentProgram,
               QWidget *parent = nullptr, StartMode startMode = StartMode::Normal);
    ~MainWindow() override;

protected:
    void closeEvent(QCloseEvent *event) override;

private slots:
    void onConnection(bool connected, const QString &description);
    void onHello(const QJsonObject &message);
    void onSnapshot(const QJsonObject &message);
    void onEvent(const QJsonObject &message);
    void onCommand(const QJsonObject &message);
    void onProtocolError(const QString &message);
    void onBinding(bool controlReady, const QString &description);
    void requestCommand(const QString &moduleId, const QString &action,
                        const QJsonObject &arguments, int deadlineMs);

signals:
    void agentReady();
    void startClient();
    void stopClient();
    void refreshClient(const QString &moduleId);
    void commandClient(const QString &moduleId, const QString &action,
                       const QJsonObject &arguments, int deadlineMs,
                       qint64 expectedRevision, const QString &reason);
    void criticalEventRendered(qint64 auditEventId, const QString &auditEpoch,
                               quint64 deliveryGeneration);

private:
    struct CardWidgets {
        QWidget *card = nullptr;
        QLabel *state = nullptr;
        QLabel *headline = nullptr;
        QLabel *metric = nullptr;
        QLabel *attention = nullptr;
        QPushButton *control = nullptr;
        int pageIndex = 0;
    };

    QWidget *buildOverview();
    QWidget *buildCard(const ModuleConfig &module, int pageIndex);
    ModulePage *createModulePage(const ModuleConfig &module);
    void updateCard(const QString &moduleId, const QJsonObject &message);
    void appendEventRow(const QJsonObject &message, const QString &kindOverride = {});
    QString moduleDisplayName(const QString &moduleId) const;

    AppConfig config_;
    QListWidget *navigation_ = nullptr;
    QStackedWidget *pages_ = nullptr;
    QLabel *agentState_ = nullptr;
    QLabel *agentIdentity_ = nullptr;
    QLabel *clock_ = nullptr;
    QComboBox *operatingMode_ = nullptr;
    QLabel *operatingModeState_ = nullptr;
    QPushButton *applyOperatingMode_ = nullptr;
    QTableWidget *events_ = nullptr;
    QHash<QString, CardWidgets> cards_;
    QHash<QString, ModulePage *> modulePages_;
    QHash<QString, ModuleConfig> moduleConfigs_;
    QHash<QString, qint64> moduleRevisions_;
    QHash<QString, QString> moduleOperatingModes_;
    HubClient *client_ = nullptr;
    QThread *clientThread_ = nullptr;
    QSystemTrayIcon *tray_ = nullptr;
    bool quitting_ = false;
    bool controlPlaneReady_ = false;
};

} // namespace hub
