#include "common/BridgeFrame.h"
#include "common/SnapshotParser.h"
#include "tgw/RawEventValidator.h"
#include "tgw/RawEventExtractor.h"
#include "tgw/SubscriptionPlan.h"

#include <QtTest>

using namespace premium;
using namespace premium::native_tgw;

namespace {

BridgeFrame marketFrame(const QByteArray &payload, const RawEventMetadata &metadata,
                        quint64 sequence = 1)
{
    BridgeFrame frame;
    frame.kind = BridgeFrame::Kind::MarketEvent;
    frame.sequence = sequence;
    frame.sessionId = QStringLiteral("native-fixture-session");
    frame.receiveWallNs = 1'787'798'235'495'000'000LL;
    frame.receiveMonotonicNs = 123'456'789;
    frame.isDelta = metadata.isDelta;
    frame.tag = metadata.tag;
    frame.payloadJson = payload;
    return frame;
}

const QByteArray DomesticFull = QByteArrayLiteral(
    R"({"headers":{"tag":"14"},"status":0,"is_delta":0,"data":{"1":101,"2":"513770","3":2,"4":20260827103715638,"5":"T111\u0000\u0000\u0000","6":355000,"7":355000,"8":356000,"9":352000,"10":353000,"11":0,"12":"352000|351000|350000|349000|348000|0|0|0|0|0","13":"7526344300|10313990000|8412530000|3399200000|2796820000|0|0|0|0|0","14":"353000|354000|355000|356000|357000|0|0|0|0|0","15":"5521094300|7931340000|4650860000|2294690000|2300970000|0|0|0|0|0","16":2982,"17":25117431400,"18":8873547700000,"19":353000,"20":391000,"21":320000}})"
);

const QByteArray DomesticDelta = QByteArrayLiteral(
    R"({"headers":{"tag":"14"},"status":0,"is_delta":1,"data":{"1":101,"2":"513770","4":20260827103721705,"15":"5521094300|7929340000|4653750000|2295190000|2300970000|0|0|0|0|0","19":352900}})"
);

const QByteArray HktFull = QByteArrayLiteral(
    R"({"headers":{"tag":"16"},"status":0,"is_delta":0,"data":{"1":102,"2":"02800","3":20260827154936000,"4":"T0\u0000\u0000\u0000\u0000\u0000","5":74786891300,"6":1952183035644000,"7":26140000,"8":26080000,"9":26300000,"10":26040000,"11":26080000,"12":"26080000|0|0|0|0","13":"1617100000|0|0|0|0","14":"26100000|0|0|0|0","15":"938450000|0|0|0|0","16":0,"17":0,"18":0,"19":0,"20":0,"21":0,"22":0,"23":6}})"
);

} // namespace

class NativeTgwTests final : public QObject {
    Q_OBJECT
private Q_SLOTS:
    void subscriptionMappingIsExact()
    {
        QString error;
        const auto sh = subscriptionItemForSymbol(QStringLiteral("513770.SH"), &error);
        QVERIFY2(sh.has_value(), qPrintable(error));
        QCOMPARE(sh->market, 101);
        QCOMPARE(sh->flag, quint64{10});
        QCOMPARE(QString::fromStdString(sh->security_code), QStringLiteral("513770"));
        QCOMPARE(sh->category_type, quint8{0});

        const auto sz = subscriptionItemForSymbol(QStringLiteral("159866.SZ"), &error);
        QVERIFY(sz.has_value());
        QCOMPARE(sz->market, 102);
        QCOMPARE(sz->flag, quint64{10});

        const auto hk = subscriptionItemForSymbol(QStringLiteral("02800.HK"), &error);
        QVERIFY(hk.has_value());
        QCOMPARE(hk->market, 102);
        QCOMPARE(hk->flag, quint64{12});
        QCOMPARE(QString::fromStdString(hk->security_code), QStringLiteral("02800"));
        QVERIFY(!subscriptionItemForSymbol(QStringLiteral("2800.HK"), &error).has_value());
        QVERIFY(!subscriptionItemForSymbol(QStringLiteral("02800.SZ"), &error).has_value());
    }

