/**
 * 基金成交概况-每日概况
 */

// 时间格式化
function dateReplace(date) {
    return date ?
        date.substring(0, 4) +
        "-" +
        date.substring(4, 6) +
        "-" +
        date.substring(6) :
        "-";
}

var overviewDay = {
    overviewDayUrl: sseQueryURL + "commonQuery.do?jsonCallBack=?",
    overviewDayParams: {
        // 'sqlId': 'COMMON_SSE_SJ_GPSJ_CJGK_DAYCJGK_C',
        sqlId: "COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C",
        // 'searchDate': '',
        SEARCH_DATE: "",
        PRODUCT_CODE: "05,13,16,14,15,12",
        type: "inParams",
    },
    init: function () {
        this.loadEvents();
    },
    loadEvents: function () {
        var _this = this;
        //日期范围渲染
        laydate({
            elem: ".js_date input",
            theme: "#b50005",
            format: "yyyy-MM-dd",
            trigger: "click",
            btns: ["confirm"],
            done: function (value, date, endDate) {
                //选择时间触发查询
                setTimeout(function () {
                    _this.setOverviewDayParams();
                }, 500);
            },
        });
        //点击搜索、回车触发查询
        triggerSearch(_this.setOverviewDayParams);
        //初始加载
        _this.getOverviewDayList();
    },
    setOverviewDayParams: function () {
        var _this = overviewDay;
        var searchDate = $(".js_date input").val() ? $(".js_date input").val() : "";
        $(".js_overviewDay .title_lev2 .new_date").html("数据日期：" + searchDate);
        _this.overviewDayParams.SEARCH_DATE = searchDate;
        _this.getOverviewDayList();
    },
    getOverviewDayList: function () {
        var _this = this;
        var deal_time_2 = new Date('2022-09-05').getTime();
        var emptyTr = '<tr><td colspan="18">暂无数据</td></tr>';
        var overviewDayHtml = "<thead><tr>";
        overviewDayHtml += "<th>单日情况</th>";
        overviewDayHtml += '<th class="text-right">基金</th>';
        // overviewDayHtml += '<th class="text-right">封闭式基金</th>';
        overviewDayHtml += '<th class="text-right">ETF</th>';
        overviewDayHtml += '<th class="text-right">公募REITs</th>';
        overviewDayHtml += '<th class="text-right">LOF</th>';
        overviewDayHtml += '<th id="dealFund" class="text-right" style="display:none">交易型货币基金</th>';
        overviewDayHtml += '<th class="text-right">基金回购</th>';
        overviewDayHtml += "</tr></thead><tbody>";
        getJSONP({
            type: "post",
            dataType: "jsonp",
            url: _this.overviewDayUrl,
            data: _this.overviewDayParams,
            successCallback: function (data) {
                if (data && data.result && data.result.length > 0) {
                    //初次加载 更新数据日期及插件日期
                    if (!$(".js_date input").val()) {
                        $(".js_overviewDay .title_lev2 .new_date").html(
                            "数据日期：" + dateReplace(data.result[0].TRADE_DATE)
                        );
                        $(".js_date input").val(dateReplace(data.result[0].TRADE_DATE));
                    }
                    var deal_time_1 = new Date(dateReplace(data.result[0].TRADE_DATE)).getTime();
                    var dealFundState = false;
                    if(deal_time_1 < deal_time_2){
                        dealFundState = true;
                    } else {
                        dealFundState = false;
                    }
                    var resultDatas = [];
                    $.each(data.result, function (k, v) {
                        var resultData = [];
                        //数组传入顺序固定，与表格第一列抱持一致，类型作为行数据获取标准（通过类型下标获取行数据）
                        // resultData.push(v.PRODUCT_TYPE);//基金类型
                        // resultData.push(v.TX_NUM);//挂牌数
                        // resultData.push(v.TX_VOLUME_FULL);//成交量
                        // resultData.push(v.TX_AMOUNT_FULL);//成交金额
                        // resultData.push(v.TRADING_TX_FULL);//成交笔数
                        // resultData.push(v.CAL_DATE);//日期
                        // resultDatas.push(resultData);
                        resultData.push(v.PRODUCT_CODE); //基金类型
                        resultData.push(isZero(v.LIST_NUM)); //挂牌数
                        resultData.push(isZero(v.TRADE_VOL)); //成交量
                        resultData.push(isZero(v.TRADE_AMT)); //成交金额
                        // resultData.push(v.TRADE_VOL);//成交笔数
                        // resultData.push(v.TRADE_DATE);//日期
                        resultDatas.push(resultData);
                    });
                    //横向变纵向
                    resultDatas = changearr(resultDatas);
                    //获取下标
                    var FUNDINDEX = resultDatas[0].indexOf("05"); //基金下标
                    // var CLOSEFUNDINDEX = resultDatas[0].indexOf('3');//证券投资基金（封闭式基金）下标
                    var ETFINDEX = resultDatas[0].indexOf("13"); //ETF下标
                    var LOFINDEX = resultDatas[0].indexOf("14"); //LOF下标
                    var MMFINDEX = resultDatas[0].indexOf("15"); //交易型货币基金下标
                    var REITSINDEX = resultDatas[0].indexOf("16"); //公募REITS
                    var BUYBACKFUNDINDEX = resultDatas[0].indexOf("12"); //基金回购下标
                    $.each(resultDatas, function (k, v) {
                        if (k > 0 && k < 5) {
                            overviewDayHtml += "<tr>";
                            if (k == 1) {
                                overviewDayHtml += '<td class="text-nowrap">挂牌数</td>'; //单日情况
                            } else if (k == 2) {
                                overviewDayHtml += '<td class="text-nowrap">成交量(亿份)</td>'; //单日情况
                            } else if (k == 3) {
                                overviewDayHtml +=
                                    '<td class="text-nowrap">成交金额(亿元)</td>'; //单日情况
                            }
                            // else if (k == 4) {
                            //   overviewDayHtml += '<td class="text-nowrap">成交笔数(亿股)</td>';    //单日情况
                            // }
                            overviewDayHtml +=
                                '<td class="text-right text-nowrap">' +
                                (FUNDINDEX != -1 ? v[FUNDINDEX] : "-") +
                                "</td>"; //基金
                            // overviewDayHtml += '<td class="text-right text-nowrap">' +
                            // ((CLOSEFUNDINDEX != -1) ? v[CLOSEFUNDINDEX] : '-')
                            //  + '</td>';//封闭式基金
                            overviewDayHtml +=
                                '<td class="text-right text-nowrap">' +
                                (ETFINDEX != -1 ? v[ETFINDEX] : "-") +
                                "</td>"; //ETF
                            overviewDayHtml +=
                                '<td class="text-right text-nowrap">' +
                                (REITSINDEX != -1 ? v[REITSINDEX] : "-") +
                                "</td>"; //公募REITs
                            overviewDayHtml +=
                                '<td class="text-right text-nowrap">' +
                                (LOFINDEX != -1 ? v[LOFINDEX] : "-") +
                                "</td>"; //LOF
                            if(dealFundState){
                                overviewDayHtml +=
                                '<td class="text-right text-nowrap">' +
                                (MMFINDEX != -1 ? v[MMFINDEX] : "-") +
                                "</td>"; //交易型货币基金
                            }
                            overviewDayHtml +=
                                '<td class="text-right text-nowrap">' +
                                (BUYBACKFUNDINDEX != -1 ? v[BUYBACKFUNDINDEX] : "-") +
                                "</td>"; //基金回购
                            overviewDayHtml += "</tr>";
                        }
                    });
                    overviewDayHtml += "</tbody>";
                    $(".js_overviewDay .table").html(overviewDayHtml);
                    if(dealFundState){
                        $('#dealFund').show();
                    } else {
                        $('#dealFund').hide();
                    }
                } else {
                    overviewDayHtml += emptyTr;
                    overviewDayHtml += "</tbody>";
                    $(".js_overviewDay .table").html(overviewDayHtml);
                    if($(".js_date input").val()){
                        var deal_time_1 = new Date($(".js_date input").val()).getTime();
                        if(deal_time_1 < deal_time_2){
                            $('#dealFund').show();
                        } else {
                            $('#dealFund').hide();
                        }
                    } else {
                        $('#dealFund').show();
                    }
                }
            },
            errCallback: function () {
                overviewDayHtml += emptyTr;
                overviewDayHtml += "</tbody>";
                $(".js_overviewDay .table").html(overviewDayHtml);
                if($(".js_date input").val()){
                    var deal_time_1 = new Date($(".js_date input").val()).getTime();
                    if(deal_time_1 < deal_time_2){
                        $('#dealFund').show();
                    } else {
                        $('#dealFund').hide();
                    }
                } else {
                    $('#dealFund').show();
                }
            },
        });
    },
};
if ($(".js_overviewDay").length > 0) {
    overviewDay.init();
}
//基金成交概况-每日概况 END

/**
 * 基金成交概况-每周基金情况
 */
