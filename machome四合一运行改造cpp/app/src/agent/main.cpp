#include "agent/HubAgent.h"
#include "common/Config.h"

#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QLockFile>
#include <QSocketNotifier>
#include <QTextStream>
#include <QTimer>

#ifdef Q_OS_UNIX
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <unistd.h>

namespace {
volatile sig_atomic_t signalWriteFd = -1;

void requestQtShutdown(int signalNumber) {
    const int savedErrno = errno;
    const unsigned char value = static_cast<unsigned char>(signalNumber);
    const int fd = static_cast<int>(signalWriteFd);
    if (fd >= 0) {
        const ssize_t ignored = ::write(fd, &value, sizeof(value));
        Q_UNUSED(ignored)
    }
    errno = savedErrno;
}
} // namespace
#endif

int main(int argc, char *argv[]) {
    QCoreApplication application(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("machome-hub-agent"));
    QCoreApplication::setApplicationVersion(QStringLiteral("1.0.0"));
    QCoreApplication::setOrganizationName(QStringLiteral("Ellis"));
    QCoreApplication::setOrganizationDomain(QStringLiteral("ellis.local"));

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Machome 四合一运行中心后台 Agent"));
    parser.addHelpOption();
    parser.addVersionOption();
    QCommandLineOption configOption({QStringLiteral("c"), QStringLiteral("config")},
                                    QStringLiteral("模块配置 JSON"), QStringLiteral("path"),
                                    hub::AppConfig::defaultConfigPath());
    QCommandLineOption checkOption(QStringLiteral("check-config"), QStringLiteral("校验配置后退出"));
    QCommandLineOption runForOption(QStringLiteral("run-for"), QStringLiteral("运行指定毫秒后退出（测试用）"),
                                   QStringLiteral("ms"));
    parser.addOption(configOption);
    parser.addOption(checkOption);
    parser.addOption(runForOption);
    parser.process(application);

    QString error;
    const auto config = hub::AppConfig::load(parser.value(configOption), &error);
    if (!config.isValid(&error)) {
        QTextStream(stderr) << error << Qt::endl;
        return 2;
    }
    if (parser.isSet(checkOption)) {
        QTextStream(stdout) << "config ok: " << config.modules.size() << " modules" << Qt::endl;
        return 0;
    }

    const QFileInfo socketInfo(config.socketPath);
    QDir socketDirectory(socketInfo.absolutePath());
    if ((!socketDirectory.exists() && !socketDirectory.mkpath(QStringLiteral(".")))
        || !QFile::setPermissions(socketDirectory.absolutePath(),
                                  QFileDevice::ReadOwner | QFileDevice::WriteOwner
                                      | QFileDevice::ExeOwner)) {
        QTextStream(stderr) << "cannot create or secure agent runtime directory: "
                            << socketDirectory.absolutePath() << Qt::endl;
        return 3;
    }

    QLockFile lock(config.socketPath + QStringLiteral(".lock"));
    // A hard crash must not leave the control plane permanently unavailable.
    // QLockFile also checks whether the recorded PID is alive; this timeout is
    // the conservative fallback for stale metadata/host identity changes.
    lock.setStaleLockTime(30'000);
    if (!lock.tryLock(100)) {
        const QString reason = lock.error() == QLockFile::LockFailedError
            ? QStringLiteral("another agent holds the lock")
            : lock.error() == QLockFile::PermissionError
                ? QStringLiteral("runtime lock permission denied")
                : QStringLiteral("runtime lock creation failed");
        QTextStream(stderr) << reason << ": " << lock.error() << Qt::endl;
        return 3;
    }

    hub::HubAgent agent(config);
    if (!agent.start(&error)) {
        QTextStream(stderr) << error << Qt::endl;
        return 4;
    }
    QObject::connect(&application, &QCoreApplication::aboutToQuit, &agent, &hub::HubAgent::shutdown);
#ifdef Q_OS_UNIX
    int signalPipe[2] = {-1, -1};
    if (::pipe(signalPipe) != 0
        || ::fcntl(signalPipe[0], F_SETFL,
                   ::fcntl(signalPipe[0], F_GETFL) | O_NONBLOCK) < 0
        || ::fcntl(signalPipe[1], F_SETFL,
                   ::fcntl(signalPipe[1], F_GETFL) | O_NONBLOCK) < 0
        || ::fcntl(signalPipe[0], F_SETFD, FD_CLOEXEC) < 0
        || ::fcntl(signalPipe[1], F_SETFD, FD_CLOEXEC) < 0) {
        QTextStream(stderr) << "cannot create POSIX shutdown self-pipe" << Qt::endl;
        agent.shutdown();
        if (signalPipe[0] >= 0) ::close(signalPipe[0]);
        if (signalPipe[1] >= 0) ::close(signalPipe[1]);
        return 5;
    }
    signalWriteFd = signalPipe[1];
    struct sigaction action {};
    action.sa_handler = requestQtShutdown;
    sigemptyset(&action.sa_mask);
    action.sa_flags = SA_RESTART;
    if (::sigaction(SIGTERM, &action, nullptr) != 0
        || ::sigaction(SIGINT, &action, nullptr) != 0) {
        QTextStream(stderr) << "cannot install POSIX shutdown handlers" << Qt::endl;
        signalWriteFd = -1;
        ::close(signalPipe[0]);
        ::close(signalPipe[1]);
        agent.shutdown();
        return 5;
    }
    QSocketNotifier shutdownNotifier(signalPipe[0], QSocketNotifier::Read, &application);
    QObject::connect(&shutdownNotifier, &QSocketNotifier::activated,
                     &application, [&application, readFd = signalPipe[0]](qintptr) {
        unsigned char buffer[64];
        while (::read(readFd, buffer, sizeof(buffer)) > 0) {}
        QMetaObject::invokeMethod(&application, &QCoreApplication::quit,
                                  Qt::QueuedConnection);
    });
    QObject::connect(&application, &QCoreApplication::aboutToQuit,
                     &application, [readFd = signalPipe[0], writeFd = signalPipe[1]] {
        signalWriteFd = -1;
        ::close(readFd);
        ::close(writeFd);
    });
#endif
    if (parser.isSet(runForOption)) {
        bool ok = false;
        const int milliseconds = parser.value(runForOption).toInt(&ok);
        if (ok && milliseconds > 0) QTimer::singleShot(milliseconds, &application, &QCoreApplication::quit);
    }
    return application.exec();
}
