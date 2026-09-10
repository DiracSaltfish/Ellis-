#pragma once

#include "common/Config.h"

#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QPointer>
#include <QSet>
#include <QWidget>

class QLabel;
class QComboBox;
class QCheckBox;
class QDateEdit;
class QLineEdit;
class QPlainTextEdit;
class QPushButton;
class QTableWidget;
class QTabWidget;
class QTimer;
class QTreeWidget;
class QEvent;
class QCloseEvent;

namespace hub {

class PremiumHistoryLoader;

class ModulePage : public QWidget {
    Q_OBJECT
public:
    explicit ModulePage(ModuleConfig config, QWidget *parent = nullptr);
    ~ModulePage() override = default;

    QString moduleId() const { return config_.id; }
    virtual void applySnapshot(const QJsonObject &message);
    virtual void applyEvent(const QJsonObject &message);
    virtual void applyCommand(const QJsonObject &message);
    virtual void prepareForShutdown() {}
    void reconcileActiveCommands(const QSet<QString> &commandIds);

signals:
    void commandRequested(const QString &moduleId, const QString &action,
                          const QJsonObject &arguments, int deadlineMs);
    void refreshRequested(const QString &moduleId);
    void alertRequested(const QString &moduleId, const QString &title,
                        const QString &message, bool sound, bool popup);

protected:
    QWidget *buildHeader(const QString &subtitle, const QList<QWidget *> &extraActions = {});
    void setContent(QWidget *content);
    void updateControlState(const QJsonObject &snapshot);
    void appendLog(const QString &text);
    void send(const QString &action, const QJsonObject &arguments = {}, int deadlineMs = 45000);
    bool logicalControlAllowed(const QJsonObject &snapshot) const;
    virtual void updateBusyControls();

    ModuleConfig config_;
    QJsonObject lastSnapshot_;
    QLabel *stateLabel_ = nullptr;
    QLabel *freshnessLabel_ = nullptr;
    QPushButton *startButton_ = nullptr;
    QPushButton *stopButton_ = nullptr;
    QPushButton *restartButton_ = nullptr;
    QPushButton *legacyButton_ = nullptr;
    QPushButton *acknowledgeButton_ = nullptr;
    QPlainTextEdit *eventLog_ = nullptr;
    bool commandBusy_ = false;
    QSet<QString> busyCommandIds_;
    QHash<QString, int> commandStateRanks_;
    QHash<QString, qint64> commandStateTimestamps_;
};

class UploadPage final : public ModulePage {
    Q_OBJECT
public:
    explicit UploadPage(ModuleConfig config, QWidget *parent = nullptr);
    void applySnapshot(const QJsonObject &message) override;
    void applyEvent(const QJsonObject &message) override;

private:
    QTreeWidget *runtimeTree_ = nullptr;
    QTableWidget *workersTable_ = nullptr;
    QTableWidget *fundsTable_ = nullptr;
    QTableWidget *historyTable_ = nullptr;
    QTableWidget *recordsTable_ = nullptr;
    QLineEdit *navSymbolEdit_ = nullptr;
    QLineEdit *navValueEdit_ = nullptr;
    QLineEdit *sharesValueEdit_ = nullptr;
    QLineEdit *positionValueEdit_ = nullptr;
    QPlainTextEdit *messageEdit_ = nullptr;
    QLabel *workersExpectedLabel_ = nullptr;
    QPushButton *ibkrReconnectButton_ = nullptr;
    QPushButton *ibkrDisconnectButton_ = nullptr;
    QList<QPointer<QPushButton>> jobControlButtons_;

protected:
    void updateBusyControls() override;
};

class PremiumDetailWindow final : public QWidget {
    Q_OBJECT
public:
    explicit PremiumDetailWindow(QString symbol, QString name = {},
                                 QWidget *parent = nullptr);
    QString symbol() const { return symbol_; }
    void applyDetail(const QJsonObject &detail);
    void setConnectionState(const QString &state, const QString &description = {});
    void setSubscriptionAcknowledged(bool acknowledged);

signals:
    void closed(const QString &symbol);

protected:
    void closeEvent(QCloseEvent *event) override;

private:
    QString symbol_;
    QString name_;
    QLabel *connectionLabel_ = nullptr;
    QLabel *headlineLabel_ = nullptr;
    QLabel *marketLabel_ = nullptr;
    QTableWidget *bookTable_ = nullptr;
    QTreeWidget *fieldsTree_ = nullptr;
    QPlainTextEdit *rawView_ = nullptr;
    bool closePublished_ = false;
};

class PremiumPage final : public ModulePage {
    Q_OBJECT
public:
    explicit PremiumPage(ModuleConfig config, QWidget *parent = nullptr);
    void applySnapshot(const QJsonObject &message) override;
    void applyEvent(const QJsonObject &message) override;
    void prepareForShutdown() override;

private:
    bool addSignal(const QJsonObject &payload);
    void upsertSummary(const QJsonObject &payload);
    void flushPendingSummaries();
    void openDetail(const QJsonObject &payload);
    void populateHistory(const QJsonArray &records, const QJsonObject &statistics);
    void renderHistoryPage(int page);

