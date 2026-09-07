#include "agent/LifecycleController.h"

#include <QtTest>

namespace hub {

class LifecycleControllerTestPeer {
public:
    static void setContext(LifecycleController &controller, const QString &action,
                           const QString &target, const QJsonArray &units,
                           const QHash<QString, qint64> &previousPids = {}) {
        controller.currentAction_ = action;
        controller.currentTargetUnit_ = target;
        controller.probeUnits_ = units;
        controller.preCommandPids_ = previousPids;
        controller.commandFailed_ = false;
    }

    static QStringList plannedUnits(LifecycleController &controller) {
        controller.prepareLaunchdSteps(controller.currentAction_);
        QStringList descriptions;
        auto steps = controller.steps_;
        while (!steps.isEmpty()) descriptions.append(steps.dequeue().description);
        return descriptions;
    }

    static bool outcomeSatisfied(const LifecycleController &controller) {
        return controller.launchdOutcomeSatisfied();
    }
};

} // namespace hub

class LifecycleControllerTests final : public QObject {
    Q_OBJECT
private slots:
    void targetPlanDoesNotTouchSibling();
    void targetPostconditionIsIndependentFromGroupReadiness();
};

namespace {

hub::ModuleConfig uploadConfig() {
    hub::ModuleConfig config;
    config.id = QStringLiteral("upload");
    config.adapter = QStringLiteral("upload");
    hub::LaunchdUnit web;
    web.id = QStringLiteral("web");
    web.label = QStringLiteral("com.example.web");
    web.plistPath = QStringLiteral("/tmp/web.plist");
    web.required = true;
    hub::LaunchdUnit sina;
    sina.id = QStringLiteral("sina");
    sina.label = QStringLiteral("com.example.sina");
    sina.plistPath = QStringLiteral("/tmp/sina.plist");
    sina.required = true;
    config.launchdUnits = {web, sina};
    return config;
}

QJsonObject unit(const QString &id, bool loaded, bool running, qint64 pid) {
    return {{QStringLiteral("id"), id},
            {QStringLiteral("loaded"), loaded},
            {QStringLiteral("running"), running},
            {QStringLiteral("pid"), pid},
            {QStringLiteral("required"), true},
            {QStringLiteral("probe_authoritative"), true}};
}

} // namespace

void LifecycleControllerTests::targetPlanDoesNotTouchSibling() {
    hub::LifecycleController controller(uploadConfig());
    hub::LifecycleControllerTestPeer::setContext(
        controller, QStringLiteral("stop_service"), QStringLiteral("web"),
        QJsonArray{unit(QStringLiteral("web"), true, true, 101),
                   unit(QStringLiteral("sina"), true, true, 202)});
    QCOMPARE(hub::LifecycleControllerTestPeer::plannedUnits(controller),
             QStringList{QStringLiteral("停止 web")});

    hub::LifecycleControllerTestPeer::setContext(
        controller, QStringLiteral("stop_service"), {},
        QJsonArray{unit(QStringLiteral("web"), true, true, 101),
                   unit(QStringLiteral("sina"), true, true, 202)});
    QCOMPARE(hub::LifecycleControllerTestPeer::plannedUnits(controller),
             (QStringList{QStringLiteral("停止 sina"), QStringLiteral("停止 web")}));
}

void LifecycleControllerTests::targetPostconditionIsIndependentFromGroupReadiness() {
    hub::LifecycleController controller(uploadConfig());
    const QJsonArray partiallyRunning{
        unit(QStringLiteral("web"), true, true, 303),
        unit(QStringLiteral("sina"), false, false, 0)};
    hub::LifecycleControllerTestPeer::setContext(
        controller, QStringLiteral("start_service"), QStringLiteral("web"), partiallyRunning);
    QVERIFY(hub::LifecycleControllerTestPeer::outcomeSatisfied(controller));

    hub::LifecycleControllerTestPeer::setContext(
        controller, QStringLiteral("start_service"), {}, partiallyRunning);
    QVERIFY(!hub::LifecycleControllerTestPeer::outcomeSatisfied(controller));

    hub::LifecycleControllerTestPeer::setContext(
        controller, QStringLiteral("restart_service"), QStringLiteral("web"), partiallyRunning,
        {{QStringLiteral("web"), 101}});
    QVERIFY(hub::LifecycleControllerTestPeer::outcomeSatisfied(controller));
}

QTEST_GUILESS_MAIN(LifecycleControllerTests)
#include "tst_lifecycle_controller.moc"
