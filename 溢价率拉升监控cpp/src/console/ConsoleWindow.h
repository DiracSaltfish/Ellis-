#pragma once

#include <QDateTime>
#include <QMainWindow>
#include <QJsonObject>
#include <QProcess>
#include <QHash>
#include <QSet>
#include <QStringList>
#include <QTimer>
#include <QWebSocket>

class QLabel;
class QPlainTextEdit;
class QSystemTrayIcon;
class QComboBox;
class QCheckBox;
class QDateEdit;
class QLineEdit;
class QTabWidget;
class QTableWidget;
template <typename T> class QFutureWatcher;

namespace premium {

class ConsoleWindow final : public QMainWindow {
    Q_OBJECT
public:
    explicit ConsoleWindow(QString projectRoot, bool autoStart, QWidget *parent = nullptr);
    ~ConsoleWindow() override;

protected:
    void closeEvent(QCloseEvent *event) override;

private:
    void buildUi();
    void startServices();
    void stopServices();
    void restartServices();
    void startCore();
    void startAdapter();
    void handleFinished(const QString &name, int exitCode, QProcess::ExitStatus status);
    void appendLog(const QString &source, const QByteArray &bytes);
    void updateState();
    void connectMetrics();
    void startReplay();
    void validateConfiguration();
    void loadWatchlistEditor();
    void loadHotlistEditor();
    void refreshWatchlistTable();
    void refreshHotlistTable();
    bool persistWatchlist();
    bool persistHotlist();
    void sendWatchlistToCore();
    void sendHotlistToCore();
    void addWatchSymbol();
    void addHotSymbol();
    void removeSelectedWatchSymbols();
    void removeSelectedHotSymbols();
    Q_INVOKABLE void handleMetricsMessage(const QString &message);
    void processSignal(const QJsonObject &signal);
    void appendSignalRow(QTableWidget *table, const QJsonObject &signal, bool prepend);
    void showSignalDetails(QTableWidget *table, int row);
    void loadSignalHistory();
    void exportSignalHistory();
    QString dataDirectory() const;
    void startSignalSound();
    bool permitAutomaticRestart();
    QString executablePath(const QString &name) const;

    QString root_;
    QProcess core_;
    QProcess adapter_;
    QLabel *coreState_ = nullptr;
    QLabel *adapterState_ = nullptr;
    QLabel *restartState_ = nullptr;
    QLabel *metricsState_ = nullptr;
    QPlainTextEdit *log_ = nullptr;
    QLineEdit *watchCode_ = nullptr;
    QComboBox *watchMarket_ = nullptr;
    QTableWidget *watchTable_ = nullptr;
    QLineEdit *hotCode_ = nullptr;
    QComboBox *hotMarket_ = nullptr;
    QTableWidget *hotTable_ = nullptr;
    QTabWidget *workspaceTabs_ = nullptr;
    QTableWidget *signalTable_ = nullptr;
    QTableWidget *historyTable_ = nullptr;
    QLabel *signalStatus_ = nullptr;
    QLabel *historyStatus_ = nullptr;
    QCheckBox *soundEnabled_ = nullptr;
    QCheckBox *popupEnabled_ = nullptr;
    QDateEdit *historyFrom_ = nullptr;
    QDateEdit *historyTo_ = nullptr;
    QLineEdit *historySymbol_ = nullptr;
    QComboBox *historyModel_ = nullptr;
    QFutureWatcher<QList<QJsonObject>> *historyWatcher_ = nullptr;
    QStringList watchSymbols_;
    QStringList hotSymbols_;
    QHash<QString, QString> watchNames_;
    QHash<QString, QJsonObject> latestSummaries_;
    QSet<QString> seenSignals_;
    QSystemTrayIcon *tray_ = nullptr;
    QList<QDateTime> restartTimes_;
    QWebSocket metrics_;
    QProcess replayProcess_;
    QTimer signalSoundTimer_;
    int signalSoundRemaining_ = 0;
    int liveSignalCount_ = 0;
    bool intentionalStop_ = false;
    bool fault_ = false;
    bool forceQuotesRequested_ = false;
    bool replayRequested_ = false;
    bool attachOnly_ = false;
};

} // namespace premium