    QTableWidget *signalsTable_ = nullptr;
    QTableWidget *summariesTable_ = nullptr;
    QTreeWidget *runtimeTree_ = nullptr;
    QPlainTextEdit *watchlistEdit_ = nullptr;
    QPlainTextEdit *hotlistEdit_ = nullptr;
    QLabel *syncLabel_ = nullptr;
    PremiumHistoryLoader *historyLoader_ = nullptr;
    QDateEdit *historyFrom_ = nullptr;
    QDateEdit *historyTo_ = nullptr;
    QLineEdit *historySymbol_ = nullptr;
    QComboBox *historyModel_ = nullptr;
    QLabel *historyStatus_ = nullptr;
    QTableWidget *historyTable_ = nullptr;
    QJsonArray historyRecords_;
    QJsonObject historyStatistics_;
    QPushButton *historyPreviousButton_ = nullptr;
    QPushButton *historyNextButton_ = nullptr;
    int historyPage_ = 0;
    QCheckBox *soundEnabled_ = nullptr;
    QCheckBox *popupEnabled_ = nullptr;
    QList<QPushButton *> mutationButtons_;
    QHash<QString, QPointer<PremiumDetailWindow>> detailWindows_;
    QHash<QString, QJsonObject> pendingSummaries_;
    QHash<QString, int> summaryRows_;
    QTimer *summaryRenderTimer_ = nullptr;

protected:
    void updateBusyControls() override;
};

class WebullPage final : public ModulePage {
    Q_OBJECT
public:
    explicit WebullPage(ModuleConfig config, QWidget *parent = nullptr);
    void applySnapshot(const QJsonObject &message) override;
    void applyEvent(const QJsonObject &message) override;

private:
    void updateBook(const QJsonObject &book);
    void updateClients(const QJsonValue &clients);

    QLineEdit *symbolEdit_ = nullptr;
    QTableWidget *bookTable_ = nullptr;
    QTableWidget *clientsTable_ = nullptr;
    QTreeWidget *runtimeTree_ = nullptr;
    QLabel *staleLabel_ = nullptr;
    QList<QPushButton *> mutationButtons_;

protected:
    void updateBusyControls() override;
};

class PcfDetailWindow;

class RealtimePage final : public ModulePage {
    Q_OBJECT
public:
    explicit RealtimePage(ModuleConfig config, QWidget *parent = nullptr);
    void applySnapshot(const QJsonObject &message) override;
    void applyEvent(const QJsonObject &message) override;
    void applyCommand(const QJsonObject &message) override;

private:
    void updateRows(const QJsonValue &snapshot, bool fullSnapshot);
    void updateMutationControlState(const QJsonObject &payload);
    void openPcfForRow(int row);

    QTableWidget *snapshotTable_ = nullptr;
    QTableWidget *historyTable_ = nullptr;
    QTreeWidget *runtimeTree_ = nullptr;
    QPlainTextEdit *watchlistEdit_ = nullptr;
    QLineEdit *nameSymbolEdit_ = nullptr;
    QLineEdit *symbolNameEdit_ = nullptr;
    QLineEdit *historyDateEdit_ = nullptr;
    QLineEdit *historySymbolEdit_ = nullptr;
    QList<QPushButton *> mutationButtons_;
    QHash<QString, QJsonObject> realtimeItems_;
    QJsonObject qmtBackends_;
    bool mutationAllowed_ = false;
    QHash<QString, QPointer<PcfDetailWindow>> pcfWindows_;
    QCheckBox *soundEnabled_ = nullptr;
    QCheckBox *popupEnabled_ = nullptr;

protected:
    void updateBusyControls() override;
};

class PcfDetailWindow final : public QWidget {
    Q_OBJECT
public:
    explicit PcfDetailWindow(QString symbol, QWidget *parent = nullptr);
    void applyData(const QJsonObject &object);
    void applyQmtTelemetry(const QJsonObject &backends);
    void setMutationEnabled(bool enabled);
    void setLiveOrdersEnabled(bool enabled);
    QString symbol() const { return symbol_; }

signals:
    void qmtCommandRequested(const QString &action, const QJsonObject &arguments,
                             int deadlineMs);

protected:
    bool eventFilter(QObject *watched, QEvent *event) override;

private:
    struct QmtWidgets {
        QLabel *state = nullptr;
        QTreeWidget *runtime = nullptr;
        QTableWidget *positions = nullptr;
        QTableWidget *orders = nullptr;
        QPlainTextEdit *lastResult = nullptr;
        QPushButton *connectButton = nullptr;
        QPushButton *disconnectButton = nullptr;
        QPushButton *syncButton = nullptr;
        QPushButton *purchaseButton = nullptr;
        QPushButton *redeemButton = nullptr;
    };

    QWidget *buildQmtTab(const QString &backend);
    void updateQmtTab(const QString &backend, const QJsonObject &snapshot);

    QString symbol_;
    QLabel *status_ = nullptr;
    QPlainTextEdit *summary_ = nullptr;
    QPlainTextEdit *components_ = nullptr;
    QHash<QString, QmtWidgets> qmtWidgets_;
    bool mutationEnabled_ = false;
    bool liveOrdersEnabled_ = false;
};

} // namespace hub