var overviewWeekly = {
    todayDate: get_systemDate_global(),
    overviewWeeklyUrl: sseQueryURL + "commonQuery.do?jsonCallBack=?",
    isfrist: true, // 默认首次访问
    overviewWeeklyParams: {
        // 'sqlId': 'COMMON_SSE_SJ_GPSJ_CJGK_WEEKCJGK_C',
        sqlId: "COMMON_SSE_SJ_GPSJ_CJGK_MZGK_C",
        PRODUCT_CODE: "",
        // 'startDate': '',
        // 'endDate': '',
        PRODUCT_CODE: "05,13,16,14,15,12",
        START_DATE: "",
        END_DATE: "",
        type: "inParams",
    },
    init: function () {
        this.loadEvents();
    },
    loadEvents: function () {
        var _this = this;
        //日期范围渲染
        laydate({
            elem: ".js_date input",
            theme: "#b50005",
            format: "yyyy-MM-dd",
            trigger: "click",
            btns: ["confirm"],
            done: function (value, date, endDate) {
                //选择时间触发查询
                setTimeout(function () {
                    _this.setOverviewWeeklyParams();
                }, 500);
            },
        });
        //点击搜索、回车触发查询
        triggerSearch(_this.setOverviewWeeklyParams);
        //初始加载
        var dayNum = new Date(_this.todayDate).getDay();
        var day = getXDayString(_this.todayDate, 5); //获取本周五
        if (!(dayNum == 6 || dayNum == 0)) {
            var friday = new Date(day);
            friday.setDate(friday.getDate() - 7);
            day = turnDateToString(friday); //获取上周五
        }
        var monday = getXDayString(day, 1);
        var sunday = getXDayString(day, 7);
        $(".js_date input").val(day);
        $(".js_overviewWeekly .title_lev2 .new_date").html(
            "数据日期：" + monday + "至" + sunday
        );
        _this.overviewWeeklyParams.START_DATE = monday;
        _this.overviewWeeklyParams.END_DATE = sunday;
        _this.getOverviewWeeklyList();
    },
    setOverviewWeeklyParams: function () {
        var _this = overviewWeekly;
        var searchDate = $(".js_date input").val() ? $(".js_date input").val() : "";
        var monday = getXDayString(searchDate, 1);
        var sunday = getXDayString(searchDate, 7);
        $(".js_overviewWeekly .title_lev2 .new_date").html(
            "数据日期：" + monday + "至" + sunday
        );
        _this.overviewWeeklyParams.START_DATE = monday;
        _this.overviewWeeklyParams.END_DATE = sunday;
        _this.getOverviewWeeklyList();
    },
    getOverviewWeeklyList: function () {
        var _this = this;
        var deal_time_2 = new Date('2022-08-29').getTime();
        if (_this.isfrist) {
            _this.overviewWeeklyParams.START_DATE = "";
            _this.overviewWeeklyParams.END_DATE = "";
        }
        var emptyTr = '<tr><td colspan="18">暂无数据</td></tr>';
        var overviewWeeklyHtml = "<thead><tr>";
        overviewWeeklyHtml += "<th>本周情况</th>";
        overviewWeeklyHtml += '<th class="text-right">基金</th>';
        // overviewWeeklyHtml += '<th class="text-right">封闭式基金</th>';
        overviewWeeklyHtml += '<th class="text-right">ETF</th>';
        overviewWeeklyHtml += '<th class="text-right">公募REITs</th>';
        overviewWeeklyHtml += '<th class="text-right">LOF</th>';
        overviewWeeklyHtml += '<th id="dealFund" class="text-right" style="display:none">交易型货币基金</th>';
        overviewWeeklyHtml += '<th class="text-right">基金回购</th>';
        overviewWeeklyHtml += "</tr></thead><tbody>";
        getJSONP({
            type: "post",
            dataType: "jsonp",
            url: _this.overviewWeeklyUrl,
            data: _this.overviewWeeklyParams,
            successCallback: function (data) {
                if (data && data.result && data.result.length > 0) {
                    var deal_time_1 = new Date(dateReplace(data.result[0].END_DATE)).getTime();
                    var dealFundState = false;
                    if(deal_time_1 < deal_time_2){
                        dealFundState = true;
                    } else {
                        dealFundState = false;
                    }
                    var resultDatas = [];
                    $.each(data.result, function (k, v) {
                        var resultData = [];
                        //数组传入顺序固定，与表格第一列抱持一致，类型作为行数据获取标准（通过类型下标获取行数据）
                        resultData.push(
                            v.PRODUCT_CODE == "-" || v.PRODUCT_CODE == undefined ?
                            v.PRODUCT_CODE_B :
                            v.PRODUCT_CODE
                        ); //基金类型
                        resultData.push(isZero(v.LIST_NUM)); //挂牌数
                        resultData.push(isZero(v.TRADE_AMT)); //成交金额(亿元)
                        resultData.push(
                            isZero(v.HIGH_AMT) +
                            "<br>" +
                            "(" +
                            dateReplace(v.HIGH_AMT_DATE) +
                            ")"
                        ); //最高成交金额(亿元)+最高成交金额对应日期
                        resultData.push(
                            isZero(v.LOW_AMT) +
                            "<br>" +
                            "(" +
                            dateReplace(v.LOW_AMT_DATE) +
                            ")"
                        ); //最低成交金额(亿元)+最低成交金额对应日期
                        resultData.push(isZero(v.TRADE_VOL)); //成交量(亿份)
                        resultData.push(
                            isZero(v.HIGH_VOL) +
                            "<br>" +
                            "(" +
                            dateReplace(v.HIGH_VOL_DATE) +
                            ")"
                        ); //最高成交量(亿份)+最高成交量对应日期
                        resultData.push(
                            isZero(v.LOW_VOL) +
                            "<br>" +
                            "(" +
                            dateReplace(v.LOW_VOL_DATE) +
                            ")"
                        ); //最低成交量(亿份)+最低成交量对应日期
                        // resultData.push(v.TRADING_TX);//成交笔数(万笔)
                        // resultData.push(v.HGH_TRN + '<br>' + '(' + v.HGH_TRND + ')');//最高成交笔数(万笔)+最高成交笔数对应日期
                        // resultData.push(v.LOW_TRN + '<br>' + '(' + v.LOW_TRND + ')');//最低成交笔数(万笔)+最低成交笔数对应日期
                        resultData.push(v.WEEK_TRADE_DAYS); //累计交易天数(天)

                        resultDatas.push(resultData);
                    });
                    //横向变纵向
                    resultDatas = changearr(resultDatas);
                    //获取下标
                    var FUNDINDEX = resultDatas[0].indexOf("05"); //基金下标
                    //   var CLOSEFUNDINDEX = resultDatas[0].indexOf('3');//证券投资基金（封闭式基金）下标
                    var ETFINDEX = resultDatas[0].indexOf("13"); //ETF下标
                    var LOFINDEX = resultDatas[0].indexOf("14"); //LOF下标
                    var MMFINDEX = resultDatas[0].indexOf("15"); //交易型货币基金下标
                    var REITSINDEX = resultDatas[0].indexOf("16"); //公募REITs
                    var BUYBACKFUNDINDEX = resultDatas[0].indexOf("12"); //基金回购下标

                    $.each(resultDatas, function (k, v) {
                        if (k > 0 && k < 12) {
                            overviewWeeklyHtml += "<tr>";
                            if (k == 1) {
                                overviewWeeklyHtml += "<td>挂牌数</td>"; //本周情况
                            } else if (k == 2) {
                                overviewWeeklyHtml += "<td>成交金额(亿元)</td>"; //本周情况
                            } else if (k == 3) {
                                overviewWeeklyHtml += "<td>最高成交金额(亿元)</td>"; //本周情况
                            } else if (k == 4) {
                                overviewWeeklyHtml +=
                                    '<td class="text-nowrap">最低成交金额(亿元)</td>'; //本周情况
                            } else if (k == 5) {
                                overviewWeeklyHtml += "<td>成交量(亿份)</td>"; //本周情况
                            } else if (k == 6) {
                                overviewWeeklyHtml += "<td>最高成交量(亿份)</td>"; //本周情况
                            } else if (k == 7) {
                                overviewWeeklyHtml += "<td>最低成交量(亿份)</td>"; //本周情况
                            }
                            //   else if (k == 8) {
                            //     // overviewWeeklyHtml += '<td>成交笔数(万笔)</td>';    //本周情况
                            //   }
                            //   else if (k == 9) {
                            //     overviewWeeklyHtml += '<td>最高成交笔数(万笔)</td>'; //本周情况
                            //   } else if (k == 10) {
                            //     overviewWeeklyHtml += '<td>最低成交笔数(万笔)</td>'; //本周情况
                            //   }
                            else if (k == 8) {
                                overviewWeeklyHtml += "<td>累计交易天数(天)</td>"; //本周情况
                            }
                            overviewWeeklyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (FUNDINDEX != -1 ?
                                    v[FUNDINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //基金
                            //   overviewWeeklyHtml += '<td class="text-right text-nowrap">' + ((CLOSEFUNDINDEX != -1) ? v[CLOSEFUNDINDEX] : ((k > 2 && k < 11 && k != 5 && k != 8) ? '-<br/>(-)' : '-')) + '</td>';//封闭式基金
                            overviewWeeklyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (ETFINDEX != -1 ?
                                    v[ETFINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //ETF
                            overviewWeeklyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (REITSINDEX != -1 ?
                                    v[REITSINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //REITS
                            overviewWeeklyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (LOFINDEX != -1 ?
                                    v[LOFINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //LOF
                            if(dealFundState){    
                                overviewWeeklyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (MMFINDEX != -1 ?
                                    v[MMFINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //交易型货币基金
                            }
                            overviewWeeklyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (BUYBACKFUNDINDEX != -1 ?
                                    v[BUYBACKFUNDINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //基金回购
                            overviewWeeklyHtml += "</tr>";
                        }
                    });

                    overviewWeeklyHtml += "</tbody>";
                    $(".js_overviewWeekly .table").html(overviewWeeklyHtml);
                    if(dealFundState){
                        $('#dealFund').show();
                    } else {
                        $('#dealFund').hide();
                    }
                    // 传值日期控件
                    var timeipt = $(".sse_input");
                    var endDate = data.result[0].END_DATE.substr(0, 8).replace(
                        /^(\d{4})(\d{2})(\d{2})$/,
                        "$1-$2-$3"
                    );
                    if (_this.isfrist) {
                        var day = getXDayString(endDate, 5); //获取本周五
                        timeipt.val(day);
                        $(".js_overviewWeekly .title_lev2 .new_date").html(
                            "数据日期：" + getXDayString(endDate, 1) + "至" + endDate
                        );
                    }
                    _this.isfrist = false;
                } else {
                    overviewWeeklyHtml += emptyTr;
                    overviewWeeklyHtml += "</tbody>";
                    $(".js_overviewWeekly .table").html(overviewWeeklyHtml);
                    if($(".js_date input").val()){
                        var deal_time_1 = new Date($(".js_date input").val()).getTime();
                        if(deal_time_1 < deal_time_2){
                            $('#dealFund').show();
                        } else {
                            $('#dealFund').hide();
                        }
                    } else {
                        $('#dealFund').show();
                    }
                }
            },
            errCallback: function () {
                overviewWeeklyHtml += emptyTr;
                overviewWeeklyHtml += "</tbody>";
                $(".js_overviewWeekly .table").html(overviewWeeklyHtml);
                if($(".js_date input").val()){
                    var deal_time_1 = new Date($(".js_date input").val()).getTime();
                    if(deal_time_1 < deal_time_2){
                        $('#dealFund').show();
                    } else {
                        $('#dealFund').hide();
                    }
                } else {
                    $('#dealFund').show();
                }
            },
        });
    },
};
if ($(".js_overviewWeekly").length > 0) {
    overviewWeekly.init();
}
//基金成交概况-每周基金情况 END

/**
 * 基金成交概况-月度基金情况
 */
var overviewMonthly = {
    todayDate: get_systemDate_global(),
    overviewMonthlyUrl: sseQueryURL + "commonQuery.do?jsonCallBack=?",
    isfrist: true, // 默认首次访问
    overviewMonthlyParams: {
        //   'sqlId': 'COMMON_SSE_SJ_GPSJ_CJGK_MONTHCJGK_C',
        sqlId: "COMMON_SSE_SJ_GPSJ_CJGK_MYGK_C",
        PRODUCT_CODE: "05,13,16,14,15,12",
        SEARCH_DATE: "",
        type: "inParams",
    },
    init: function () {
        this.loadEvents();
    },
    loadEvents: function () {
        var _this = this;
        //日期范围渲染
        laydate({
            elem: ".js_date input",
            theme: "#b50005",
            type: "month",
            format: "yyyy-MM",
            trigger: "click",
            min: "1999-01-01",
            max: _this.todayDate,
            btns: ["confirm"],
            done: function (value, date, endDate) {
                //选择时间触发查询
                setTimeout(function () {
                    _this.setOverviewMonthlyParams();
                }, 500);
            },
        });
        //点击搜索、回车触发查询
        triggerSearch(_this.setOverviewMonthlyParams);
        //初始加载
        $(".js_date input").val(searchMonthJ);
        $(".js_overviewMonthly .title_lev2 .new_date").html(
            "数据日期：" + searchMonthJ
        );
        _this.overviewMonthlyParams.SEARCH_DATE = searchMonthJ;
        _this.getOverviewMonthlyList();
    },
    setOverviewMonthlyParams: function () {
        var _this = overviewMonthly;
        var inYear = $(".js_date input").val() ? $(".js_date input").val() : "";
        $(".js_overviewMonthly .title_lev2 .new_date").html("数据日期：" + inYear);
        _this.overviewMonthlyParams.SEARCH_DATE = inYear;
        _this.getOverviewMonthlyList();
    },
    getOverviewMonthlyList: function () {
        var _this = this;
        var deal_time_2 = new Date('2022-08-01').getTime();
        if (_this.isfrist) {
            _this.overviewMonthlyParams.SEARCH_DATE = "";
        }
        var emptyTr = '<tr><td colspan="18">暂无数据</td></tr>';
        var overviewMonthlyHtml = "<thead><tr>";
        overviewMonthlyHtml += "<th>月度情况</th>";
        overviewMonthlyHtml += '<th class="text-right">基金</th>';
        //   overviewMonthlyHtml += '<th class="text-right">封闭式基金</th>';
        overviewMonthlyHtml += '<th class="text-right">ETF</th>';
        overviewMonthlyHtml += '<th class="text-right">公募REITs</th>';
        overviewMonthlyHtml += '<th class="text-right">LOF</th>';
        overviewMonthlyHtml += '<th id="dealFund" class="text-right" style="display:none">交易型货币基金</th>';
        overviewMonthlyHtml += '<th class="text-right">基金回购</th>';
        overviewMonthlyHtml += "</tr></thead><tbody>";
        getJSONP({
            type: "post",
            dataType: "jsonp",
            url: _this.overviewMonthlyUrl,
            data: _this.overviewMonthlyParams,
            successCallback: function (data) {
                if (data && data.result && data.result.length > 0) {
                    var deal_time_1 = new Date(dateReplace(data.result[0].TRADE_DATE)).getTime();
                    var dealFundState = false;
                    if(deal_time_1 < deal_time_2){
                        dealFundState = true;
                    } else {
                        dealFundState = false;
                    }
                    var resultDatas = [];
                    $.each(data.result, function (k, v) {
                        var resultData = [];
                        //数组传入顺序固定，与表格第一列抱持一致，类型作为行数据获取标准（通过类型下标获取行数据）
                        resultData.push(
                            v.PRODUCT_CODE == "-" || v.PRODUCT_CODE == undefined ?
                            v.PRODUCT_CODE_B :
                            v.PRODUCT_CODE
                        ); //基金类型
                        resultData.push(isZero(v.LIST_NUM)); //挂牌数
                        resultData.push(isZero(v.TRADE_AMT)); //成交金额(亿元)
                        resultData.push(
                            isZero(v.M_HIGH_AMT) +
                            "<br>" +
                            "(" +
                            dateReplace(v.M_HIGH_AMT_DATE) +
                            ")"
                        ); //最高成交金额(亿元)+最高成交金额对应日期
                        resultData.push(
                            isZero(v.M_LOW_AMT) +
                            "<br>" +
                            "(" +
                            dateReplace(v.M_LOW_AMT_DATE) +
                            ")"
                        ); //最低成交金额(亿元)+最低成交金额对应日期
                        resultData.push(isZero(v.TRADE_VOL)); //成交量(亿份)
                        resultData.push(
                            isZero(v.M_HIGH_VOL) +
                            "<br>" +
                            "(" +
                            dateReplace(v.M_HIGH_VOL_DATE) +
                            ")"
                        ); //最高成交量(亿份)+最高成交量对应日期
                        resultData.push(
                            isZero(v.M_LOW_VOL) +
                            "<br>" +
                            "(" +
                            dateReplace(v.M_LOW_VOL_DATE) +
                            ")"
                        ); //最低成交量(亿份)+最低成交量对应日期
                        //   resultData.push(v.TRADING_TX);//成交笔数(万笔)
                        //   resultData.push(v.MHGH_TRN + '<br>' + '(' + v.MHGH_TRND + ')');//最高成交笔数(万笔)+最高成交笔数对应日期
                        //   resultData.push(v.MLOW_TRN + '<br>' + '(' + v.MLOW_TRND + ')');//最低成交笔数(万笔)+最低成交笔数对应日期
                        resultData.push(v.TRADE_DAYS); //累计交易天数(天)

                        resultDatas.push(resultData);
                    });
                    //横向变纵向
                    resultDatas = changearr(resultDatas);
                    //获取下标
                    var FUNDINDEX = resultDatas[0].indexOf("05"); //基金下标
                    // var CLOSEFUNDINDEX = resultDatas[0].indexOf('3');//证券投资基金（封闭式基金）下标
                    var ETFINDEX = resultDatas[0].indexOf("13"); //ETF下标
                    var LOFINDEX = resultDatas[0].indexOf("14"); //LOF下标
                    var MMFINDEX = resultDatas[0].indexOf("15"); //交易型货币基金下标
                    var REITSINDEX = resultDatas[0].indexOf("16"); //公募REITs
                    var BUYBACKFUNDINDEX = resultDatas[0].indexOf("12"); //基金回购下标

                    $.each(resultDatas, function (k, v) {
                        if (k > 0 && k < 12) {
                            overviewMonthlyHtml += "<tr>";
                            if (k == 1) {
                                overviewMonthlyHtml += "<td>挂牌数</td>"; //本周情况
                            } else if (k == 2) {
                                overviewMonthlyHtml += "<td>成交金额(亿元)</td>"; //本周情况
                            } else if (k == 3) {
                                overviewMonthlyHtml += "<td>最高成交金额(亿元)</td>"; //本周情况
                            } else if (k == 4) {
                                overviewMonthlyHtml +=
                                    '<td  class="text-nowrap">最低成交金额(亿元)</td>'; //本周情况
                            } else if (k == 5) {
                                overviewMonthlyHtml += "<td>成交量(亿份)</td>"; //本周情况
                            } else if (k == 6) {
                                overviewMonthlyHtml += "<td>最高成交量(亿份)</td>"; //本周情况
                            } else if (k == 7) {
                                overviewMonthlyHtml += "<td>最低成交量(亿份)</td>"; //本周情况
                            }
                            // else if (k == 8) {
                            //   overviewMonthlyHtml += '<td>成交笔数(万笔)</td>';    //本周情况
                            // } else if (k == 9) {
                            //   overviewMonthlyHtml += '<td>最高成交笔数(万笔)</td>';  //本周情况
                            // } else if (k == 10) {
                            //   overviewMonthlyHtml += '<td>最低成交笔数(万笔)</td>';  //本周情况
                            // }
                            else if (k == 8) {
                                overviewMonthlyHtml += "<td>累计交易天数(天)</td>"; //本周情况
                            }
                            overviewMonthlyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (FUNDINDEX != -1 ?
                                    v[FUNDINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //基金
                            // overviewMonthlyHtml += '<td class="text-right text-nowrap">' + ((CLOSEFUNDINDEX != -1) ? v[CLOSEFUNDINDEX] : ((k > 2 && k < 11 && k != 5 && k != 8) ? '-<br/>(-)' : '-')) + '</td>';//封闭式基金
                            overviewMonthlyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (ETFINDEX != -1 ?
                                    v[ETFINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //ETF
                            overviewMonthlyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (REITSINDEX != -1 ?
                                    v[REITSINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //REITS
                            overviewMonthlyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (LOFINDEX != -1 ?
                                    v[LOFINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //LOF
                            if(dealFundState){
                                overviewMonthlyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (MMFINDEX != -1 ?
                                    v[MMFINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //交易型货币基金
                            }
                            overviewMonthlyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (BUYBACKFUNDINDEX != -1 ?
                                    v[BUYBACKFUNDINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //基金回购
                            overviewMonthlyHtml += "</tr>";
                        }
                    });

                    overviewMonthlyHtml += "</tbody>";
                    $(".js_overviewMonthly .table").html(overviewMonthlyHtml);
                    if(dealFundState){
                        $('#dealFund').show();
                    } else {
                        $('#dealFund').hide();
                    }
                    // 传值日期控件
                    var timeipt = $(".sse_input");
                    if (_this.isfrist) {
                        timeipt.val(
                            data.result[0].TRADE_DATE.substr(0, 6).replace(
                                /^(\d{4})(\d{2})$/,
                                "$1-$2"
                            )
                        );
                        $(".js_overviewMonthly .title_lev2 .new_date").html(
                            "数据日期：" +
                            data.result[0].TRADE_DATE.substr(0, 6).replace(
                                /^(\d{4})(\d{2})$/,
                                "$1-$2"
                            )
                        );
                    }
                    _this.isfrist = false;
                } else {
                    overviewMonthlyHtml += emptyTr;
                    overviewMonthlyHtml += "</tbody>";
                    $(".js_overviewMonthly .table").html(overviewMonthlyHtml);
                    if($(".js_date input").val()){
                        var deal_time_1 = new Date($(".js_date input").val()).getTime();
                        if(deal_time_1 < deal_time_2){
                            $('#dealFund').show();
                        } else {
                            $('#dealFund').hide();
                        }
                    } else {
                        $('#dealFund').show();
                    }
                }
                if ($(".js_overviewMonthly .table-responsive .remarks")) {
                    $(".js_overviewMonthly .table-responsive .remarks").remove();
                }
                if (
                    _this.overviewMonthlyParams["SEARCH_DATE"] ==
                    _this.todayDate.substring(0, 7)
                ) {
                    $(".js_overviewMonthly .table").after(
                        '<div class="remarks">*本月度数据统计截止前1交易日</div>'
                    );
                }
            },
            errCallback: function () {
                overviewMonthlyHtml += emptyTr;
                overviewMonthlyHtml += "</tbody>";
                $(".js_overviewMonthly .table").html(overviewMonthlyHtml);
                if($(".js_date input").val()){
                    var deal_time_1 = new Date($(".js_date input").val()).getTime();
                    if(deal_time_1 < deal_time_2){
                        $('#dealFund').show();
                    } else {
                        $('#dealFund').hide();
                    }
                } else {
                    $('#dealFund').show();
                }
            },
        });
    },
};
if ($(".js_overviewMonthly").length > 0) {
    overviewMonthly.init();
}
//基金成交概况-月度基金情况 END

/**
 * 基金成交概况-年度基金情况
 */
var overviewYearly = {
    todayDate: get_systemDate_global(),
    overviewYearlyUrl: sseQueryURL + "commonQuery.do?jsonCallBack=?",
    isfrist: true, // 默认首次访问
    overviewYearlyParams: {
        //   'sqlId': 'COMMON_SSE_SJ_GPSJ_CJGK_YEARCJGK_C',
        sqlId: "COMMON_SSE_SJ_GPSJ_CJGK_MNGK_C",
        PRODUCT_CODE: "05,13,16,14,15,12",
        SEARCH_YEAR: "",
        type: "inParams",
    },
    init: function () {
        this.loadEvents();
    },
    loadEvents: function () {
        var _this = this;
        //日期范围渲染
        laydate({
            elem: ".js_date input",
            theme: "#b50005",
            type: "year",
            format: "yyyy",
            trigger: "click",
            min: "1999-01-01",
            max: searchYearJ,
            btns: ["confirm"],
            done: function (value, date, endDate) {
                //选择时间触发查询
                setTimeout(function () {
                    _this.setOverviewYearlyParams();
                }, 500);
            },
        });
        //点击搜索、回车触发查询
        triggerSearch(_this.setOverviewYearlyParams);
        //初始加载
        $(".js_date input").val(searchYearJ);
        $(".js_overviewYearly .title_lev2 .new_date").html(
            "数据日期：" + searchYearJ + "年"
        );
        _this.overviewYearlyParams.SEARCH_YEAR = searchYearJ;
        _this.getOverviewYearlyList();
    },
    setOverviewYearlyParams: function () {
        var _this = overviewYearly;
        var inYear = $(".js_date input").val() ? $(".js_date input").val() : "";
        $(".js_overviewYearly .title_lev2 .new_date").html(
            "数据日期：" + inYear + "年"
        );
        _this.overviewYearlyParams.SEARCH_YEAR = inYear;
        _this.getOverviewYearlyList();
    },
    getOverviewYearlyList: function () {
        var _this = this;
        if (_this.isfrist) {
            _this.overviewYearlyParams.SEARCH_YEAR = "";
        }
        var emptyTr = '<tr><td colspan="18">暂无数据</td></tr>';
        var overviewYearlyHtml = "<thead><tr>";
        overviewYearlyHtml += "<th>年度情况</th>";
        overviewYearlyHtml += '<th class="text-right">基金</th>';
        //   overviewYearlyHtml += '<th class="text-right">封闭式基金</th>';
        overviewYearlyHtml += '<th class="text-right">ETF</th>';
        overviewYearlyHtml += '<th class="text-right">公募REITs</th>';
        overviewYearlyHtml += '<th class="text-right">LOF</th>';
        // overviewYearlyHtml += '<th class="text-right">交易型货币基金</th>';
        overviewYearlyHtml += '<th class="text-right">基金回购</th>';
        overviewYearlyHtml += "</tr></thead><tbody>";
        getJSONP({
            type: "post",
            dataType: "jsonp",
            url: _this.overviewYearlyUrl,
            data: _this.overviewYearlyParams,
            successCallback: function (data) {
                if (data && data.result && data.result.length > 0) {
                    var resultDatas = [];
                    var highAmtDate = ""; //最高成交金额对应日期
                    var lowAmtDate = ""; //最低成交金额对应日期
                    var highVolDate = ""; //最高成交量对应日期
                    var lowVolDate = ""; //最低成交量对应日期

                    $.each(data.result, function (k, v) {
                        var resultData = [];
                        //数组传入顺序固定，与表格第一列抱持一致，类型作为行数据获取标准（通过类型下标获取行数据）
                        resultData.push(
                            v.PRODUCT_CODE == "-" || v.PRODUCT_CODE == undefined ?
                            v.PRODUCT_CODE_B :
                            v.PRODUCT_CODE
                        ); //基金类型
                        resultData.push(isZero(v.LIST_NUM)); //挂牌数
                        resultData.push(isZero(v.Y_TRADE_AMT)); //成交金额(亿元)
                        if (v.Y_HIGH_AMT_DATE && v.Y_HIGH_AMT_DATE != "-") {
                            highAmtDate = dateReplace(v.Y_HIGH_AMT_DATE);
                        } else {
                            highAmtDate = "-";
                        }
                        if (v.Y_LOW_AMT_DATE && v.Y_LOW_AMT_DATE != "-") {
                            lowAmtDate = dateReplace(v.Y_LOW_AMT_DATE);
                        } else {
                            lowAmtDate = "-";
                        }
                        if (v.Y_HIGH_VOL_DATE && v.Y_HIGH_VOL_DATE != "-") {
                            highVolDate = dateReplace(v.Y_HIGH_VOL_DATE);
                        } else {
                            highVolDate = "-";
                        }
                        if (v.Y_LOW_VOL_DATE && v.Y_LOW_VOL_DATE != "-") {
                            lowVolDate = dateReplace(v.Y_LOW_VOL_DATE);
                        } else {
                            lowVolDate = "-";
                        }
                        resultData.push(
                            isZero(v.Y_HIGH_AMT) + "<br>" + "(" + highAmtDate + ")"
                        ); //最高成交金额(亿元)+最高成交金额对应日期
                        resultData.push(
                            isZero(v.Y_LOW_AMT) + "<br>" + "(" + lowAmtDate + ")"
                        ); //最低成交金额(亿元)+最低成交金额对应日期
                        resultData.push(isZero(v.Y_TRADE_VOL)); //成交量(亿份)
                        resultData.push(
                            isZero(v.Y_HIGH_VOL) + "<br>" + "(" + highVolDate + ")"
                        ); //最高成交量(亿份)+最高成交量对应日期
                        resultData.push(
                            isZero(v.Y_LOW_VOL) + "<br>" + "(" + lowVolDate + ")"
                        ); //最低成交量(亿份)+最低成交量对应日期
                        //   resultData.push(v.YTRADING_TX);//成交笔数(万笔)
                        //   resultData.push(v.YHGH_TRN + '<br>' + '(' + v.YHGH_TRND + ')');//最高成交笔数(万笔)+最高成交笔数对应日期
                        //   resultData.push(v.YLOW_TRN + '<br>' + '(' + v.YLOW_TRND + ')');//最低成交笔数(万笔)+最低成交笔数对应日期
                        resultData.push(v.Y_TRADE_DAYS); //累计交易天数(天)

                        resultDatas.push(resultData);
                    });
                    //横向变纵向
                    resultDatas = changearr(resultDatas);
                    //获取下标
                    var FUNDINDEX = resultDatas[0].indexOf("05"); //基金下标
                    // var CLOSEFUNDINDEX = resultDatas[0].indexOf('3');//证券投资基金（封闭式基金）下标
                    var ETFINDEX = resultDatas[0].indexOf("13"); //ETF下标
                    var LOFINDEX = resultDatas[0].indexOf("14"); //LOF下标
                    var MMFINDEX = resultDatas[0].indexOf("15"); //交易型货币基金下标
                    var REITSINDEX = resultDatas[0].indexOf("16"); //公募REITs
                    var BUYBACKFUNDINDEX = resultDatas[0].indexOf("12"); //基金回购下标

                    $.each(resultDatas, function (k, v) {
                        if (k > 0 && k < 12) {
                            overviewYearlyHtml += "<tr>";
                            if (k == 1) {
                                overviewYearlyHtml += "<td>挂牌数</td>"; //本周情况
                            } else if (k == 2) {
                                overviewYearlyHtml += "<td>成交金额(亿元)</td>"; //本周情况
                            } else if (k == 3) {
                                overviewYearlyHtml += "<td>最高成交金额(亿元)</td>"; //本周情况
                            } else if (k == 4) {
                                overviewYearlyHtml +=
                                    '<td class="text-nowrap">最低成交金额(亿元)</td>'; //本周情况
                            } else if (k == 5) {
                                overviewYearlyHtml += "<td>成交量(亿份)</td>"; //本周情况
                            } else if (k == 6) {
                                overviewYearlyHtml += "<td>最高成交量(亿份)</td>"; //本周情况
                            } else if (k == 7) {
                                overviewYearlyHtml += "<td>最低成交量(亿份)</td>"; //本周情况
                            }
                            //  else if (k == 8) {
                            //   overviewYearlyHtml += '<td>成交笔数(万笔)</td>'; //本周情况
                            // } else if (k == 9) {
                            //   overviewYearlyHtml += '<td>最高成交笔数(万笔)</td>';   //本周情况
                            // } else if (k == 10) {
                            //   overviewYearlyHtml += '<td>最低成交笔数(万笔)</td>';   //本周情况
                            // }
                            else if (k == 8) {
                                overviewYearlyHtml += "<td>累计交易天数(天)</td>"; //本周情况
                            }
                            overviewYearlyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (FUNDINDEX != -1 ?
                                    v[FUNDINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //基金
                            // overviewYearlyHtml += '<td class="text-right text-nowrap">' + ((CLOSEFUNDINDEX != -1) ? v[CLOSEFUNDINDEX] : ((k > 2 && k < 11 && k != 5 && k != 8) ? '-<br/>(-)' : '-')) + '</td>';//封闭式基金
                            overviewYearlyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (ETFINDEX != -1 ?
                                    v[ETFINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //ETF
                            overviewYearlyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (REITSINDEX != -1 ?
                                    v[REITSINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //REITS
                            overviewYearlyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (LOFINDEX != -1 ?
                                    v[LOFINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //LOF
                            // overviewYearlyHtml +=
                            //     '<td class="text-right text-nowrap">' +
                            //     (MMFINDEX != -1 ?
                            //         v[MMFINDEX] :
                            //         k > 2 && k < 11 && k != 5 && k != 8 ?
                            //         "-<br/>(-)" :
                            //         "-") +
                            //     "</td>"; //交易型货币基金
                            overviewYearlyHtml +=
                                '<td class="text-right text-nowrap">' +
                                (BUYBACKFUNDINDEX != -1 ?
                                    v[BUYBACKFUNDINDEX] :
                                    k > 2 && k < 11 && k != 5 && k != 8 ?
                                    "-<br/>(-)" :
                                    "-") +
                                "</td>"; //基金回购
                            overviewYearlyHtml += "</tr>";
                        }
                    });

                    overviewYearlyHtml += "</tbody>";
                    $(".js_overviewYearly .table").html(overviewYearlyHtml);
                    // 传值日期控件
                    var timeipt = $(".sse_input");
                    if (_this.isfrist) {
                        timeipt.val(data.result[0].DATA_DATE.substr(0, 4));
                        $(".js_overviewYearly .title_lev2 .new_date").html(
                            "数据日期：" + data.result[0].DATA_DATE.substr(0, 4) + "年"
                        );
                    }
                    _this.isfrist = false;
                } else {
                    overviewYearlyHtml += emptyTr;
                    overviewYearlyHtml += "</tbody>";
                    $(".js_overviewYearly .table").html(overviewYearlyHtml);
                }
                if ($(".js_overviewYearly .table-responsive .remarks")) {
                    $(".js_overviewYearly .table-responsive .remarks").remove();
                }
                if (
                    _this.overviewYearlyParams["SEARCH_YEAR"] ==
                    get_systemDate_global().substring(0, 4)
                ) {
                    $(".js_overviewYearly .table").after(
                        '<div class="remarks">*本年度数据统计截止前1交易月</div>'
                    );
                }
            },
            errCallback: function () {
                overviewYearlyHtml += emptyTr;
                overviewYearlyHtml += "</tbody>";
                $(".js_overviewYearly .table").html(overviewYearlyHtml);
            },
        });
    },
};
if ($(".js_overviewYearly").length > 0) {
    overviewYearly.init();
}
//基金成交概况-年度基金情况 END

/**
 * ETF规模
 */
var etfvolumn = {
    etfvolumnUrl: sseQueryURL + "commonQuery.do?jsonCallBack=?",
    etfvolumnParams: {
        isPagination: true,
        "pageHelp.pageSize": 25,
        "pageHelp.pageNo": 1,
        "pageHelp.beginPage": 1,
        "pageHelp.cacheSize": 1,
        "pageHelp.endPage": 1,
        sqlId: "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
        STAT_DATE: "",
    },
    init: function () {
        this.loadEvents();
    },
    loadEvents: function () {
        var _this = this;
        //日期范围渲染
        $(".js_date input").attr("placeholder", "查询日期");
        laydate({
            elem: ".js_date input",
            theme: "#b50005",
            format: "yyyy-MM-dd",
            trigger: "click",
            btns: ["confirm"],
            done: function (value, date, endDate) {
                //选择时间触发查询
                setTimeout(function () {
                    _this.setEtfvolumnParams();
                }, 500);
            },
        });
        //点击搜索、回车触发查询
        triggerSearch(_this.setEtfvolumnParams);
        //初始加载
        _this.getEtfvolumnList(1);
    },
    setEtfvolumnParams: function () {
        var _this = etfvolumn;
        var STAT_DATE = $(".js_date input").val() ? $(".js_date input").val() : "";
        _this.etfvolumnParams.STAT_DATE = STAT_DATE;
        _this.getEtfvolumnList(1);
    },
    getEtfvolumnList: function (pageIndex) {
        var _this = this;
        if (!paginationChange(_this.etfvolumnParams, pageIndex)) return; //触发分页改变分页参数
        var emptyTr = '<tr><td colspan="18">暂无数据</td></tr>';
        var etfvolumnHtml = "<thead><tr>";
        etfvolumnHtml += '<th class="text-nowrap">日期</th>';
        etfvolumnHtml += '<th class="text-nowrap">基金代码</th>';
        // etfvolumnHtml += '<th class="text-nowrap">基金简称</th>';
        etfvolumnHtml += "<th>基金扩位简称</th>";
        etfvolumnHtml += '<th class="text-right">总份额（万份）</th>';
        etfvolumnHtml += "</tr></thead><tbody>";
        getJSONP({
            type: "post",
            dataType: "jsonp",
            url: _this.etfvolumnUrl,
            data: _this.etfvolumnParams,
            successCallback: function (data) {
                if (data && data.result && data.result.length > 0) {
                    if (!$(".js_date input").val()) {
                        $(".js_date input").val(data.result[0].STAT_DATE);
                    }
                    var dataList = data,
                        sec_codes = [];
                    $.each(data.result, function (k, v) {
                        sec_codes.push(v.SEC_CODE);
                    });
                    //处理基金扩位简称
                    getJSONP({
                        type: "post",
                        dataType: "jsonp",
                        url: sseQueryURL + "security/stock/queryExpandName.do?jsonCallBack=?",
                        data: {
                            secCodes: sec_codes.join(","),
                        },
                        successCallback: function (data) {
                            //数组转json对象
                            var expandData = {};
                            if (data && data.result && data.result.length > 0) {
                                $.each(data.result, function (k, v) {
                                    expandData[v[0]] = v[1];
                                });
                            }
                            $.each(dataList.result, function (k, v) {
                                etfvolumnHtml += "<tr>";
                                etfvolumnHtml +=
                                    '<td class="text-nowrap">' + v.STAT_DATE + "</td>"; //日期
                                etfvolumnHtml +=
                                    '<td class="text-nowrap">' + v.SEC_CODE + "</td>"; //基金代码
                                // etfvolumnHtml += '<td class="text-nowrap">' + v.SEC_NAME + '</td>';  //基金简称
                                etfvolumnHtml +=
                                    '<td class="text-nowrap">' +
                                    (expandData && expandData[v.SEC_CODE] ?
                                        expandData[v.SEC_CODE] :
                                        "-") +
                                    "</td>"; //基金扩位简称
                                etfvolumnHtml +=
                                    '<td class="text-right">' + v.TOT_VOL + "</td>"; //总份额（万份）
                                etfvolumnHtml += "</tr>";
                            });
                            etfvolumnHtml += "</tbody>";
                            $(".js_etfvolumn .table").html(etfvolumnHtml);
                            //调用分页
                            Page.navigation(
                                ".js_etfvolumn .pagination-box",
                                dataList.pageHelp.pageCount,
                                dataList.pageHelp.total,
                                dataList.pageHelp.pageNo,
                                dataList.pageHelp.pageSize,
                                "etfvolumn.getEtfvolumnList"
                            );
                        },
                    });
                } else {
                    etfvolumnHtml += emptyTr;
                    etfvolumnHtml += "</tbody>";
                    $(".js_etfvolumn .table").html(etfvolumnHtml);
                    $(".js_etfvolumn .pagination-box").html("");
                }
            },
            errCallback: function () {
                etfvolumnHtml += emptyTr;
                etfvolumnHtml += "</tbody>";
                $(".js_etfvolumn .table").html(etfvolumnHtml);
                $(".js_etfvolumn .pagination-box").html("");
            },
        });
    },
};
if ($(".js_etfvolumn").length > 0) {
    etfvolumn.init();
}
//ETF规模 END

/**
 * 交易型货币基金规模
 */
var tcuvolumn = {
    tcuvolumnUrl: sseQueryURL + "commonQuery.do?jsonCallBack=?",
    tcuvolumnParams: {
        isPagination: true,
        "pageHelp.pageSize": 25,
        "pageHelp.pageCount": 50,
        "pageHelp.pageNo": 1,
        "pageHelp.beginPage": 1,
        "pageHelp.cacheSize": 1,
        "pageHelp.endPage": 5,
        sqlId: "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_JYXJJ_SEARCH_L",
        STAT_DATE: "",
    },
    init: function () {
        this.loadEvents();
    },
    loadEvents: function () {
        var _this = this;
        //日期范围渲染
        $(".js_date input").attr("placeholder", "查询日期");
        laydate({
            elem: ".js_date input",
            theme: "#b50005",
            format: "yyyy-MM-dd",
            min: "1990-12-19",
            max: get_systemDate_global(),
            trigger: "click",
            btns: ["confirm"],
            done: function (value, date, endDate) {
                //选择时间触发查询
                setTimeout(function () {
                    _this.settcuvolumnParams();
                }, 500);
            },
        });
        //点击搜索、回车触发查询
        triggerSearch(_this.settcuvolumnParams);
        //初始加载
        _this.gettcuvolumnList(1);
    },
    settcuvolumnParams: function () {
        var _this = tcuvolumn;
        var STAT_DATE = $(".js_date input").val() ? $(".js_date input").val() : "";
        _this.tcuvolumnParams.STAT_DATE = STAT_DATE;
        
        _this.gettcuvolumnList(1);
    },
    gettcuvolumnList: function (pageIndex) {
        var _this = this;
        if (!paginationChange(_this.tcuvolumnParams, pageIndex)) { return } //触发分页改变分页参数
        var emptyTr = '<tr><td colspan="5">暂无数据</td></tr>';
        var tcuvolumnHtml = "<thead><tr>";
        tcuvolumnHtml += '<th class="text-nowrap">日期</th>';
        tcuvolumnHtml += '<th class="text-nowrap">基金代码</th>';
        // tcuvolumnHtml += '<th class="text-nowrap">基金简称</th>';
        tcuvolumnHtml += "<th>基金扩位简称</th>";
        tcuvolumnHtml += '<th class="text-right">总份额（万份）</th>';
        tcuvolumnHtml += "</tr></thead><tbody>";
        getJSONP({
            type: "post",
            dataType: "jsonp",
            url: _this.tcuvolumnUrl,
            data: _this.tcuvolumnParams,
            successCallback: function (data) {
                if (data && data.result && data.result.length > 0) {
                    if (!$(".js_date input").val()) {
                        $(".js_date input").val(data.result[0].STAT_DATE);
                    }
                    var dataList = data,
                        sec_codes = [];
                    $.each(data.result, function (k, v) {
                        sec_codes.push(v.SEC_CODE);
                    });
                    //处理基金扩位简称
                    getJSONP({
                        type: "post",
                        dataType: "jsonp",
                        url: sseQueryURL + "security/stock/queryExpandName.do?jsonCallBack=?",
                        data: {
                            secCodes: sec_codes.join(","),
                        },
                        successCallback: function (data) {
                            //数组转json对象
                            var expandData = {};
                            if (data && data.result && data.result.length > 0) {
                                $.each(data.result, function (k, v) {
                                    expandData[v[0]] = v[1];
                                });
                            }
                            $.each(dataList.result, function (k, v) {
                                tcuvolumnHtml += "<tr>";
                                tcuvolumnHtml +=
                                    '<td class="text-nowrap">' + v.STAT_DATE + "</a></td>"; //日期
                                tcuvolumnHtml +=
                                    '<td class="text-nowrap"><a target="_blank" href="/assortment/fund/list/tcurrencyfundinfo/basic/index.shtml?FUNDID=' +
                                    v.SEC_CODE +
                                    "&FULLNAME=" +
                                    v.SEC_NAME +
                                    '">' +
                                    v.SEC_CODE +
                                    "</a></td>"; //基金代码
                                // tcuvolumnHtml += '<td class="text-nowrap">' + v.SEC_NAME + '</a></td>';  //基金简称
                                tcuvolumnHtml +=
                                    '<td class="text-nowrap">' +
                                    (expandData && expandData[v.SEC_CODE] ?
                                        expandData[v.SEC_CODE] :
                                        "-") +
                                    "</td>"; //基金扩位简称
                                tcuvolumnHtml +=
                                    '<td class="text-right">' + v.TOT_VOL + "</td>"; //总份额（万份）
                                tcuvolumnHtml += "</tr>";
                            });
                            tcuvolumnHtml += "</tbody>";
                            //调用分页
                            Page.navigation(
                                ".js_tcuvolumn .pagination-box",
                                dataList.pageHelp.pageCount,
                                dataList.pageHelp.total,
                                dataList.pageHelp.pageNo,
                                dataList.pageHelp.pageSize,
                                 "tcuvolumn.gettcuvolumnList"
                            );
                            $(".js_tcuvolumn .table").html(tcuvolumnHtml);
                        },
                    });
                } else {
                    tcuvolumnHtml += emptyTr;
                    tcuvolumnHtml += "</tbody>";
                    $(".js_tcuvolumn .table").html(tcuvolumnHtml);
                    $(".js_tcuvolumn .pagination-box").html("");
                }
            },
            errCallback: function () {
                tcuvolumnHtml += emptyTr;
                tcuvolumnHtml += "</tbody>";
                $(".js_tcuvolumn .table").html(tcuvolumnHtml);
                $(".js_tcuvolumn .pagination-box").html("");
            },
        });
    },
};
if ($(".js_tcuvolumn").length > 0) {
    tcuvolumn.init();
}
//交易型货币基金规模 END

/**
 * 上市开放式基金（LOF）规模
 */
var lofvolumn = {
    lofvolumnUrl: sseQueryURL + "commonQuery.do?jsonCallBack=?",
    lofvolumnParams: {
        isPagination: true,
        "pageHelp.pageSize": 10000,
        sqlId: "COMMON_SSE_FUND_LOF_SCALE_CX_S",
        FILEDATE: "",
    },
    init: function () {
        this.loadEvents();
    },
    loadEvents: function () {
        var _this = this;
        //日期范围渲染
        $(".js_date input").attr("placeholder", "查询日期");
        laydate({
            elem: ".js_date input",
            theme: "#b50005",
            format: "yyyy-MM-dd",
            min: "1990-12-19",
            max: get_systemDate_global(),
            trigger: "click",
            btns: ["confirm"],
            done: function (value, date, endDate) {
                //选择时间触发查询
                setTimeout(function () {
                    _this.setlofvolumnParams();
                }, 500);
            },
        });
        //点击搜索、回车触发查询
        triggerSearch(_this.setlofvolumnParams);
        //初始加载
        _this.getlofvolumnList(1);
    },
    setlofvolumnParams: function () {
        var _this = lofvolumn;
        var FILEDATE = $(".js_date input").val() ? $(".js_date input").val() : "";
        _this.lofvolumnParams.FILEDATE = FILEDATE.replace(/\-/g, "");
        _this.getlofvolumnList(1);
    },
    getlofvolumnList: function () {
        var _this = this;
        var emptyTr = '<tr><td colspan="5">暂无数据</td></tr>';
        var lofvolumnHtml = "<thead><tr>";
        lofvolumnHtml += '<th class="text-nowrap">交易日期</th>';
        lofvolumnHtml += '<th class="text-nowrap">基金代码</th>';
        // lofvolumnHtml += '<th class="text-nowrap">基金简称</th>';
        lofvolumnHtml += "<th>基金扩位简称</th>";
        lofvolumnHtml += '<th class="text-right">基金规模（万份）</th>';
        lofvolumnHtml += "</tr></thead><tbody>";
        getJSONP({
            type: "post",
            dataType: "jsonp",
            url: _this.lofvolumnUrl,
            data: _this.lofvolumnParams,
            successCallback: function (data) {
                if (data && data.result && data.result.length > 0) {
                    if (!$(".js_date input").val()) {
                        $(".js_date input").val(turnDateAddLine(data.result[0].TRADE_DATE));
                    }
                    var dataList = data,
                        sec_codes = [];
                    $.each(data.result, function (k, v) {
                        sec_codes.push(v.FUND_CODE);
                    });
                    //处理基金扩位简称
                    getJSONP({
                        type: "post",
                        dataType: "jsonp",
                        url: sseQueryURL + "security/stock/queryExpandName.do?jsonCallBack=?",
                        data: {
                            secCodes: sec_codes.join(","),
                        },
                        successCallback: function (data) {
                            //数组转json对象
                            var expandData = {};
                            if (data && data.result && data.result.length > 0) {
                                $.each(data.result, function (k, v) {
                                    expandData[v[0]] = v[1];
                                });
                            }
                            $.each(dataList.result, function (k, v) {
                                lofvolumnHtml += "<tr>";
                                lofvolumnHtml +=
                                    '<td class="text-nowrap">' + v.TRADE_DATE + "</a></td>"; //日期
                                lofvolumnHtml +=
                                    '<td class="text-nowrap"><a target="_blank" href="/assortment/fund/list/lofinfo/basic/index.shtml?FUNDID=' +
                                    v.FUND_CODE +
                                    '">' +
                                    v.FUND_CODE +
                                    "</a></td>"; //基金代码
                                // lofvolumnHtml += '<td class="text-nowrap"><a target="_blank" href="/assortment/fund/list/lofinfo/basic/index.shtml?FUNDID=' + v.FUND_CODE + '">' + v.FUND_ABBR + '</a></td>';    //基金简称
                                lofvolumnHtml +=
                                    '<td class="text-nowrap">' +
                                    (expandData && expandData[v.FUND_CODE] ?
                                        expandData[v.FUND_CODE] :
                                        "-") +
                                    "</td>"; //基金扩位简称
                                lofvolumnHtml +=
                                    '<td class="text-right">' + v.INTERNAL_VOL + "</td>"; //总份额（万份）
                                lofvolumnHtml += "</tr>";
                            });
                            lofvolumnHtml += "</tbody>";
                            $(".js_lofvolumn .table").html(lofvolumnHtml);
                        },
                    });
                } else {
                    lofvolumnHtml += emptyTr;
                    lofvolumnHtml += "</tbody>";
                    $(".js_lofvolumn .table").html(lofvolumnHtml);
                }
            },
            errCallback: function () {
                lofvolumnHtml += emptyTr;
                lofvolumnHtml += "</tbody>";
                $(".js_lofvolumn .table").html(lofvolumnHtml);
            },
        });
    },
};
if ($(".js_lofvolumn").length > 0) {
    lofvolumn.init();
}
//上市开放式基金（LOF）规模 END

/**
 * 上市开放式基金（LOF）（新）
 */
var lofvolumn_new = {
    lofvolumnUrl: sseQueryURL + "commonQuery.do?jsonCallBack=?",
    lofvolumnParams: {
        isPagination: true,
        sqlId: "COMMON_SSE_SJ_JJSJ_JJGM_LOFGMTJ_L",
        PRODUCT_TYPE: "11,14,15",//基金产品类型
        SEARCH_DATE:"",//查询日期
        'type':'inParams',
        "pageHelp.pageSize": 25,
        "pageHelp.pageCount": 50,
        "pageHelp.pageNo": 1,
        "pageHelp.beginPage": 1,
        "pageHelp.cacheSize": 1,
        "pageHelp.endPage": 5,
    },
    init: function () {
        this.loadEvents();
    },
    loadEvents: function () {
        var _this = this;
        //日期范围渲染
        $(".js_date input").attr("placeholder", "查询日期");
        laydate({
            elem: ".js_date input",
            theme: "#b50005",
            format: "yyyy-MM-dd",
            min: "1990-12-19",
            max: get_systemDate_global(),
            trigger: "click",
            btns: ["confirm"],
            done: function (value, date, endDate) {
                //选择时间触发查询
                setTimeout(function () {
                    _this.setlofvolumnParams();
                }, 500);
            },
        });
        //点击搜索、回车触发查询
        triggerSearch(_this.setlofvolumnParams);
        //初始加载
        _this.getlofvolumnList(1);
    },
    setlofvolumnParams: function () {
        var _this = lofvolumn_new;
        var SEARCH_DATE = $(".js_date input").val() ? $(".js_date input").val() : "";
        _this.lofvolumnParams.SEARCH_DATE = SEARCH_DATE.replace(/\-/g, "");
        _this.getlofvolumnList(1);
    },
    getlofvolumnList: function (pageIndex) {
        var _this = this;
        if (!paginationChange(_this.lofvolumnParams, pageIndex)) { return } //触发分页改变分页参数
        var emptyTr = '<tr><td colspan="5">暂无数据</td></tr>';
        var lofvolumnHtml = "<thead><tr>";
        lofvolumnHtml += '<th class="text-nowrap">交易日期</th>';
        lofvolumnHtml += '<th class="text-nowrap">基金代码</th>';
        lofvolumnHtml += "<th>基金扩位简称</th>";
        lofvolumnHtml += '<th class="text-right">基金规模（万份）</th>';
        lofvolumnHtml += "</tr></thead><tbody>";
        getJSONP({
            type: "post",
            dataType: "jsonp",
            url: _this.lofvolumnUrl,
            data: _this.lofvolumnParams,
            successCallback: function (data) {
                if (data && data.result && data.result.length > 0) {
                    if (!$(".js_date input").val()) {
                        $(".js_date input").val(turnDateAddLine(data.result[0].TRADE_DATE));
                    }
                    var dataList = data,
                        sec_codes = [];
                    $.each(data.result, function (k, v) {
                        sec_codes.push(v.FUND_CODE);
                    });
                    //处理基金扩位简称
                    getJSONP({
                        type: "post",
                        dataType: "jsonp",
                        url: sseQueryURL + "security/stock/queryExpandName.do?jsonCallBack=?",
                        data: {
                            secCodes: sec_codes.join(","),
                        },
                        successCallback: function (Data) {
                            //数组转json对象
                            var expandData = {};
                            if (Data && Data.result && Data.result.length > 0) {
                                $.each(Data.result, function (k, v) {
                                    expandData[v[0]] = v[1];
                                });
                            }
                            $.each(dataList.result, function (k, v) {
                                lofvolumnHtml += "<tr>";
                                lofvolumnHtml +=
                                    '<td class="text-nowrap">' + v.TRADE_DATE + "</a></td>"; //日期
                                lofvolumnHtml +=
                                    '<td class="text-nowrap"><a target="_blank" href="/assortment/fund/list/lofinfo/basic/index.shtml?FUNDID=' +
                                    v.FUND_CODE +
                                    '">' +
                                    v.FUND_CODE +
                                    "</a></td>"; //基金代码
                                lofvolumnHtml +=
                                    '<td class="text-nowrap">' +
                                    (expandData && expandData[v.FUND_CODE] ?
                                        expandData[v.FUND_CODE] :
                                        "-") +
                                    "</td>"; //基金扩位简称
                                lofvolumnHtml +=
                                    '<td class="text-right">' + v.INTERNAL_VOL + "</td>"; //总份额（万份）
                                lofvolumnHtml += "</tr>";
                            });
                            lofvolumnHtml += "</tbody>";
                            //调用分页
                            Page.navigation(".js_lofvolumn_2022 .pagination-box", data.pageHelp.pageCount, data.pageHelp.total, data.pageHelp.pageNo, data.pageHelp.pageSize, "lofvolumn_new.getlofvolumnList");
                            $(".change-pageSize").hide();
                            $(".js_lofvolumn_2022 .table").html(lofvolumnHtml);
                        },
                    });
                } else {
                    lofvolumnHtml += emptyTr;
                    lofvolumnHtml += "</tbody>";
                    $(".js_lofvolumn_2022 .table").html(lofvolumnHtml);
                    $(".js_lofvolumn_2022 .pagination-box").html("");
                }
            },
            errCallback: function () {
                lofvolumnHtml += emptyTr;
                lofvolumnHtml += "</tbody>";
                $(".js_lofvolumn_2022 .table").html(lofvolumnHtml);
                $(".js_lofvolumn_2022 .pagination-box").html("");
            },
        });
    },
};
if ($(".js_lofvolumn_2022").length > 0) {
    lofvolumn_new.init();
}
//上市开放式基金（LOF）规模（新）end

/**
 * 分级LOF基金规模
 */
var fjlofvolumn = {
    todayDate: get_systemDate_global(),
    fjlofvolumnUrl: sseQueryURL + "commonQuery.do?jsonCallBack=?",
    fjlofvolumnParams: {
        isPagination: true,
        "pageHelp.pageSize": 25,
        "pageHelp.pageNo": 1,
        "pageHelp.beginPage": 1,
        "pageHelp.cacheSize": 1,
        "pageHelp.endPage": 1,
        sqlId: "COMMON_SSE_FUND_FJLOF_SCALE_CX_S",
        FILEDATE: "",
    },
    statisticsTypeOption: [{
            value: "1",
            name: "分级LOF场内规模统计",
        },
        {
            value: "2",
            name: "分拆合并统计",
        },
    ],
    init: function () {
        this.loadEvents();
    },
    loadEvents: function () {
        var _this = this;
        //日期渲染
        $(".js_date input").attr("placeholder", "查询日期");
        $(".js_fjlofvolumn").append(
            '<p class="remarks">注：以上份额数据为当日收盘清算后，次日开盘前份额数据</p>'
        );
        laydate({
            elem: ".js_date input",
            theme: "#b50005",
            format: "yyyy-MM-dd",
            trigger: "click",
            min: "1990-12-19",
            max: _this.todayDate,
            btns: ["confirm"],
            done: function (value, date, endDate) {
                //选择时间触发查询
                setTimeout(function () {
                    _this.setfjlofvolumnParams();
                }, 500);
            },
        });
        bootstrapSelect({
            method: function () {
                //统计类型数据渲染
                $(".js_statisticsType .selectpicker")
                    .html(addSelectOption(_this.statisticsTypeOption))
                    .selectpicker("refresh")
                    .selectpicker("render");
                //下拉框改变触发查询
                $(".js_statisticsType .selectpicker").on(
                    "changed.bs.select",
                    function (e) {
                        _this.setfjlofvolumnParams();
                    }
                );
            },
        });
        //点击搜索、回车触发查询
        triggerSearch(_this.setfjlofvolumnParams);
        //初始加载
        _this.fjlofvolumnParams.FILEDATE = _this.todayDate.replace(/\-/g, "");
        $(".js_date input").val(_this.todayDate);
        _this.getfjlofvolumnList(1);
    },
    setfjlofvolumnParams: function () {
        var _this = fjlofvolumn;
        var statisticsType = $(".js_statisticsType .selectpicker").val();
        var FILEDATE = $(".js_date input").val() ? $(".js_date input").val() : "";
        if (statisticsType == "1") {
            _this.fjlofvolumnParams = {
                isPagination: true,
                "pageHelp.pageSize": 25,
                "pageHelp.pageNo": 1,
                "pageHelp.beginPage": 1,
                "pageHelp.cacheSize": 1,
                "pageHelp.endPage": 1,
                sqlId: "COMMON_SSE_FUND_FJLOF_SCALE_CX_S",
                FILEDATE: "",
            };
            _this.fjlofvolumnParams.FILEDATE = FILEDATE.replace(/\-/g, "");
            $(".js_fjlofvolumn .remarks").remove();
            $(".js_fjlofvolumn").append(
                '<p class="remarks">注：以上份额数据为当日收盘清算后，次日开盘前份额数据</p>'
            );
            _this.getfjlofvolumnList(1);
        } else if (statisticsType == "2") {
            _this.fjlofvolumnParams = {
                isPagination: true,
                "pageHelp.pageSize": 25,
                "pageHelp.pageNo": 1,
                "pageHelp.beginPage": 1,
                "pageHelp.cacheSize": 1,
                "pageHelp.endPage": 1,
                sqlId: "COMMON_SSE_FUND_FJLOF_SM_CX_S",
                FILEDATE: "",
            };
            _this.fjlofvolumnParams.FILEDATE = FILEDATE.replace(/\-/g, "");
            $(".js_fjlofvolumn .remarks").remove();
            _this.getfjlofvolumnFchbList(1);
        }
    },
    getfjlofvolumnList: function (pageIndex) {
        var _this = this;
        if (!paginationChange(_this.fjlofvolumnParams, pageIndex)) return; //触发分页改变分页参数
        var emptyTr = '<tr><td colspan="4">暂无数据</td></tr>';
        var fjlofvolumnHtml = "<thead><tr>";
        fjlofvolumnHtml += '<th class="text-nowrap">交易日期</th>';
        fjlofvolumnHtml += '<th class="text-nowrap">基金代码</th>';
        fjlofvolumnHtml += '<th class="text-nowrap">基金简称</th>';
        fjlofvolumnHtml += '<th class="text-right">基金规模（万份）</th>';
        fjlofvolumnHtml += "</tr></thead><tbody>";
        getJSONP({
            type: "post",
            dataType: "jsonp",
            url: _this.fjlofvolumnUrl,
            data: _this.fjlofvolumnParams,
            successCallback: function (data) {
                if (data && data.result && data.result.length > 0) {
                    $.each(data.result, function (k, v) {
                        fjlofvolumnHtml += "<tr>";
                        fjlofvolumnHtml +=
                            '<td class="text-nowrap">' + v.TRADE_DATE + "</td>";
                        fjlofvolumnHtml +=
                            '<td class="text-nowrap"><a href="/assortment/fund/fjlof/home/index_detal.shtml?FUNDID=' +
                            v.FUND_CODE +
                            "&ABBR=" +
                            v.FUND_ABBR +
                            '" target="_blank">' +
                            v.FUND_CODE +
                            "</a></td>";
                        fjlofvolumnHtml +=
                            '<td class="text-nowrap"><a href="/assortment/fund/fjlof/home/index_detal.shtml?FUNDID=' +
                            v.FUND_CODE +
                            "&ABBR=" +
                            v.FUND_ABBR +
                            '" target="_blank">' +
                            v.FUND_ABBR +
                            "</a></td>";
                        fjlofvolumnHtml +=
                            '<td class="text-right">' + v.INTERNAL_VOL + "</td>";
                        fjlofvolumnHtml += "</tr>";
                    });
                    //调用分页
                    Page.navigation(
                        ".js_fjlofvolumn .pagination-box",
                        data.pageHelp.pageCount,
                        data.pageHelp.total,
                        data.pageHelp.pageNo,
                        data.pageHelp.pageSize,
                        "fjlofvolumn.getfjlofvolumnList"
                    );
                    $(".change-pageSize").hide();
                    fjlofvolumnHtml += "</tbody>";
                    $(".js_fjlofvolumn .table").html(fjlofvolumnHtml);
                } else {
                    fjlofvolumnHtml += emptyTr;
                    fjlofvolumnHtml += "</tbody>";
                    $(".js_fjlofvolumn .table").html(fjlofvolumnHtml);
                    $(".js_fjlofvolumn .pagination-box").html("");
                }
            },
            errCallback: function () {
                fjlofvolumnHtml += emptyTr;
                fjlofvolumnHtml += "</tbody>";
                $(".js_fjlofvolumn .table").html(fjlofvolumnHtml);
                $(".js_fjlofvolumn .pagination-box").html("");
            },
        });
    },
    getfjlofvolumnFchbList: function (pageIndex) {
        var _this = this;
        if (!paginationChange(_this.fjlofvolumnParams, pageIndex)) return; //触发分页改变分页参数
        var emptyTr = '<tr><td colspan="5">暂无数据</td></tr>';
        var fjlofvolumnFchbHtml = "<thead><tr>";
        fjlofvolumnFchbHtml += '<th class="text-nowrap">交易日期</th>';
        fjlofvolumnFchbHtml += '<th class="text-nowrap">基金代码</th>';
        fjlofvolumnFchbHtml += '<th class="text-nowrap">基金简称</th>';
        fjlofvolumnFchbHtml += '<th class="text-right">分拆总份额（万份）</th>';
        fjlofvolumnFchbHtml += '<th class="text-right">合并总份额（万份）</th>';
        fjlofvolumnFchbHtml += "</tr></thead><tbody>";
        getJSONP({
            type: "post",
            dataType: "jsonp",
            url: _this.fjlofvolumnUrl,
            data: _this.fjlofvolumnParams,
            successCallback: function (data) {
                if (data && data.result && data.result.length > 0) {
                    $.each(data.result, function (k, v) {
                        fjlofvolumnFchbHtml += "<tr>";
                        fjlofvolumnFchbHtml +=
                            '<td class="text-nowrap">' + v.TRADE_DATE + "</td>";
                        fjlofvolumnFchbHtml +=
                            '<td class="text-nowrap"><a href="/assortment/fund/fjlof/home/index_detal.shtml?FUNDID=' +
                            v.FUND_CODE +
                            "&ABBR=" +
                            v.FUND_ABBR +
                            '" target="_blank">' +
                            v.FUND_CODE +
                            "</a></td>";
                        fjlofvolumnFchbHtml +=
                            '<td class="text-nowrap"><a href="/assortment/fund/fjlof/home/index_detal.shtml?FUNDID=' +
                            v.FUND_CODE +
                            "&ABBR=" +
                            v.FUND_ABBR +
                            '" target="_blank">' +
                            v.FUND_ABBR +
                            "</a></td>";
                        fjlofvolumnFchbHtml +=
                            '<td class="text-right">' + v.SPLIT_VOL + "</td>";
                        fjlofvolumnFchbHtml +=
                            '<td class="text-right">' + v.MERGE_VOL + "</td>";
                        fjlofvolumnFchbHtml += "</tr>";
                    });
                    //调用分页
                    Page.navigation(
                        ".js_fjlofvolumn .pagination-box",
                        data.pageHelp.pageCount,
                        data.pageHelp.total,
                        data.pageHelp.pageNo,
                        data.pageHelp.pageSize,
                        "fjlofvolumn.getfjlofvolumnFchbList"
                    );
                    $(".change-pageSize").hide();
                    fjlofvolumnFchbHtml += "</tbody>";
                    $(".js_fjlofvolumn .table").html(fjlofvolumnFchbHtml);
                } else {
                    fjlofvolumnFchbHtml += emptyTr;
                    fjlofvolumnFchbHtml += "</tbody>";
                    $(".js_fjlofvolumn .table").html(fjlofvolumnFchbHtml);
                    $(".js_fjlofvolumn .pagination-box").html("");
                }
            },
            errCallback: function () {
                fjlofvolumnFchbHtml += emptyTr;
                fjlofvolumnFchbHtml += "</tbody>";
                $(".js_fjlofvolumn .table").html(fjlofvolumnFchbHtml);
                $(".js_fjlofvolumn .pagination-box").html("");
            },
        });
    },
};
if ($(".js_fjlofvolumn").length > 0) {
    fjlofvolumn.init();
}
//分级LOF基金规模