    void capturedDomesticTypesAndValuesRemainExact()
    {
        RawEventMetadata metadata;
        QString error;
        QVERIFY2(inspectRawEvent(DomesticFull, &metadata, &error), qPrintable(error));
        QCOMPARE(metadata.tag, QStringLiteral("14"));
        QCOMPARE(metadata.symbol, QStringLiteral("513770.SH"));
        QVERIFY(!metadata.isDelta);
        QVERIFY(metadata.numericSchema);

        SnapshotParser parser;
        const auto result = parser.consume(marketFrame(DomesticFull, metadata));
        QVERIFY(result.snapshot.has_value());
        const QuoteSnapshot &snapshot = *result.snapshot;
        QCOMPARE(snapshot.origTime, 20'260'827'103'715'638LL);
        QCOMPARE(snapshot.bidVolumesE2[1], 10'313'990'000LL);
        QCOMPARE(snapshot.askVolumesE2[0], 5'521'094'300LL);
        QCOMPARE(snapshot.totalVolumeE2, 25'117'431'400LL);
        QCOMPARE(snapshot.totalAmountE5, 8'873'547'700'000LL);
        QCOMPARE(snapshot.iopvE6, 353'000);
        QCOMPARE(snapshot.mappingVersion, QStringLiteral("numeric-live-20260827-UNVERIFIED"));
    }

    void capturedDeltaMergesWithoutPythonCoercion()
    {
        RawEventMetadata full;
        RawEventMetadata delta;
        QString error;
        QVERIFY2(inspectRawEvent(DomesticFull, &full, &error), qPrintable(error));
        QVERIFY2(inspectRawEvent(DomesticDelta, &delta, &error), qPrintable(error));
        QVERIFY(delta.isDelta);
        SnapshotParser parser;
        QVERIFY(parser.consume(marketFrame(DomesticFull, full)).snapshot.has_value());
        const auto merged = parser.consume(marketFrame(DomesticDelta, delta, 2));
        QVERIFY(merged.snapshot.has_value());
        QCOMPARE(merged.snapshot->lastPriceE6, 353'000);
        QCOMPARE(merged.snapshot->iopvE6, 352'900);
        QCOMPARE(merged.snapshot->openPriceE6, 355'000);
        QCOMPARE(merged.snapshot->totalAmountE5, 8'873'547'700'000LL);
    }

    void seventeenDigitHktTimeAboveDoublePrecisionIsExact()
    {
        RawEventMetadata metadata;
        QString error;
        QVERIFY2(inspectRawEvent(HktFull, &metadata, &error), qPrintable(error));
        QCOMPARE(metadata.tag, QStringLiteral("16"));
        QCOMPARE(metadata.symbol, QStringLiteral("02800.HK"));
        SnapshotParser parser;
        const auto result = parser.consume(marketFrame(HktFull, metadata));
        QVERIFY2(result.snapshot.has_value(), qPrintable(result.issues.join(u',')));
        QCOMPARE(result.snapshot->origTime, 20'260'827'154'936'000LL);
        QCOMPARE(result.snapshot->totalAmountE5, 1'952'183'035'644'000LL);
        QCOMPARE(result.snapshot->totalVolumeE2, 74'786'891'300LL);
        QCOMPARE(result.snapshot->bidPricesE6[0], 26'080'000);
        QCOMPARE(result.snapshot->askVolumesE2[0], 938'450'000);
    }

    void rawBytesAreNotReserialized()
    {
        RawEventMetadata metadata;
        QString error;
        QVERIFY2(inspectRawEvent(DomesticFull, &metadata, &error), qPrintable(error));
        const BridgeFrame frame = marketFrame(DomesticFull, metadata);
        QCOMPARE(frame.payloadJson, DomesticFull);
    }

    void persistedWrapperExtractionPreservesExactLexicalBytes()
    {
        const QByteArray wrapper = QByteArrayLiteral("{\"adapter_seq\":7,\"event\":")
            + DomesticFull + QByteArrayLiteral(",\"tag\":\"14\"}");
        ExtractedRawEvent extracted;
        QString error;
        QVERIFY2(extractRawEvent(wrapper, &extracted, &error), qPrintable(error));
        QVERIFY(extracted.wrapped);
        QCOMPARE(QByteArray(extracted.payload.data(), extracted.payload.size()), DomesticFull);

        ExtractedRawEvent direct;
        QVERIFY(extractRawEvent(DomesticFull, &direct, &error));
        QVERIFY(!direct.wrapped);
        QCOMPARE(QByteArray(direct.payload.data(), direct.payload.size()), DomesticFull);

        const QByteArray floatingWrapper = QByteArrayLiteral(
            R"({"event":{"headers":{"tag":"14"},"status":0.0,"is_delta":1,"data":{"1":102,"2":"159866","4":20260827103718000}}})"
        );
        QVERIFY(extractRawEvent(floatingWrapper, &extracted, &error));
        RawEventMetadata metadata;
        QVERIFY(!inspectRawEvent(extracted.payload, &metadata, &error));
        QVERIFY(error.contains(QStringLiteral("status")));
    }

    void automaticStringAndFloatCoercionsAreRejected()
    {
        RawEventMetadata metadata;
        QString error;
        const QByteArray stringTime = QByteArrayLiteral(
            R"({"headers":{"tag":"14"},"status":0,"is_delta":1,"data":{"1":102,"2":"159866","4":"20260827103718000"}})"
        );
        QVERIFY(!inspectRawEvent(stringTime, &metadata, &error));
        QVERIFY(error.contains(QStringLiteral("data.4")));

        const QByteArray fractional = QByteArrayLiteral(
            R"({"headers":{"tag":"14"},"status":0,"is_delta":1,"data":{"1":102,"2":"159866","19":1631400.5}})"
        );
        QVERIFY(!inspectRawEvent(fractional, &metadata, &error));
        QVERIFY(error.contains(QStringLiteral("data.19")));

        const QByteArray integralLookingFloat = QByteArrayLiteral(
            R"({"headers":{"tag":"14"},"status":0.0,"is_delta":1,"data":{"1":102,"2":"159866","19":1631400.0}})"
        );
        QVERIFY(!inspectRawEvent(integralLookingFloat, &metadata, &error));
        QVERIFY(error.contains(QStringLiteral("status")));

        const QByteArray numericCode = QByteArrayLiteral(
            R"({"headers":{"tag":"16"},"status":0,"is_delta":1,"data":{"1":102,"2":2800,"3":20260827154936000}})"
        );
        QVERIFY(!inspectRawEvent(numericCode, &metadata, &error));
        QVERIFY(error.contains(QStringLiteral("data.2")));

        const QByteArray booleanDelta = QByteArrayLiteral(
            R"({"headers":{"tag":"14"},"status":0,"is_delta":true,"data":{"1":102,"2":"159866","4":20260827103718000}})"
        );
        QVERIFY(!inspectRawEvent(booleanDelta, &metadata, &error));
        QVERIFY(error.contains(QStringLiteral("is_delta")));

        const QByteArray numericTag = QByteArrayLiteral(
            R"({"headers":{"tag":14},"status":0,"is_delta":1,"data":{"1":102,"2":"159866","4":20260827103718000}})"
        );
        QVERIFY(!inspectRawEvent(numericTag, &metadata, &error));
        QVERIFY(error.contains(QStringLiteral("headers.tag")));
    }

    void observedSchemaIsPinnedAndIncompleteFullIsRejected()
    {
        RawEventMetadata metadata;
        QString error;
        const QByteArray unknownField = QByteArrayLiteral(
            R"({"headers":{"tag":"14"},"status":0,"is_delta":1,"data":{"1":102,"2":"159866","4":20260827103718000,"future_wrapper":{"value":1}}})"
        );
        QVERIFY(!inspectRawEvent(unknownField, &metadata, &error));
        QVERIFY(error.contains(QStringLiteral("unsupported data field")));

        const QByteArray missingDeltaTime = QByteArrayLiteral(
            R"({"headers":{"tag":"14"},"status":0,"is_delta":1,"data":{"1":102,"2":"159866","19":1631400}})"
        );
        QVERIFY(!inspectRawEvent(missingDeltaTime, &metadata, &error));
        QVERIFY(error.contains(QStringLiteral("required JSON field 4")));

        const QByteArray incompleteFull = QByteArrayLiteral(
            R"({"headers":{"tag":"14"},"status":0,"is_delta":0,"data":{"1":102,"2":"159866","4":20260827103718000}})"
        );
        QVERIFY(!inspectRawEvent(incompleteFull, &metadata, &error));
        QVERIFY(error.contains(QStringLiteral("required JSON field")));
    }
};

QTEST_GUILESS_MAIN(NativeTgwTests)
#include "test_native_tgw.moc"
